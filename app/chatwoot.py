"""Cliente de la API de Chatwoot (solo rutas permitidas al token de Agent Bot).

Rutas verificadas en Chatwoot v4.17.0 (AccessTokenAuthHelper::BOT_ACCESSIBLE_ENDPOINTS):
mensajes (create), etiquetas de conversación (index/create), atributos de
conversación (custom_attributes), toggle_status y show.

Sin token (CHATWOOT_BOT_TOKEN vacío) funciona en modo de prueba: registra en
el log lo que haría, sin llamar a Chatwoot.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

# Límites de WhatsApp para mensajes interactivos.
_MAX_BUTTONS = 3
_BUTTON_TITLE_MAX = 20
_MAX_ROWS = 10
_ROW_TITLE_MAX = 24


def fits_interactive(options: list[str]) -> bool:
    """True si WhatsApp aceptará estas opciones como botones o lista."""
    if not options or len(options) > _MAX_ROWS:
        return False
    limit = _BUTTON_TITLE_MAX if len(options) <= _MAX_BUTTONS else _ROW_TITLE_MAX
    return all(0 < len(o) <= limit for o in options)


def numbered_fallback(text: str, options: list[str]) -> str:
    lines = [text, ""] + [f"{i}. {o}" for i, o in enumerate(options, 1)]
    lines += ["", "Responde con el número de tu opción."]
    return "\n".join(lines)


class ChatwootClient:
    def __init__(
        self, base_url: str, account_id: int, token: str, *, timeout: float = 10.0
    ) -> None:
        self.base = f"{base_url.rstrip('/')}/api/v1/accounts/{account_id}"
        self.token = token
        self.dry_run = not token
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.base,
                timeout=self._timeout,
                headers={"api_access_token": self.token, "Content-Type": "application/json"},
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        if self.dry_run:
            log.info("chatwoot_dry_run", method=method, path=path, body=json)
            return {}
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                r = await self._client().request(method, path, json=json)
                if r.status_code >= 500 or r.status_code == 429:
                    raise httpx.HTTPStatusError(
                        f"status {r.status_code}", request=r.request, response=r
                    )
                if r.status_code >= 400:
                    # Error del lado nuestro (token, permisos, datos): no reintentar.
                    log.error(
                        "chatwoot_error", method=method, path=path,
                        status=r.status_code, body=r.text[:300],
                    )
                    return None
                return r.json() if r.content else {}
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                last_error = e
                await asyncio.sleep(0.5 * (attempt + 1))
        log.error("chatwoot_unreachable", method=method, path=path, error=str(last_error))
        return None

    # --- mensajes ---------------------------------------------------------

    async def send_text(self, conversation_id: int, content: str) -> Any:
        return await self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {"content": content, "message_type": "outgoing", "private": False},
        )

    async def send_options(self, conversation_id: int, content: str, options: list[str]) -> Any:
        """Botones (≤3) o lista (4–10) de WhatsApp vía input_select.

        Si las opciones no caben en los límites de WhatsApp, se manda como
        texto numerado para que el bot nunca se quede mudo.
        """
        if not fits_interactive(options):
            log.warning("interactive_fallback_text", options=options)
            return await self.send_text(conversation_id, numbered_fallback(content, options))
        return await self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {
                "content": content,
                "message_type": "outgoing",
                "private": False,
                "content_type": "input_select",
                "content_attributes": {"items": [{"title": o, "value": o} for o in options]},
            },
        )

    async def send_note(self, conversation_id: int, content: str) -> Any:
        return await self._request(
            "POST",
            f"/conversations/{conversation_id}/messages",
            {"content": content, "message_type": "outgoing", "private": True},
        )

    # --- etiquetas y atributos -------------------------------------------

    async def get_labels(self, conversation_id: int) -> list[str] | None:
        if self.dry_run:
            return []
        data = await self._request("GET", f"/conversations/{conversation_id}/labels")
        if data is None:
            return None
        payload = data.get("payload") if isinstance(data, dict) else None
        return list(payload) if isinstance(payload, list) else []

    async def set_labels(self, conversation_id: int, labels: list[str]) -> Any:
        # Ojo: Chatwoot REEMPLAZA la lista completa de etiquetas.
        return await self._request(
            "POST", f"/conversations/{conversation_id}/labels", {"labels": labels}
        )

    async def set_attributes(
        self, conversation_id: int, values: dict[str, str], *, merge: bool = True
    ) -> Any:
        return await self._request(
            "POST",
            f"/conversations/{conversation_id}/custom_attributes",
            {"custom_attributes": values, "merge": merge},
        )

    # --- estado -----------------------------------------------------------

    async def toggle_status(self, conversation_id: int, status: str) -> Any:
        return await self._request(
            "POST", f"/conversations/{conversation_id}/toggle_status", {"status": status}
        )

    async def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        if self.dry_run:
            return {"id": conversation_id, "status": "pending", "inbox_id": None}
        data = await self._request("GET", f"/conversations/{conversation_id}")
        return data if isinstance(data, dict) else None

    async def get_status(self, conversation_id: int) -> str | None:
        data = await self.get_conversation(conversation_id)
        return data.get("status") if data else None


_CLIENT: ChatwootClient | None = None


def get_client() -> ChatwootClient:
    global _CLIENT
    if _CLIENT is None:
        s = get_settings()
        _CLIENT = ChatwootClient(s.chatwoot_base_url, s.chatwoot_account_id, s.chatwoot_bot_token)
    return _CLIENT


def set_client(client: Any) -> None:
    """Para pruebas."""
    global _CLIENT
    _CLIENT = client
