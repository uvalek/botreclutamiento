"""Registro del candidato en Supabase (`contactos`).

Un renglón por prospecto: `chat_id = cw:{conversación}:{prospecto}`. Así,
cada vez que se escribe "reiniciar" en la demo queda un registro nuevo y el
historial de todos los prospectos se conserva.
"""

from __future__ import annotations

from typing import Any

from app import vacante
from app.config import get_settings
from app.flow import rules
from app.tools import supa

TABLE = "contactos"

_ETAPA = {
    "BIENVENIDA": "nuevo",
    "INFO": "nuevo",
    "NO_INTERESADO": "no_interesado",
    "AGENDADO": "cita_agendada",
    "RECORDATORIO": "cita_agendada",
    "CONFIRMADO": "confirmado",
    "ASESOR": "handoff",
}


def row_for(chat_id: str, conversation_id: int, state: str, data: dict[str, str]) -> dict[str, Any]:
    etapa = _ETAPA.get(state, "en_proceso")
    if data.get("clasificacion") and etapa == "en_proceso":
        etapa = "calificado" if rules.is_califica(data["clasificacion"]) else "revisar"
    return {
        "chat_id": chat_id,
        "canal": "whatsapp",
        "demo": get_settings().demo_key,
        "chatwoot_conversation_id": conversation_id,
        "vacante": vacante.VACANTE_RESUMEN,
        "nombre": data.get("nombre"),
        "telefono": data.get("telefono"),
        "edad_rango": data.get("edad"),
        "municipio": rules.municipio_display(data) or None,
        "turno": data.get("turno"),
        "documentos": data.get("documentos"),
        "experiencia": data.get("experiencia"),
        "clasificacion": data.get("clasificacion"),
        "entrevista_horario": data.get("horario"),
        "entrevista_at": data.get("horario_iso") or None,
        "entrevista_asistencia": data.get("asistencia"),
        "cal_booking_uid": data.get("cal_booking_uid"),
        "etapa_seguimiento": etapa,
    }


async def sync(chat_id: str, conversation_id: int, state: str, data: dict[str, str]) -> None:
    """Crea o actualiza el renglón del prospecto. Silencioso ante errores."""
    if not supa.ready():
        return
    row = row_for(chat_id, conversation_id, state, data)
    existing = await supa.select(TABLE, {"select": "id", "chat_id": f"eq.{chat_id}", "limit": "1"})
    if existing:
        values = {k: v for k, v in row.items() if k != "chat_id"}
        await supa.update(TABLE, {"id": f"eq.{existing[0]['id']}"}, values)
    elif existing is not None:
        await supa.insert(TABLE, row)
