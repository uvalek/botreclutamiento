"""M1 · Preguntas: responde dudas con la base de conocimiento (RAG).

Orden: primero las respuestas fijas de `app/vacante.py` (instantáneas y
seguras); si la pregunta no es de esas, busca en la base de conocimiento de
Supabase y la IA responde SOLO con ese contexto. Si no encuentra el dato,
responde que lo confirma Recursos Humanos: nunca inventa.
"""

from __future__ import annotations

import json
from pathlib import Path

from app import llm, rag, vacante
from app.flow import faq
from app.security.prompt_fence import wrap_user_message

_PROMPT = (Path(__file__).parent.parent / "prompts" / "m1_preguntas.md").read_text(encoding="utf-8")

_FACTS = "\n".join(
    [
        f"Empresa: {vacante.EMPRESA} (ficticia, demo de Adlek).",
        f"Puesto: {vacante.PUESTO}, planta Huamantla, Tlaxcala.",
        *vacante.FAQ_RESPUESTAS.values(),
    ]
)


async def answer(question: str, chat_id: str) -> str:
    fixed = faq.answer(question)
    if fixed:
        return fixed
    if not llm.available(chat_id):
        return vacante.FAQ_DESCONOCIDA
    chunks = await rag.retrieve(question)
    context = _FACTS + ("\n\n" + "\n\n".join(chunks) if chunks else "")
    data = await llm.chat_json(
        _PROMPT,
        [
            {
                "role": "user",
                "content": json.dumps(
                    {"contexto": context, "pregunta": wrap_user_message(question)},
                    ensure_ascii=False,
                ),
            }
        ],
        chat_id=chat_id,
        max_tokens=300,
        purpose="m1",
    )
    if not data or not data.get("respuesta"):
        return vacante.FAQ_DESCONOCIDA
    text = str(data["respuesta"]).strip()
    if not data.get("encontrado", True):
        return vacante.FAQ_DESCONOCIDA
    return text[:500]
