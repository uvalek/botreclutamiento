"""Cliente del CRM de Adlek (app.adlek.com.mx), con la misma forma que
`ChatwootClient` para que el resto del bot no note la diferencia.

Cómo se traduce cada cosa:
- Conversación: el CRM usa UUID; el bot, enteros. `Store.crm_conversation_int`
  le da a cada UUID un entero fijo y guarda su contacto y teléfono.
- Mandar texto / botones / lista: POST /messages (`to` = teléfono con +).
- Etiquetas: son las etiquetas del CONTACTO en el CRM (GET/PATCH /contacts/{id}).
- Estado: el bot contesta ("pending") salvo que alguien del equipo se haya
  asignado la conversación o el contacto tenga la etiqueta de humano
  (`CRM_HANDOFF_TAG`). Handoff = poner esa etiqueta; SetPending = quitarla.
- Notas privadas y atributos: la API del CRM todavía no los tiene. Se anotan
  en el log y se sigue (el bot nunca se queda mudo por esto).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx
import structlog

from app.chatwoot import fits_interactive, numbered_fallback
from app.store import Store, get_store

log = structlog.get_logger(__name__)

_MAX_BUTTONS = 3


def message_int_id(key: str) -> int:
    """Entero estable (63 bits) a partir del id del mensaje de WhatsApp."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)


