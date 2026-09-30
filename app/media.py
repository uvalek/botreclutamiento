"""Notas de voz → texto (Whisper), como en la plantilla.

Fotos y stickers siguen recibiendo "por ahora solo puedo leer texto".
Si la transcripción falla, el audio se trata igual que una foto.
"""

from __future__ import annotations

import asyncio
import io

import httpx
import structlog

from app import llm
from app.config import get_settings

log = structlog.get_logger(__name__)

_MAX_BYTES = 8 * 1024 * 1024


async def transcribe(url: str, chat_id: str) -> str | None:
    if not url or not llm.available(chat_id):
        return None
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            r = await http.get(url)
            r.raise_for_status()
            audio = r.content
    except httpx.HTTPError as e:
        log.warning("audio_download_failed", error=str(e)[:160])
        return None
    if not audio or len(audio) > _MAX_BYTES:
        return None
    buf = io.BytesIO(audio)
    buf.name = "audio.ogg"
    try:
        resp = await asyncio.wait_for(
            llm.client().audio.transcriptions.create(model="whisper-1", file=buf, language="es"),
            timeout=max(20.0, get_settings().llm_timeout_seconds),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("audio_transcribe_failed", error=str(e)[:160])
        return None
    text = (getattr(resp, "text", "") or "").strip()
    return text or None
