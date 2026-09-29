"""Recibe eventos de Chatwoot, decide si el bot debe responder y ejecuta.

Reglas (verificadas contra Chatwoot v4.17.0):
- Solo `message_created` entrantes, públicos, de la bandeja configurada.
  Chatwoot también manda al bot sus PROPIOS mensajes: se ignoran (bucle).
- Chatwoot espera 5 s y reintenta: se responde de inmediato y se procesa en
  segundo plano; los reintentos se descartan por id de mensaje.
- El bot solo responde si la conversación está en "pending". Si está
  "open", la atiende un humano (excepto "reiniciar", que siempre funciona).
- Un candado por conversación procesa sus mensajes en orden, uno a la vez.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog

from app import executor, rate_limit
from app.chatwoot import get_client
from app.config import get_settings
from app.flow import engine
from app.flow.matcher import is_reset, sanitize
from app.store import Job, get_store
from app.tasks import spawn

log = structlog.get_logger(__name__)

_locks: dict[int, asyncio.Lock] = {}


def _lock_for(conversation_id: int) -> asyncio.Lock:
    if conversation_id not in _locks:
        _locks[conversation_id] = asyncio.Lock()
    return _locks[conversation_id]


def build_conf() -> engine.Conf:
    s = get_settings()
    return engine.Conf(
        slots=s.slots,
        reminder_delay=s.reminder_delay_seconds,
        followup_delay=s.followup_delay_seconds,
        demo=s.demo_mode,
    )


@dataclass
class Incoming:
    conversation_id: int
    message_id: int
    status: str
    text: str
    has_media: bool


def parse_event(payload: dict[str, Any]) -> tuple[Incoming | None, str]:
    """Devuelve (mensaje entrante, motivo). Si no aplica, (None, motivo)."""
    s = get_settings()
    if not isinstance(payload, dict):
        return None, "invalid_payload"
    if payload.get("event") != "message_created":
        return None, "ignored_event"
    if payload.get("message_type") != "incoming":
        return None, "not_incoming"
    if payload.get("private"):
        return None, "private"
    conv = payload.get("conversation") or {}
    inbox_id = (payload.get("inbox") or {}).get("id") or conv.get("inbox_id")
    if inbox_id != s.chatwoot_inbox_id:
        return None, "other_inbox"
    account_id = (payload.get("account") or {}).get("id")
    if account_id is not None and account_id != s.chatwoot_account_id:
        return None, "other_account"
    conversation_id = conv.get("id")
    message_id = payload.get("id")
    if not isinstance(conversation_id, int) or not isinstance(message_id, int):
        return None, "missing_ids"
    attrs = payload.get("content_attributes") or {}
    has_media = bool(payload.get("attachments")) or bool(
        isinstance(attrs, dict) and attrs.get("is_unsupported")
    )
    text = sanitize(payload.get("content") or "")
    if isinstance(attrs, dict) and attrs.get("is_unsupported"):
        text = ""  # "Este mensaje no está disponible." lo pone Chatwoot, no el candidato
    return (
        Incoming(
            conversation_id=conversation_id,
            message_id=message_id,
            status=conv.get("status") or "pending",
            text=text,
            has_media=has_media,
        ),
        "ok",
    )


def accept(payload: dict[str, Any]) -> str:
    """Filtro rápido (dentro del request del webhook). Programa el trabajo."""
    msg, reason = parse_event(payload)
    if msg is None:
        return reason
    store = get_store()
    if not store.mark_processed(msg.message_id):
        return "duplicate"
    ok, _ = rate_limit.hit(
        f"conv:{msg.conversation_id}", get_settings().chat_rate_limit_per_min, 60
    )
    if not ok:
        log.warning("rate_limited", conversation_id=msg.conversation_id)
        return "rate_limited"
    spawn(process_incoming(msg))
    return "queued"


async def process_incoming(msg: Incoming) -> None:
    store = get_store()
    conf = build_conf()
    async with _lock_for(msg.conversation_id):
        session = store.get_session(msg.conversation_id)
        before = session.state if session else None
        if session is None and not await _conversation_is_ours(msg.conversation_id):
            return
        if msg.text and is_reset(msg.text):
            session, actions = engine.reset(session, conf)
        elif msg.status != "pending":
            log.info(
                "skip_human_handling",
                conversation_id=msg.conversation_id,
                status=msg.status,
            )
            return
        elif msg.has_media and not msg.text:
            session, actions = engine.handle_media(session, conf)
        elif msg.text:
            session, actions = engine.handle_text(session, msg.text, conf)
        else:
            return
        log.info(
            "message_processed",
            conversation_id=msg.conversation_id,
            text_len=len(msg.text),
            media=msg.has_media,
            state_from=before,
            state_to=session.state,
            actions=len(actions),
        )
        try:
            await executor.run(
                msg.conversation_id,
                session,
                actions,
                client=get_client(),
                store=store,
                send_delay=get_settings().send_delay_seconds,
            )
        finally:
            store.save_session(msg.conversation_id, session)


async def _conversation_is_ours(conversation_id: int) -> bool:
    """Defensa extra: el token del bot puede escribir en CUALQUIER conversación
    de la cuenta. Antes de abrir una sesión nueva confirmamos con Chatwoot que
    la conversación es de la bandeja del bot (evita payloads falsificados)."""
    client = get_client()
    if getattr(client, "dry_run", False):
        return True
    conv = await client.get_conversation(conversation_id)
    if conv is None:
        # Chatwoot no respondió: preferimos contestar a dejar mudo al candidato.
        log.warning("inbox_check_unavailable", conversation_id=conversation_id)
        return True
    if conv.get("inbox_id") != get_settings().chatwoot_inbox_id:
        log.warning(
            "conversation_not_in_bot_inbox",
            conversation_id=conversation_id,
            inbox_id=conv.get("inbox_id"),
        )
        return False
    return True


async def process_job(job: Job) -> None:
    """Dispara un temporizador (recordatorio o seguimiento)."""
    store = get_store()
    client = get_client()
    conf = build_conf()
    async with _lock_for(job.conversation_id):
        session = store.get_session(job.conversation_id)
        if session is None:
            return
        status = await client.get_status(job.conversation_id)
        if status is not None and status != "pending":
            log.info("job_skip_not_pending", job=job.kind, status=status)
            return
        session, actions = engine.on_timer(session, job.kind, job.state_at_schedule, conf)
        if not actions:
            log.info("job_noop", conversation_id=job.conversation_id, job=job.kind)
            store.save_session(job.conversation_id, session)
            return
        log.info(
            "job_fired",
            conversation_id=job.conversation_id,
            job=job.kind,
            state_to=session.state,
        )
        try:
            await executor.run(
                job.conversation_id,
                session,
                actions,
                client=client,
                store=store,
                send_delay=get_settings().send_delay_seconds,
            )
        finally:
            store.save_session(job.conversation_id, session)


async def scheduler_loop() -> None:
    """Reloj de temporizadores (mismo patrón que _reaper_loop de la plantilla)."""
    tick = max(1.0, get_settings().scheduler_tick_seconds)
    log.info("scheduler_start", tick=tick)
    n = 0
    while True:
        try:
            for job in get_store().take_due():
                await process_job(job)
        except Exception as e:  # noqa: BLE001
            log.exception("scheduler_error", error=str(e))
        n += 1
        if n % 720 == 0:  # ~1 h con tick de 5 s
            try:
                get_store().prune()
            except Exception as e:  # noqa: BLE001
                log.warning("prune_failed", error=str(e))
            log.info("scheduler_heartbeat")
        await asyncio.sleep(tick)
