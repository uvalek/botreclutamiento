"""Conector al CRM de Adlek: firma, cliente HTTP y webhook → respuesta."""

import asyncio
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import chatwoot, crm, processor, rate_limit
from app import store as store_mod
from app.config import get_settings
from app.store import Store

CONV = "11111111-1111-1111-1111-111111111111"
CONTACT = "22222222-2222-2222-2222-222222222222"
PHONE = "+5212221234567"


def sign(secret: str, body: bytes, t: int | None = None) -> str:
    t = t or int(time.time())
    v1 = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


class FakeCRM:
    """Imita la API del CRM y guarda lo que el bot le pidió."""

    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.tags: list[str] = []
        self.assigned: str | None = None
        self.messages: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path.replace("/api/v1", "")
        self.requests.append((request.method, path, body))
        assert request.headers["authorization"] == "Bearer wacrm_live_test"
        if request.method == "GET" and path == f"/contacts/{CONTACT}":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": CONTACT,
                        "phone": PHONE,
                        "tags": [{"name": t} for t in self.tags],
                    }
                },
            )
        if request.method == "PATCH" and path == f"/contacts/{CONTACT}":
            self.tags = list(body["tags"])
            return httpx.Response(200, json={"data": {"id": CONTACT}})
        if request.method == "GET" and path == f"/conversations/{CONV}":
            return httpx.Response(
                200, json={"data": {"id": CONV, "assigned_agent_id": self.assigned}}
            )
        if request.method == "GET" and path == f"/conversations/{CONV}/messages":
            return httpx.Response(200, json={"data": self.messages, "meta": {"next_cursor": None}})
        if request.method == "POST" and path == "/messages":
            return httpx.Response(201, json={"data": {"message_id": "m1"}})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def sent(self):
        return [b for (m, p, b) in self.requests if m == "POST" and p == "/messages"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "crm_api_key", "wacrm_live_test")
    monkeypatch.setattr(s, "crm_webhook_secret", "whsec_test")
    monkeypatch.setattr(s, "send_delay_seconds", 0)
    monkeypatch.setattr(s, "buffer_window_seconds", 0)
    monkeypatch.setattr(s, "buffer_button_seconds", 0)
    monkeypatch.setattr(s, "openai_api_key", "")
    monkeypatch.setattr(s, "supabase_url", "")
    monkeypatch.setattr(s, "cal_api_key", "")
    rate_limit._BUCKETS.clear()
    st = Store(str(tmp_path / "t.db"))
    store_mod.set_store(st)
    fake = FakeCRM()
    client = crm.CrmClient(
        "https://app.adlek.com.mx/api/v1",
        "wacrm_live_test",
        store=st,
        transport=httpx.MockTransport(fake.handler),
    )
    chatwoot.set_client(client)
    yield fake, client, st
    store_mod.set_store(None)
    chatwoot.set_client(None)


def payload(text="hola", wamid="wamid.A", content_type="text"):
    return {
        "id": "delivery-" + wamid,
        "event": "message.received",
        "occurred_at": "2026-11-15T20:00:00Z",
        "account_id": "acc",
        "data": {
            "conversation_id": CONV,
            "contact_id": CONTACT,
            "whatsapp_message_id": wamid,
            "content_type": content_type,
            "text": text,
        },
    }


async def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


# --- firma ------------------------------------------------------------------


def test_signature_valid_and_rejections():
    body = b'{"a":1}'
    assert crm.valid_signature("s", body, sign("s", body), 300)
    assert not crm.valid_signature("otro", body, sign("s", body), 300)
    assert not crm.valid_signature("s", b"cambiado", sign("s", body), 300)
    assert not crm.valid_signature("s", body, sign("s", body, int(time.time()) - 3600), 300)
    assert not crm.valid_signature("s", body, None, 300)
    assert not crm.valid_signature("s", body, "basura", 300)


def test_webhook_rejects_bad_signature(env):
    from app.main import app

    body = json.dumps(payload()).encode()
    with TestClient(app) as tc:
        r = tc.post("/webhooks/crm", content=body, headers={"x-wacrm-signature": sign("mal", body)})
        assert r.status_code == 403
        r = tc.post(
            "/webhooks/crm", content=body, headers={"x-wacrm-signature": sign("whsec_test", body)}
        )
        assert r.status_code == 200


