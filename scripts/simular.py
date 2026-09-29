"""Platica con el bot en la terminal, sin WhatsApp ni Chatwoot.

Uso:
    python scripts/simular.py

Comandos especiales:
    /foto          simula que el candidato manda una foto o audio
    /recordatorio  dispara el recordatorio (como si pasaran los 2 minutos)
    /seguimiento   dispara el seguimiento por abandono del paso actual
    /estado        muestra el paso actual y los datos guardados
    /salir         termina
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.flow import engine as E  # noqa: E402


def show(actions: list[E.Action]) -> None:
    for a in actions:
        if isinstance(a, E.Send):
            print(f"\n🤖 {a.text}")
        elif isinstance(a, E.SendOptions):
            print(f"\n🤖 {a.text}")
            print("   " + "  ".join(f"[{i}] {o}" for i, o in enumerate(a.options, 1)))
        elif isinstance(a, E.Labels):
            parts = [f"+{x}" for x in a.add] + [f"-{x}" for x in a.remove]
            if a.clear_bot:
                parts.insert(0, "(limpia etiquetas del bot)")
            print(f"   · etiquetas: {' '.join(parts)}")
        elif isinstance(a, E.Attrs):
            prefix = "(reemplaza todos) " if a.replace else ""
            print(f"   · atributos: {prefix}{a.values}")
        elif isinstance(a, E.Note):
            print("   · nota privada:\n     " + a.text.replace("\n", "\n     "))
        elif isinstance(a, E.Handoff):
            print("   · conversación → ABIERTA (humano)")
        elif isinstance(a, E.SetPending):
            print("   · conversación → PENDIENTE (bot)")
        elif isinstance(a, E.Schedule):
            print(f"   · temporizador '{a.kind}' en {a.delay_seconds} s")
        elif isinstance(a, E.CancelJobs):
            print(f"   · cancela temporizadores: {a.kinds or 'todos'}")


def main() -> None:
    s = get_settings()
    conf = E.Conf(
        slots=s.slots,
        reminder_delay=s.reminder_delay_seconds,
        followup_delay=s.followup_delay_seconds,
        demo=s.demo_mode,
    )
    session: E.Session | None = None
    print(__doc__)
    while True:
        try:
            text = input("\n👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text == "/salir":
            return
        if text == "/estado":
            print(session.to_dict() if session else "(sin sesión)")
            continue
        if text == "/foto":
            session, actions = E.handle_media(session, conf)
        elif text == "/recordatorio":
            session, actions = E.on_timer(session, E.RECORDATORIO, "AGENDADO", conf)
            if not actions:
                print("   (no aplica: la entrevista no está agendada)")
        elif text == "/seguimiento":
            state = session.state if session else None
            session, actions = E.on_timer(session, E.SEGUIMIENTO, state, conf)
            if not actions:
                print("   (no aplica en este paso o ya se mandó)")
        elif session is not None and session.state == "ASESOR" and not text.lower().startswith("reiniciar"):
            print("   (la conversación la atiende un humano: el bot no responde; escribe 'reiniciar')")
            continue
        else:
            session, actions = E.handle_text(session, text, conf)
        show(actions)


if __name__ == "__main__":
    main()
