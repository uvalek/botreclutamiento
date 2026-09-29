"""Clasificación del candidato y resumen para Recursos Humanos."""

from __future__ import annotations

from app import vacante

CALIFICA = "Califica"


def classify(data: dict[str, str]) -> tuple[str, list[str]]:
    """Devuelve (clasificación, líneas que se le explican al candidato).

    - Le faltan documentos → "Revisar: faltan documentos" (sí puede agendar).
    - Municipio "Otro"     → "Revisar: fuera de ruta" (sí puede agendar).
    - Si no                → "Califica".
    Edad, turno y experiencia no descalifican; solo se registran.
    """
    motivos: list[str] = []
    lineas: list[str] = []
    if data.get("documentos") == "Incompletos":
        motivos.append("faltan documentos")
        lineas.append(vacante.CLASIF_FALTAN_DOCS)
    if data.get("municipio") and data.get("municipio") not in vacante.MUNICIPIOS_CON_RUTA:
        motivos.append("fuera de ruta")
        lineas.append(vacante.CLASIF_FUERA_RUTA)
    if not motivos:
        return CALIFICA, [vacante.CLASIF_CALIFICA]
    return "Revisar: " + " y ".join(motivos), lineas


def is_califica(clasificacion: str | None) -> bool:
    return clasificacion == CALIFICA


def municipio_display(data: dict[str, str]) -> str:
    municipio = data.get("municipio") or ""
    detalle = data.get("municipio_detalle") or ""
    if municipio == "Otro" and detalle:
        return f"Otro ({detalle})"
    return municipio


_EXPERIENCIA_CORTA = {
    "Más de 6 meses": "+6 meses",
    "Menos de 6 meses": "Menos de 6 meses",
    "Sin experiencia": "Sin experiencia",
}


def summary(data: dict[str, str], context: str | None = None) -> str:
    """Nota privada de una ojeada para RH.

    📋 **Juan Pérez López**
    Califica · 25–35 · Apizaco · Matutino · +6 meses · Entrevista Mié 30 sep 9:00 · Confirmó
    """
    nombre = data.get("nombre") or "Candidato sin nombre"
    parts: list[str] = []
    if data.get("clasificacion"):
        parts.append(data["clasificacion"])
    if data.get("edad"):
        parts.append(data["edad"])
    if data.get("municipio"):
        parts.append(municipio_display(data))
    if data.get("turno"):
        parts.append(data["turno"])
    if data.get("documentos") == "Incompletos":
        parts.append("Faltan docs")
    if data.get("experiencia"):
        parts.append(_EXPERIENCIA_CORTA.get(data["experiencia"], data["experiencia"]))
    if data.get("horario"):
        parts.append(f"Entrevista {data['horario']}")
    if data.get("asistencia"):
        parts.append(data["asistencia"])
    lines = [f"📋 **{nombre}**"]
    if parts:
        lines.append(" · ".join(parts))
    if context:
        lines.append(context)
    return "\n".join(lines)
