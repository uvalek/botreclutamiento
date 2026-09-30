"""FastAPI: webhook del Agent Bot de Chatwoot + salud + versión."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from app import processor, rag
from app.chatwoot import fits_interactive, get_client
from app.config import get_settings
from app.store import get_store
from app.tasks import spawn
from app.tools import cal, supa

settings = get_settings()
logging.basicConfig(level=settings.log_level)
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = structlog.get_logger(__name__)

# Se sube a mano en cada cambio importante para confirmar que EasyPanel
# redeployó (GET /version).
_VERSION = "v2.5-sede-centro-convenciones-2026-09-29"


@asynccontextmanager
async def lifespan(_: FastAPI):
    store = get_store()
    log.info(
        "startup",
        version=_VERSION,
        db=store.path,
        inbox_id=settings.chatwoot_inbox_id,
        dry_run=settings.dry_run,
        slots=settings.slots,
        reminder_s=settings.reminder_delay_seconds,
        followup_s=settings.followup_delay_seconds,
    )
    if settings.dry_run:
        log.warning("chatwoot_token_missing_dry_run")
    if not settings.webhook_key and not settings.chatwoot_webhook_secret:
        log.warning("webhook_unprotected_set_CHATWOOT_WEBHOOK_SECRET")
    if len(settings.slots) < 1 or not fits_interactive(settings.slots):
        log.warning("interview_slots_will_use_text", slots=settings.slots)
    log.info(
        "startup_v2",
        ai=settings.ai_ready,
        model=settings.openai_model,
        supabase=settings.supabase_ready,
        cal=settings.cal_ready,
        buffer_s=settings.buffer_window_seconds,
    )
    spawn(_startup_tasks())
    spawn(processor.scheduler_loop())
    yield
    await get_client().close()
    await supa.close()


async def _startup_tasks() -> None:
    try:
        n = await processor.recover_buffer()
        if n:
            log.info("buffer_recovered", messages=n)
    except Exception as e:  # noqa: BLE001
        log.exception("buffer_recover_failed", error=str(e))
    try:
        log.info("rag_status", status=await rag.ensure_ingested())
    except Exception as e:  # noqa: BLE001
        log.exception("rag_ingest_failed", error=str(e))
    await cal.log_event_types()


app = FastAPI(title="Bot de reclutamiento (demo Adlek)", lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > get_settings().max_request_body_bytes:
            return Response(status_code=413, content="payload too large")
        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> dict[str, object]:
    return {
        "version": _VERSION,
        "dry_run": settings.dry_run,
        "ai": settings.ai_ready,
        "supabase": settings.supabase_ready,
        "cal": settings.cal_ready,
    }


def valid_signature(secret: str, body: bytes, timestamp: str | None, signature: str | None) -> bool:
    """Firma de Chatwoot: sha256=HMAC_SHA256(secret, "{timestamp}.{body}")."""
    if not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs(time.time() - int(timestamp)) > settings.signature_max_age_seconds:
        return False
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256)
    return hmac.compare_digest(f"sha256={digest.hexdigest()}", signature)


@app.post("/webhooks/chatwoot")
async def chatwoot_webhook(request: Request, key: str | None = None) -> dict[str, str]:
    expected = settings.webhook_key
    if expected and not hmac.compare_digest((key or "").encode(), expected.encode()):
        raise HTTPException(status_code=403, detail="invalid key")
    body = await request.body()
    secret = settings.chatwoot_webhook_secret
    if secret and not valid_signature(
        secret,
        body,
        request.headers.get("x-chatwoot-timestamp"),
        request.headers.get("x-chatwoot-signature"),
    ):
        log.warning("webhook_bad_signature")
        raise HTTPException(status_code=403, detail="invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid json") from None
    status = processor.accept(payload)
    if status not in ("ignored_event", "not_incoming", "private"):
        log.info("webhook_in", result=status)
    return {"status": status}
