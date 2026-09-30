"""Orquestador de un turno (v2): une el guion con los agentes.

Buffer → Policía → Router/Intérprete → Guion (máquina de estados)
      → M1 Preguntas (RAG) · M3 Agendamiento (Cal.com) → M2 Redactor (burbujas)

El guion (app/flow/engine.py) sigue siendo la autoridad: decide qué dato
falta, valida, clasifica, registra en Chatwoot y maneja los temporizadores
(M4 · Seguimiento). La IA solo entiende texto libre y hace que suene humano.
Si cualquier pieza de IA falla, el turno sale con los textos fijos de la v1.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field

import structlog

from app import llm, media, memory
from app.agents import interprete, m1_preguntas, redactor
from app.flow import engine as E
from app.flow import faq
from app.flow.matcher import is_reset
from app.security import input_guard, security_log
from app.tools import cal

log = structlog.get_logger(__name__)

# Pasos desde los que se puede llegar a elegir horario: ahí conviene tener
# los horarios de Cal.com ya consultados.
_PRE_SLOTS = {"EXPERIENCIA", "CONFIRMACION", "RECORDATORIO", "AGENDADO", "CONFIRMADO"}
_PASSIVE = {"AGENDADO", "CONFIRMADO", "NO_INTERESADO"}
_SLOT_TAKEN = "Uy, ese horario se acaba de ocupar 😅 Elige otro, por favor:"


@dataclass
class Batch:
    conversation_id: int
    status: str
    texts: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)
    other_media: bool = False
    phone: str | None = None


@dataclass
class TurnResult:
    session: E.Session
    actions: list[E.Action]
    user_text: str
    via: str  # "guion" | "ia" (para los logs)


def new_prospect() -> str:
    return uuid.uuid4().hex[:10]


def memory_id(conversation_id: int, s: E.Session) -> str:
    return f"cw:{conversation_id}:{s.prospect}"


def _messages(actions: list[E.Action]) -> list[E.Action]:
    return [a for a in actions if isinstance(a, (E.Send, E.SendOptions))]


def _merge_step(prev: list[E.Action], new: list[E.Action]) -> list[E.Action]:
    """Encadena pasos resueltos de una vez: solo sobrevive la última pregunta."""
    kept = [
        a
        for a in prev
        if not isinstance(a, (E.Send, E.SendOptions))
        and not (isinstance(a, E.Schedule) and a.kind == E.SEGUIMIENTO)
    ]
    return _messages(new) + kept + [a for a in new if not isinstance(a, (E.Send, E.SendOptions))]


async def _refresh_slots(s: E.Session) -> None:
    if not cal.ready():
        return
    age = time.time() - (s.offered_at or 0)
    stale = age > (900 if s.state in ("HORARIO", "HORA") else 120)
    if any("day" not in x for x in s.offered):
        stale = True  # formato anterior de horarios: volver a consultar
    if s.state not in _PRE_SLOTS | {"HORARIO", "HORA"} or not stale:
        return
    slots = await cal.get_slots()
    if slots is None:
        return  # Cal.com no respondió: se usan los horarios fijos
    if not slots:
        log.warning("cal_no_availability")
    s.offered = slots
    s.offered_at = time.time()


async def _book_if_needed(
    before: str, s: E.Session, actions: list[E.Action], conf: E.Conf, conversation_id: int
) -> list[E.Action]:
    """M3 · Agendamiento: al confirmar, reserva en Cal.com. Si el horario ya se
    ocupó, regresa a elegir horario con opciones frescas."""
    if not (before == "CONFIRMACION" and s.state == "AGENDADO"):
        return actions
    iso = s.data.get("horario_iso")
    if not (cal.ready() and iso):
        return actions  # horarios fijos (sin Cal.com): como la v1
    old = s.data.get("cal_booking_uid")
    if old:
        uid = await cal.reschedule(old, iso)
    else:
        uid = await cal.book(
            start=iso,
            name=s.data.get("nombre", ""),
            phone=s.data.get("telefono"),
            ref=f"{conversation_id}-{s.prospect}",
        )
    if uid:
        s.data["cal_booking_uid"] = uid
        log.info("cal_booked", conversation_id=conversation_id, rescheduled=bool(old))
        return actions
    # No se pudo: probablemente se ocupó. Volvemos a horarios.
    s.state = "HORARIO"
    s.data.pop("horario", None)
    s.data.pop("horario_iso", None)
    s.data.pop("dia", None)
    s.data["asistencia"] = ""
    s.offered_at = 0
    await _refresh_slots(s)
    return E.ask(s, conf, reask=True, prefix=_SLOT_TAKEN)


def _should_humanize(before: str | None, s: E.Session, actions: list[E.Action]) -> bool:
    msgs = _messages(actions)
    if not msgs:
        return False
    if before in (None, "INICIO") or s.state == "ASESOR":
        return False  # bienvenida (lleva el aviso de demo) y traspaso: textos fijos
    if any(isinstance(a, E.Send) and a.text == E.V.INFO_TARJETA for a in actions):
        return False
    if any(isinstance(a, E.SetPending) for a in actions):
        return False  # reinicio
    # La pregunta con botones debe ser el último mensaje.
    return isinstance(msgs[-1], E.SendOptions) or all(isinstance(m, E.Send) for m in msgs)


async def _humanize(
    s: E.Session, actions: list[E.Action], user_text: str, history: list[dict[str, str]], chat_id: str
) -> list[E.Action] | None:
    msgs = _messages(actions)
    options = msgs[-1].options if isinstance(msgs[-1], E.SendOptions) else []
    must_keep = [s.data.get("nombre", ""), s.data.get("horario", "")]
    bubbles = await redactor.rewrite(
        [m.text for m in msgs], options, user_text, history, must_keep, chat_id
    )
    if not bubbles:
        return None
    new_msgs: list[E.Action] = [E.Send(b) for b in bubbles[:-1]]
    new_msgs.append(E.SendOptions(bubbles[-1], list(options)) if options else E.Send(bubbles[-1]))
    return new_msgs + [a for a in actions if not isinstance(a, (E.Send, E.SendOptions))]


async def run(s: E.Session | None, batch: Batch, conf: E.Conf) -> TurnResult:
    chat_id = f"cw:{batch.conversation_id}"
    texts = [t for t in batch.texts if t]
    other_media = batch.other_media

    # Notas de voz → texto
    for url in batch.audio_urls:
        heard = await media.transcribe(url, chat_id)
        if heard:
            texts.append(heard)
        else:
            other_media = True
    text = "\n".join(texts).strip()

    if s is not None and not s.prospect:
        s.prospect = new_prospect()
    if s is not None and batch.phone and not s.data.get("telefono"):
        s.data["telefono"] = batch.phone
    before = s.state if s else None

    # Reiniciar (siempre gana)
    if any(is_reset(t) for t in texts):
        s, actions = E.reset(s, conf)
        s.prospect = new_prospect()
        if batch.phone:
            s.data["telefono"] = batch.phone
        return _finish(s, actions, text, "guion")

    # Solo foto/sticker/audio no entendido
    if not text:
        if not other_media:
            return _finish(s or E.Session(), [], "", "guion")
        s, actions = E.handle_media(s, conf)
        if not s.prospect:
            s.prospect = new_prospect()
        return _finish(s, actions, "[multimedia]", "guion")

    # Policía: filtro de entrada
    guard = input_guard.classify(text)
    if guard.is_block:
        security_log.log_event(
            "input_blocked", chat_id=chat_id, severity="warning", channel="whatsapp",
            reasons=",".join(guard.reasons),
        )
        actions: list[E.Action] = [E.Send(input_guard.BLOCK_RESPONSE_TEXTS[0])]
        if s is not None and s.state in E.QUESTION_STATES:
            actions = E.ask(s, conf, reask=True, prefix=input_guard.BLOCK_RESPONSE_TEXTS[0])
        return _finish(s or E.Session(), actions, text, "guion")
    text = guard.sanitized or text

    if s is not None:
        await _refresh_slots(s)

    use_ai = s is not None and s.state not in ("INICIO", "ASESOR") and llm.available(chat_id)
    history: list[dict[str, str]] = []
    if use_ai:
        history = await memory.load(memory_id(batch.conversation_id, s))  # type: ignore[arg-type]

    via = "guion"
    has_question = faq.looks_like_question(text) or bool(faq.detect_topics(text))
    if not use_ai or (E.quick_match(s, text, conf) and not has_question):
        s, actions = E.handle_text(s, text, conf)
    else:
        s, actions, via = await _ai_step(s, text, conf, history, chat_id)  # type: ignore[arg-type]

    if not s.prospect:
        s.prospect = new_prospect()
    if batch.phone and not s.data.get("telefono"):
        s.data["telefono"] = batch.phone

    actions = await _book_if_needed(before or "", s, actions, conf, batch.conversation_id)

    if use_ai and _should_humanize(before, s, actions):
        human = await _humanize(s, actions, text, history, chat_id)
        if human is not None:
            actions = human
            via = "ia"
    return _finish(s, actions, text, via)


async def _ai_step(
    s: E.Session, text: str, conf: E.Conf, history: list[dict[str, str]], chat_id: str
) -> tuple[E.Session, list[E.Action], str]:
    interp = await interprete.interpret(s, text, conf, history, chat_id)
    if interp is None:
        s, actions = E.handle_text(s, text, conf)
        return s, actions, "guion"
    if interp.asesor:
        s, actions = E.handle_text(s, "asesor", conf)
        return s, actions, "ia"

    answer = await m1_preguntas.answer(interp.pregunta, chat_id) if interp.pregunta else None

    if s.state in _PASSIVE:
        if answer and not interp.respuesta:
            return s, [E.Send(answer)], "ia"
        s, actions = E.handle_text(s, interp.respuesta or text, conf)
        if answer:
            actions.insert(0, E.Send(answer))
        return s, actions, "ia"

    consumed = False
    actions: list[E.Action] = []
    if interp.respuesta:
        trial = copy.deepcopy(s)
        s2, acts = E.handle_text(trial, interp.respuesta, conf)
        if s2.state != s.state:
            s, actions, consumed = s2, acts, True

    if consumed:
        # Datos extra dichos de una vez ("soy Juan, tengo 27, vivo en Apizaco")
        while s.state in E.FIELD_BY_STATE and E.FIELD_BY_STATE[s.state] in interp.extras:
            value = interp.extras[E.FIELD_BY_STATE[s.state]]
            trial = copy.deepcopy(s)
            s2, acts = E.handle_text(trial, value, conf)
            if s2.state == s.state:
                break
            s, actions = s2, _merge_step(actions, acts)
        if answer:
            actions.insert(0, E.Send(answer))
        return s, actions, "ia"

    if answer:
        return s, E.ask(s, conf, reask=True, prefix=answer), "ia"
    s, actions = E.handle_text(s, text, conf)
    return s, actions, "guion"


def _finish(s: E.Session, actions: list[E.Action], user_text: str, via: str) -> TurnResult:
    options = [a.options for a in actions if isinstance(a, E.SendOptions)]
    if options:
        s.last_options = list(options[-1])
    elif _messages(actions):
        s.last_options = []
    return TurnResult(session=s, actions=actions, user_text=user_text, via=via)
