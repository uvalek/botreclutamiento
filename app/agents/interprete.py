"""Router / intérprete: entiende texto libre y lo convierte en datos del guion.

Equivale al ROUTER de la plantilla, pero en lugar de solo elegir módulo,
extrae: la respuesta al paso actual, datos extra que el candidato dio de una
vez, si hizo una pregunta (→ M1) y si pide asesor. El guion valida todo lo
que devuelve; si la IA falla, el guion trabaja solo (como la v1).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app import llm
from app.flow import engine as E
from app.security.prompt_fence import wrap_user_message

_PROMPT = (Path(__file__).parent.parent / "prompts" / "interprete.md").read_text(encoding="utf-8")

_ORDER = ["NOMBRE", "EDAD", "MUNICIPIO", "TURNO", "DOCUMENTOS", "EXPERIENCIA"]
_OPTIONS_BY_FIELD = {
    "edad": ["18–24", "25–35", "36–45", "46 o más"],
    "municipio": ["Huamantla", "Apizaco", "Tlaxcala", "Otro (escribe el nombre)"],
    "turno": ["Matutino", "Vespertino", "Nocturno", "Cualquiera"],
    "documentos": ["Sí, todos", "Me falta alguno"],
    "experiencia": ["Más de 6 meses", "Menos de 6 meses", "Sin experiencia"],
}


@dataclass
class Interpretation:
    respuesta: str | None = None
    extras: dict[str, str] = field(default_factory=dict)
    pregunta: str | None = None
    asesor: bool = False


def _pending_fields(state: str) -> list[dict[str, object]]:
    if state == "BIENVENIDA" or state == "INFO":
        rest = _ORDER
    elif state in _ORDER:
        rest = _ORDER[_ORDER.index(state) + 1 :]
    else:
        return []
    out = []
    for st in rest:
        f = E.FIELD_BY_STATE[st]
        out.append({"campo": f, "opciones": _OPTIONS_BY_FIELD.get(f, "texto libre")})
    return out


async def interpret(
    s: E.Session, text: str, conf: E.Conf, history: list[dict[str, str]], chat_id: str
) -> Interpretation | None:
    question, options = E.current_question(s, conf)
    payload = {
        "paso_actual": {
            "paso": s.state,
            "pregunta": question,
            "opciones": options or "texto libre",
        },
        "pasos_siguientes": _pending_fields(s.state),
        "historial": history[-6:],
        "mensaje": wrap_user_message(text),
    }
    data = await llm.chat_json(
        _PROMPT,
        [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        chat_id=chat_id,
        max_tokens=400,
        purpose="interprete",
    )
    if data is None:
        return None
    extras_raw = data.get("datos_extra") or {}
    extras = {
        k: str(v).strip()
        for k, v in (extras_raw.items() if isinstance(extras_raw, dict) else [])
        if k in _OPTIONS_BY_FIELD or k == "nombre"
        if v not in (None, "", "null")
    }
    resp = data.get("respuesta_paso")
    preg = data.get("pregunta")
    return Interpretation(
        respuesta=str(resp).strip() if resp not in (None, "", "null") else None,
        extras=extras,
        pregunta=str(preg).strip() if preg not in (None, "", "null") else None,
        asesor=bool(data.get("quiere_asesor")),
    )
