"""Logger dedicado a eventos de seguridad.

Escribe a `logs/security_events.log` con rotación diaria, además de seguir
loggeando vía structlog para que aparezca en stdout/journalctl. El
`chat_id` se hashea (no se loguea en claro) para no dejar números de
teléfono ni IDs de subscriber en disco.
"""

from __future__ import annotations

import hashlib
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

_LOG_DIR = Path(os.environ.get("SECURITY_LOG_DIR", "logs"))
_LOG_FILE = _LOG_DIR / "security_events.log"

_file_logger: logging.Logger | None = None
_struct_log = structlog.get_logger("security")


def _ensure_logger() -> logging.Logger | None:
    """Crea el handler una sola vez. Si no podemos escribir (ej. FS read-only
    en EasyPanel) seguimos loggeando solo vía structlog."""
    global _file_logger
    if _file_logger is not None:
        return _file_logger
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger = logging.getLogger("chatbot.security")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.propagate = False
        _file_logger = logger
        return logger
    except Exception:  # noqa: BLE001
        # FS sin permisos / no escribible: seguimos con structlog en stdout.
        return None


def hash_chat_id(chat_id: str | None) -> str:
    if not chat_id:
        return "anon"
    return hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:16]


def log_event(
    event: str,
    *,
    chat_id: str | None,
    severity: str = "info",
    **fields: Any,
) -> None:
    """Registra un evento de seguridad.

    `event`: identificador corto (ej. `input_blocked`, `webhook_invalid_signature`).
    `chat_id`: se hashea antes de escribir.
    `severity`: info | warning | critical (para grep rápido).
    """
    hashed = hash_chat_id(chat_id)
    payload: dict[str, Any] = {"event": event, "chat": hashed, "severity": severity}
    payload.update(fields)

    # structlog (stdout/journalctl).
    # OJO: structlog usa el PRIMER arg posicional como `event`. Nuestro
    # `payload` ya trae una clave `event`, así que pasar **payload chocaba
    # ("got multiple values for argument 'event'") y reventaba el nodo
    # resolve_media -> el bot se quedaba MUDO (en TODOS los canales). Usamos
    # el nombre del evento como mensaje y quitamos la clave duplicada.
    kw = {k: v for k, v in payload.items() if k != "event"}
    if severity == "critical":
        _struct_log.error(event, **kw)
    elif severity == "warning":
        _struct_log.warning(event, **kw)
    else:
        _struct_log.info(event, **kw)

    # archivo dedicado (best-effort)
    logger = _ensure_logger()
    if logger is None:
        return
    parts = [f"{k}={v}" for k, v in payload.items()]
    line = " ".join(parts)
    if severity == "critical":
        logger.error(line)
    elif severity == "warning":
        logger.warning(line)
    else:
        logger.info(line)
