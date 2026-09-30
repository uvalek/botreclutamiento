"""Recibe eventos de Chatwoot, los junta en el buffer y ejecuta cada turno.

Reglas (verificadas contra Chatwoot v4.17.0):
- Solo `message_created` entrantes, públicos, de la bandeja configurada.
  Chatwoot también manda al bot sus PROPIOS mensajes: se ignoran (bucle).
- Chatwoot espera 5 s y reintenta: se responde de inmediato y se procesa en
  segundo plano; los reintentos se descartan por id de mensaje.
- Buffer de entrada: si el candidato manda varios mensajes seguidos, se
  espera a que termine (BUFFER_WINDOW_SECONDS) y se contesta todo junto.
  Si tocó un botón, casi no se espera (BUFFER_BUTTON_SECONDS).
- El bot solo responde si la conversación está en "pending". Si está
  "open", la atiende un humano (excepto "reiniciar", que siempre funciona).
- Un candado por conversación procesa sus turnos en orden, uno a la vez.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

from app import executor, memory, rate_limit, turn
from app.chatwoot import get_client
from app.config import get_settings
from app.flow import engine
from app.flow.matcher import is_reset, sanitize
from app.store import Job, get_store
from app.tasks import spawn
from app.tools import contactos

log = structlog.get_logger(__name__)

_locks: dict[int, asyncio.Lock] = {}
_pending: dict[int, list[Incoming]] = {}
_deadline: dict[int, float] = {}
_first_seen: dict[int, float] = {}
_flushing: set[int] = set()
_MAX_WAIT = 15.0  # nunca esperar más que esto desde el primer mensaje


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
    audio_urls: list[str] = field(default_factory=list)
    phone: str | None = None


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
    unsupported = isinstance(attrs, dict) and bool(attrs.get("is_unsupported"))
    audio_urls: list[str] = []
    other_media = unsupported
    for att in payload.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        if att.get("file_type") == "audio" and att.get("data_url"):
            audio_urls.append(att["data_url"])
        else:
            other_media = True
    text = "" if unsupported else sanitize(payload.get("content") or "")
    sender = payload.get("sender") or {}
    phone = sender.get("phone_number") if isinstance(sender, dict) else None
    return (
        Incoming(
            conversation_id=conversation_id,
            message_id=message_id,
            status=conv.get("status") or "pending",
            text=text,
            has_media=other_media,
            audio_urls=audio_urls,
            phone=phone,
        ),
        "ok",
    )


def accept(payload: dict[str, Any]) -> str:
    """Filtro rápido (dentro del request del webhook). Mete el mensaje al buffer."""
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
    store.buffer_add(msg.message_id, msg.conversation_id, asdict(msg))
    _enqueue(msg)
    return "queued"


def _is_quick(msg: Incoming) -> bool:
    """¿Tocó un botón (o escribió un comando)? Entonces casi no esperamos."""
    if not msg.text:
        return False
    if is_reset(msg.text):
        return True
    session = get_store().get_session(msg.conversation_id)
    return bool(session and msg.text in session.last_options)


def _enqueue(msg: Incoming) -> None:
    s = get_settings()
    conv = msg.conversation_id
    _pending.setdefault(conv, []).append(msg)
    now = time.monotonic()
    first = _first_seen.setdefault(conv, now)
    wait = s.buffer_button_seconds if _is_quick(msg) else s.buffer_window_seconds
    _deadline[conv] = min(now + max(0.0, wait), first + _MAX_WAIT)
    if conv not in _flushing:
        _flushing.add(conv)
        spawn(_flush_when_quiet(conv))


async def _flush_when_quiet(conv: int) -> None:
    try:
        while True:
            remaining = _deadline.get(conv, 0) - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        msgs = _pending.pop(conv, [])
        _deadline.pop(conv, None)
        _first_seen.pop(conv, None)
    finally:
        _flushing.discard(conv)
    if msgs:
        await process_batch(conv, msgs)


async def process_batch(conversation_id: int, msgs: list[Incoming]) -> None:
    store = get_store()
    conf = build_conf()
    ids = [m.message_id for m in msgs]
    try:
        async with _lock_for(conversation_id):
            session = store.get_session(conversation_id)
            before = session.state if session else None
            if session is None and not await _conversation_is_ours(conversation_id):
                return
            texts = [m.text for m in msgs if m.text]
            wants_reset = any(is_reset(t) for t in texts)
            status = msgs[-1].status
            if not wants_reset and status != "pending":
                log.info("skip_human_handling", conversation_id=conversation_id, status=status)
                return
            batch = turn.Batch(
                conversation_id=conversation_id,
                status=status,
                texts=texts,
                audio_urls=[u for m in msgs for u in m.audio_urls],
                other_media=any(m.has_media for m in msgs),
                phone=next((m.phone for m in msgs if m.phone), None),
            )
            try:
                result = await turn.run(session, batch, conf)
            except Exception as e:  # noqa: BLE001
                # Red de seguridad: si el turno truena, el guion responde solo.
                log.exception("turn_failed", conversation_id=conversation_id, error=str(e))
                text = "\n".join(texts)
                if text and is_reset(text):
                    s2, acts = engine.reset(session, conf)
                elif text:
                    s2, acts = engine.handle_text(session, text, conf)
                else:
                    s2, acts = engine.handle_media(session, conf)
                result = turn.TurnResult(s2, acts, text, "guion")
            session = result.session
            log.info(
                "turn_processed",
                conversation_id=conversation_id,
                messages=len(msgs),
                state_from=before,
                state_to=session.state,
                via=result.via,
                actions=len(result.actions),
            )
            try:
                await executor.run(
                    conversation_id,
                    session,
                    result.actions,
                    client=get_client(),
                    store=store,
                    send_delay=get_settings().send_delay_seconds,
                )
            finally:
                store.save_session(conversation_id, session)
            spawn(_after_turn(conversation_id, session, result.user_text, result.actions))
    finally:
        store.buffer_remove(ids)


async def _after_turn(
    conversation_id: int, session: engine.Session, user_text: str, actions: list[engine.Action]
) -> None:
    """Memoria y contactos en Supabase (no bloquea la respuesta)."""
    try:
        mid = turn.memory_id(conversation_id, session)
        if user_text:
            await memory.append(mid, "user", [user_text])
        bot = [a.text for a in actions if isinstance(a, (engine.Send, engine.SendOptions))]
        if bot:
            await memory.append(mid, "assistant", bot)
        await contactos.sync(mid, conversation_id, session.state, session.data)
    except Exception as e:  # noqa: BLE001
        log.warning("after_turn_failed", error=str(e)[:160])


async def recover_buffer() -> int:
    """Al arrancar: procesa mensajes que quedaron en el buffer (reinicio)."""
    leftovers = get_store().buffer_leftovers()
    n = 0
    for conv, rows in leftovers.items():
        msgs = []
        for r in rows:
            try:
                msgs.append(Incoming(**r))
            except TypeError:
                continue
        if msgs:
            n += len(msgs)
            await process_batch(conv, msgs)
    return n


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
    """M4 · Seguimiento: dispara un temporizador (recordatorio o seguimiento)."""
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
        options = [a.options for a in actions if isinstance(a, engine.SendOptions)]
        if options:
            session.last_options = list(options[-1])
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
        spawn(_after_turn(job.conversation_id, session, "", actions))


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