def valid_signature(secret: str, body: bytes, header: str | None, max_age: int) -> bool:
    """Firma del CRM: `X-Wacrm-Signature: t=<seg>,v1=<hex>`,
    v1 = HMAC_SHA256(secret, f"{t}.{body}")."""
    if not header:
        return False
    parts: dict[str, str] = {}
    for piece in header.split(","):
        if "=" in piece:
            k, v = piece.split("=", 1)
            parts[k.strip()] = v.strip()
    t, v1 = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or not v1:
        return False
    if abs(time.time() - int(t)) > max_age:
        return False
    expected = hmac.new(secret.encode(), t.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


def to_e164(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"+{digits}" if digits else None


class CrmClient:
    """Mismos métodos que ChatwootClient, hablando con la API del CRM."""

    channel = "crm"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        handoff_tag: str = "requiere-humano",
        store: Store | None = None,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.api_key = api_key
        self.dry_run = not api_key
        self.handoff_tag = handoff_tag
        self._store = store
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    # --- infraestructura ----------------------------------------------------

    @property
    def store(self) -> Store:
        return self._store or get_store()

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base,
                timeout=self._timeout,
                transport=self._transport,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        if self.dry_run:
            log.info("crm_dry_run", method=method, path=path, body=json)
            return None
        try:
            r = await self._client().request(method, path, json=json)
        except httpx.HTTPError as e:
            log.warning("crm_http_error", method=method, path=path, error=str(e)[:160])
            return None
        if r.status_code >= 400:
            log.warning(
                "crm_api_error",
                method=method,
                path=path,
                status=r.status_code,
                body=r.text[:300],
            )
            return None
        try:
            data = r.json()
        except ValueError:
            return None
        return data.get("data", data) if isinstance(data, dict) else data

    def _conv(self, conversation_id: int) -> dict | None:
        conv = self.store.crm_conversation(conversation_id)
        if conv is None:
            log.warning("crm_unknown_conversation", conversation_id=conversation_id)
        return conv

    # --- lecturas que usa el webhook ----------------------------------------

    async def get_contact(self, contact_id: str) -> dict[str, Any] | None:
        data = await self._request("GET", f"/contacts/{contact_id}")
        return data if isinstance(data, dict) else None

    async def get_message_media_url(
        self, conversation_uuid: str, whatsapp_message_id: str
    ) -> str | None:
        """URL del audio de un mensaje (el aviso del CRM no la trae)."""
        data = await self._request("GET", f"/conversations/{conversation_uuid}/messages")
        rows = data if isinstance(data, list) else []
        for m in rows:
            if isinstance(m, dict) and m.get("whatsapp_message_id") == whatsapp_message_id:
                url = m.get("media_url")
                return url if isinstance(url, str) and url.startswith("http") else None
        return None

    # --- mensajes -------------------------------------------------------------

    async def _send(self, conversation_id: int, body: dict[str, Any]) -> Any:
        conv = self._conv(conversation_id)
        to = to_e164(conv.get("phone")) if conv else None
        if not to:
            log.warning("crm_send_no_phone", conversation_id=conversation_id)
            return None
        return await self._request("POST", "/messages", {"to": to, **body})

    async def send_text(self, conversation_id: int, content: str) -> Any:
        return await self._send(conversation_id, {"type": "text", "text": content})

    async def send_options(self, conversation_id: int, content: str, options: list[str]) -> Any:
        """Botones (≤3) o lista (4–10). El CRM devuelve el título al tocarlo,
        así que el id y el título son el mismo texto (igual que en Chatwoot)."""
        if not fits_interactive(options):
            log.warning("interactive_fallback_text", options=options)
            return await self.send_text(conversation_id, numbered_fallback(content, options))
        if len(options) <= _MAX_BUTTONS:
            payload: dict[str, Any] = {
                "kind": "buttons",
                "body": content,
                "buttons": [{"id": o, "title": o} for o in options],
            }
        else:
            payload = {
                "kind": "list",
                "body": content,
                "button_label": "Ver opciones",
                "sections": [{"rows": [{"id": o, "title": o} for o in options]}],
            }
        return await self._send(
            conversation_id,
            {"type": "interactive", "text": content, "interactive_payload": payload},
        )

    async def send_note(self, conversation_id: int, content: str) -> Any:
        # La API pública del CRM no tiene notas privadas todavía.
        log.info("crm_note_not_supported", conversation_id=conversation_id, note=content[:500])
        return None

    # --- etiquetas y atributos ----------------------------------------------

    async def get_labels(self, conversation_id: int) -> list[str] | None:
        if self.dry_run:
            return []
        conv = self._conv(conversation_id)
        if not conv or not conv.get("contact_id"):
            return None
        contact = await self.get_contact(conv["contact_id"])
        if contact is None:
            return None
        return [
            t["name"] for t in contact.get("tags") or [] if isinstance(t, dict) and t.get("name")
        ]

    async def set_labels(self, conversation_id: int, labels: list[str]) -> Any:
        conv = self._conv(conversation_id)
        if not conv or not conv.get("contact_id"):
            return None
        # Igual que Chatwoot: se manda la lista COMPLETA (reemplaza).
        return await self._request("PATCH", f"/contacts/{conv['contact_id']}", {"tags": labels})

    async def set_attributes(
        self, conversation_id: int, values: dict[str, str], *, merge: bool = True
    ) -> Any:
        log.info("crm_attributes_not_supported", conversation_id=conversation_id, values=values)
        return None

    # --- estado -----------------------------------------------------------------

    async def _set_handoff(self, conversation_id: int, on: bool) -> Any:
        current = await self.get_labels(conversation_id)
        if current is None:
            return None
        has = self.handoff_tag in current
        if on and not has:
            return await self.set_labels(conversation_id, [*current, self.handoff_tag])
        if not on and has:
            return await self.set_labels(
                conversation_id, [t for t in current if t != self.handoff_tag]
            )
        return None

    async def toggle_status(self, conversation_id: int, status: str) -> Any:
        if status == "open":
            return await self._set_handoff(conversation_id, True)
        if status == "pending":
            return await self._set_handoff(conversation_id, False)
        return None

    async def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        conv = self._conv(conversation_id)
        if conv is None:
            return None
        if self.dry_run:
            return {"id": conv["uuid"], "status": "open", "assigned_agent_id": None}
        data = await self._request("GET", f"/conversations/{conv['uuid']}")
        return data if isinstance(data, dict) else None

    async def get_status(self, conversation_id: int) -> str | None:
        """ "pending" = contesta el bot; "open" = lo atiende una persona."""
        if self.dry_run:
            return "pending"
        data = await self.get_conversation(conversation_id)
        if data is None:
            return None
        if data.get("assigned_agent_id"):
            return "open"
        labels = await self.get_labels(conversation_id)
        if labels and self.handoff_tag in labels:
            return "open"
        return "pending"
