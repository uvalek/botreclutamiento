"""Webhook de Chatwoot → procesador → acciones, con un Chatwoot falso."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app import chatwoot, processor, rate_limit
from app import store as store_mod
from app import vacante as V
from app.config import get_settings
from app.store import Store


class FakeChatwoot:
    def __init__(self):
        self.calls = []
        self.labels = {}
        self.status = "pending"
        self.inbox_of = {}
        self.dry_run = False

    async def send_text(self, conv, content):
        self.calls.append(("text", conv, content))

    async def send_options(self, conv, content, options):
        self.calls.append(("options", conv, content, list(options)))

    async def send_note(self, conv, content):
        self.calls.append(("note", conv, content))

    async def get_labels(self, conv):
        return list(self.labels.get(conv, []))

    async def set_labels(self, conv, labels):
        self.labels[conv] = list(labels)
        self.calls.append(("labels", conv, list(labels)))

    async def set_attributes(self, conv, values, merge=True):
        self.calls.append(("attrs", conv, dict(values), merge))

    async def toggle_status(self, conv, status):
        self.status = status
        self.calls.append(("status", conv, status))

    async def get_status(self, conv):
        return self.status

    async def get_conversation(self, conv):
        return {"id": conv, "status": self.status, "inbox_id": self.inbox_of.get(conv, 3)}

    async def close(self):
        pass

    def sent(self):
        return [c for c in self.calls if c[0] in ("text", "options")]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "send_delay_seconds", 0)
    monkeypatch.setattr(s, "webhook_key", "secreto")
    monkeypatch.setattr(s, "buffer_window_seconds", 0)
    monkeypatch.setattr(s, "buffer_button_seconds", 0)
    monkeypatch.setattr(s, "openai_api_key", "")
    monkeypatch.setattr(s, "supabase_url", "")
    monkeypatch.setattr(s, "cal_api_key", "")
    rate_limit._BUCKETS.clear()
    st = Store(str(tmp_path / "t.db"))
    store_mod.set_store(st)
    fake = FakeChatwoot()
    chatwoot.set_client(fake)
    yield fake
    store_mod.set_store(None)
    chatwoot.set_client(None)


_MSG_ID = [1000]


def event(text="hola", conv=7, status="pending", **over):
    _MSG_ID[0] += 1
    payload = {
        "event": "message_created",
        "id": _MSG_ID[0],
        "content": text,
        "message_type": "incoming",
        "private": False,
        "inbox": {"id": 3},
        "account": {"id": 1},
        "conversation": {"id": conv, "status": status, "inbox_id": 3},
        "sender": {"id": 55, "type": "contact"},
    }
    payload.update(over)
    return payload


async def send(payload):
    result = processor.accept(payload)
    await asyncio.sleep(0)
    for _ in range(50):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            break
        await asyncio.gather(*pending)
    return result


async def test_mensaje_nuevo_responde_bienvenida(env):
    assert await send(event("Hola")) == "queued"
    sent = env.sent()
    assert "demostración de Adlek" in sent[0][2]
    assert sent[1][3] == V.OPC_BIENVENIDA
    assert env.labels[7] == [V.ETIQUETA_RECLUTAMIENTO]


async def test_filtros(env):
    assert await send(event(message_type="outgoing")) == "not_incoming"
    assert await send(event(private=True)) == "private"
    assert await send(event(inbox={"id": 2})) == "other_inbox"
    assert await send(event(event="conversation_updated")) == "ignored_event"
    assert env.calls == []


async def test_duplicado_se_ignora(env):
    p = event("Hola")
    assert await send(p) == "queued"
    n = len(env.calls)
    assert await send(p) == "duplicate"
    assert len(env.calls) == n


async def test_conversacion_con_humano_no_responde(env):
    assert await send(event("Hola", status="open")) == "queued"
    assert env.calls == []


async def test_reiniciar_funciona_con_humano(env):
    await send(event("Hola"))
    await send(event("1"))
    await send(event("asesor"))
    assert ("status", 7, "open") in env.calls
    env.calls.clear()
    await send(event("Hola", status="open"))
    assert env.calls == []  # bot en silencio
    await send(event("reiniciar", status="open"))
    assert ("status", 7, "pending") in env.calls
    assert any("demostración de Adlek" in c[2] for c in env.sent())


async def test_etiquetas_de_rh_se_conservan(env):
    env.labels[7] = ["vip"]
    await send(event("Hola"))
    assert env.labels[7] == ["vip", V.ETIQUETA_RECLUTAMIENTO]
    await send(event("reiniciar"))
    assert env.labels[7] == ["vip", V.ETIQUETA_RECLUTAMIENTO]


async def test_foto_sin_texto(env):
    await send(event("Hola"))
    await send(event("1"))
    env.calls.clear()
    await send(event("", attachments=[{"file_type": "image", "data_url": "x"}]))
    assert V.SOLO_TEXTO in env.sent()[0][2]


async def test_recordatorio_por_temporizador(env):
    for m in ["Hola", "1", "Juan Pérez", "25–35", "Apizaco", "Matutino", "Sí, todos",
              "Más de 6 meses", "1", "Sí, confirmar"]:
        await send(event(m))
    st = store_mod.get_store()
    jobs = st.pending_jobs(7)
    assert [j.kind for j in jobs] == ["recordatorio"]
    env.calls.clear()
    for job in st.take_due(now=time.time() + 10_000):
        await processor.process_job(job)
    assert "Responde **1**" in env.sent()[0][2]
    await send(event("1"))
    assert V.ETIQUETA_CONFIRMO in env.labels[7]


async def test_recordatorio_no_sale_si_lo_atiende_humano(env):
    for m in ["Hola", "1", "Juan Pérez", "25–35", "Apizaco", "Matutino", "Sí, todos",
              "Más de 6 meses", "1", "Sí, confirmar"]:
        await send(event(m))
    env.status = "open"
    env.calls.clear()
    st = store_mod.get_store()
    for job in st.take_due(now=time.time() + 10_000):
        await processor.process_job(job)
    assert env.sent() == []


async def test_seguimiento_se_reprograma_con_cada_pregunta(env):
    await send(event("Hola"))
    await send(event("1"))
    st = store_mod.get_store()
    assert [(j.kind, j.state_at_schedule) for j in st.pending_jobs(7)] == [("seguimiento", "NOMBRE")]
    await send(event("Juan Pérez"))
    assert [(j.kind, j.state_at_schedule) for j in st.pending_jobs(7)] == [("seguimiento", "EDAD")]
    env.calls.clear()
    for job in st.take_due(now=time.time() + 10_000):
        await processor.process_job(job)
    assert "¿Sigues ahí, Juan?" in env.sent()[0][2]
    assert V.ETIQUETA_ABANDONO in env.labels[7]


def test_webhook_exige_llave():
    client = TestClient(processor_app())
    assert client.post("/webhooks/chatwoot", json={}).status_code == 403
    assert client.post("/webhooks/chatwoot?key=mal", json={}).status_code == 403
    r = client.post("/webhooks/chatwoot?key=secreto", json={"event": "conversation_created"})
    assert r.status_code == 200 and r.json()["status"] == "ignored_event"
    assert client.get("/health").json() == {"status": "ok"}


def processor_app():
    from app.main import app

    return app


async def test_conversacion_de_otra_bandeja_se_ignora(env):
    # Payload falsificado: dice bandeja 3 pero la conversación real es de la 2.
    env.inbox_of[99] = 2
    assert await send(event("Hola", conv=99)) == "queued"
    assert env.calls == []


def test_webhook_verifica_firma(monkeypatch):
    import hashlib
    import hmac as _hmac
    import json as _json

    s = get_settings()
    monkeypatch.setattr(s, "webhook_key", "")
    monkeypatch.setattr(s, "chatwoot_webhook_secret", "firma-secreta")
    client = TestClient(processor_app())
    body = _json.dumps({"event": "conversation_created"}).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + _hmac.new(b"firma-secreta", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json"}
    assert client.post("/webhooks/chatwoot", content=body, headers=headers).status_code == 403
    bad = dict(headers, **{"X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": "sha256=00"})
    assert client.post("/webhooks/chatwoot", content=body, headers=bad).status_code == 403
    old_ts = str(int(time.time()) - 3600)
    old_sig = "sha256=" + _hmac.new(b"firma-secreta", old_ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    old = dict(headers, **{"X-Chatwoot-Timestamp": old_ts, "X-Chatwoot-Signature": old_sig})
    assert client.post("/webhooks/chatwoot", content=body, headers=old).status_code == 403
    ok = dict(headers, **{"X-Chatwoot-Timestamp": ts, "X-Chatwoot-Signature": sig})
    r = client.post("/webhooks/chatwoot", content=body, headers=ok)
    assert r.status_code == 200 and r.json()["status"] == "ignored_event"