# --- flujo completo -----------------------------------------------------------


async def test_incoming_message_gets_a_reply_by_phone(env):
    fake, _, st = env
    assert await processor.accept_crm(payload("hola")) == "queued"
    assert await wait_for(lambda: fake.sent())
    first = fake.sent()[0]
    assert first["to"] == PHONE
    assert first["type"] in ("text", "interactive")
    # El UUID quedó ligado a un entero del bot con su teléfono.
    conv = st.crm_conversation(st.crm_conversation_int(CONV))
    assert conv == {"uuid": CONV, "contact_id": CONTACT, "phone": PHONE}


async def test_duplicate_message_is_ignored(env):
    assert await processor.accept_crm(payload("hola", wamid="wamid.X")) == "queued"
    assert await processor.accept_crm(payload("hola", wamid="wamid.X")) == "duplicate"


async def test_reactions_and_other_events_are_ignored(env):
    assert (
        await processor.accept_crm({"event": "conversation.created", "data": {}}) == "ignored_event"
    )
    assert await processor.accept_crm(payload("👍", content_type="reaction")) == "reaction"


async def test_bot_stays_quiet_when_a_human_is_assigned(env):
    fake, _, _ = env
    fake.assigned = "agent-1"
    assert await processor.accept_crm(payload("hola", wamid="wamid.H")) == "queued"
    await asyncio.sleep(0.3)
    assert fake.sent() == []


async def test_bot_stays_quiet_with_handoff_tag(env):
    fake, _, _ = env
    fake.tags = ["requiere-humano"]
    assert await processor.accept_crm(payload("hola", wamid="wamid.T")) == "queued"
    await asyncio.sleep(0.3)
    assert fake.sent() == []


# --- cliente ------------------------------------------------------------------


async def test_options_become_buttons_or_list(env):
    fake, client, st = env
    conv = st.crm_conversation_int(CONV, CONTACT, PHONE)
    await client.send_options(conv, "¿Turno?", ["Matutino", "Vespertino"])
    await client.send_options(conv, "¿Municipio?", ["A", "B", "C", "D"])
    btn, lst = fake.sent()
    assert btn["type"] == "interactive"
    assert btn["interactive_payload"]["kind"] == "buttons"
    assert [b["title"] for b in btn["interactive_payload"]["buttons"]] == ["Matutino", "Vespertino"]
    assert lst["interactive_payload"]["kind"] == "list"
    assert len(lst["interactive_payload"]["sections"][0]["rows"]) == 4


async def test_options_too_long_fall_back_to_numbered_text(env):
    fake, client, st = env
    conv = st.crm_conversation_int(CONV, CONTACT, PHONE)
    await client.send_options(conv, "Elige", ["Una opción demasiado larga para WhatsApp"])
    (msg,) = fake.sent()
    assert msg["type"] == "text"
    assert "1. Una opción" in msg["text"]


async def test_labels_and_handoff_use_contact_tags(env):
    fake, client, st = env
    conv = st.crm_conversation_int(CONV, CONTACT, PHONE)
    fake.tags = ["vip"]
    assert await client.get_labels(conv) == ["vip"]
    await client.toggle_status(conv, "open")
    assert fake.tags == ["vip", "requiere-humano"]
    assert await client.get_status(conv) == "open"
    await client.toggle_status(conv, "pending")
    assert fake.tags == ["vip"]
    assert await client.get_status(conv) == "pending"


async def test_audio_uses_media_url_from_messages(env):
    fake, client, _ = env
    fake.messages = [{"whatsapp_message_id": "wamid.V", "media_url": "https://x.supabase.co/a.ogg"}]
    assert await client.get_message_media_url(CONV, "wamid.V") == "https://x.supabase.co/a.ogg"
    assert await client.get_message_media_url(CONV, "otro") is None


def test_to_e164():
    assert crm.to_e164("5212221234567") == "+5212221234567"
    assert crm.to_e164("+52 222 123 4567") == "+522221234567"
    assert crm.to_e164("") is None
