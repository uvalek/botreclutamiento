"""Convierte las acciones del motor en llamadas a Chatwoot y a la base local.

Cada acción es independiente: si una falla se registra en el log y se sigue
con la siguiente, para que el candidato nunca se quede sin respuesta.
"""

from __future__ import annotations

import asyncio

import structlog

from app import vacante
from app.chatwoot import ChatwootClient
from app.flow.engine import (
    Action,
    Attrs,
    CancelJobs,
    Handoff,
    Labels,
    Note,
    Schedule,
    Send,
    SendOptions,
    Session,
    SetPending,
)
from app.store import Store

log = structlog.get_logger(__name__)


def merge_labels(current: list[str], action: Labels) -> list[str]:
    """Lista final de etiquetas: conserva las que puso RH a mano."""
    remove = set(action.remove)
    if action.clear_bot:
        remove |= set(vacante.ETIQUETAS_DEL_BOT)
    out = [label for label in current if label not in remove]
    for label in action.add:
        if label not in out:
            out.append(label)
    return out


async def run(
    conversation_id: int,
    session: Session,
    actions: list[Action],
    *,
    client: ChatwootClient,
    store: Store,
    send_delay: float,
) -> None:
    sent_before = False
    for action in actions:
        try:
            if isinstance(action, (Send, SendOptions)):
                # Pausa entre burbujas: Chatwoot las manda a WhatsApp en una
                # cola, y sin pausa pueden llegar desordenadas.
                if sent_before and send_delay > 0:
                    # Pausa proporcional al largo, como si se estuviera escribiendo.
                    await asyncio.sleep(min(3.5, send_delay + len(action.text) / 100))
                if isinstance(action, SendOptions):
                    await client.send_options(conversation_id, action.text, action.options)
                else:
                    await client.send_text(conversation_id, action.text)
                sent_before = True
            elif isinstance(action, Note):
                await client.send_note(conversation_id, action.text)
            elif isinstance(action, Labels):
                current = await client.get_labels(conversation_id)
                if current is None:
                    # Sin la lista actual no escribimos: podríamos borrar
                    # etiquetas que puso RH.
                    log.warning("labels_skip_no_current", conversation_id=conversation_id)
                    continue
                new = merge_labels(current, action)
                if new != current:
                    await client.set_labels(conversation_id, new)
            elif isinstance(action, Attrs):
                await client.set_attributes(
                    conversation_id, action.values, merge=not action.replace
                )
            elif isinstance(action, Handoff):
                await client.toggle_status(conversation_id, "open")
            elif isinstance(action, SetPending):
                await client.toggle_status(conversation_id, "pending")
            elif isinstance(action, Schedule):
                store.schedule(conversation_id, action.kind, action.delay_seconds, session.state)
            elif isinstance(action, CancelJobs):
                store.cancel(conversation_id, action.kinds)
        except Exception as e:  # noqa: BLE001
            log.exception(
                "action_failed",
                conversation_id=conversation_id,
                action=type(action).__name__,
                error=str(e),
            )
