"""Preguntas frecuentes: detecta el tema y devuelve la respuesta FIJA.

Las respuestas salen de `app/vacante.py`; aquí solo se decide qué tema
preguntó el candidato. Nunca se genera texto nuevo.
"""

from __future__ import annotations

from app import vacante
from app.flow.matcher import contains_any, normalize

# Orden = orden en que se responden si preguntan varias cosas a la vez.
_TOPICS: dict[str, list[str]] = {
    "sueldo": [
        "sueldo", "sueldos", "salario", "pago", "pagan", "paga", "cuanto pagan",
        "cuanto se gana", "cuanto gano", "cuanto ganan", "cuanto ganaria",
        "cuanto me cae", "bono", "bonos", "semanal", "dinero",
    ],
    "prestaciones": [
        "prestaciones", "prestacion", "beneficios", "beneficio", "comedor", "comida",
        "seguro", "seguro social", "imss", "vacaciones", "aguinaldo", "de ley",
        "infonavit",
    ],
    "transporte": [
        "transporte", "trasporte", "ruta", "rutas", "camion", "camiones", "autobus",
        "pasan por", "me recogen", "recogen", "como llego",
    ],
    "turnos": [
        "turno", "turnos", "horario de trabajo", "horarios de trabajo", "que horario",
        "que horarios", "jornada", "cuantas horas", "rolar", "rolan",
    ],
    "ubicacion": [
        "donde", "en donde", "ubicacion", "direccion", "donde queda", "donde esta",
        "como llegar", "planta",
    ],
    "llevar": [
        "que llevo", "que debo llevar", "que tengo que llevar", "que necesito llevar",
        "que llevar", "requisitos", "papeles", "que necesito", "que debo presentar",
    ],
    "puesto": [
        "que puesto", "cual puesto", "de que es la vacante", "de que se trata",
        "en que consiste", "que vacante", "cual es el trabajo", "de que es el trabajo",
        "actividades", "que haria",
    ],
    "demo": [
        "es real", "es de verdad", "adlek", "eres un bot", "eres bot", "eres robot",
        "eres humano", "eres una persona", "quien eres", "que eres", "es una demo",
        "demo", "ficticia", "ficticio",
    ],
}

_QUESTION_STARTS = (
    "que ", "cual ", "cuales ", "cuanto", "cuanta", "donde", "como ", "cuando",
    "hay ", "dan ", "tienen ", "se puede", "puedo ", "es ", "son ", "me dan",
)


def detect_topics(text: str) -> list[str]:
    n = normalize(text)
    if not n:
        return []
    return [topic for topic, phrases in _TOPICS.items() if contains_any(n, phrases)]


def looks_like_question(text: str) -> bool:
    if "?" in text or "¿" in text:
        return True
    n = normalize(text)
    return n.startswith(_QUESTION_STARTS)


def answer(text: str) -> str | None:
    """Respuesta para una pregunta frecuente, o None si no es una."""
    topics = detect_topics(text)
    if topics:
        return "\n".join(vacante.FAQ_RESPUESTAS[t] for t in topics[:3])
    return None
