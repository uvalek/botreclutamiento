"""M2 · Redactor: convierte los mensajes del guion en burbujas naturales.

El guion decide QUÉ se dice (datos, pregunta, botones); el redactor decide
CÓMO suena. Salvaguardas: si la IA falla, cambia datos, pierde la pregunta
o se alarga, se usan los textos del guion tal cual.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from app import llm
from app.security import output_guard
from app.security.prompt_fence import wrap_user_message

log = structlog.get_logger(__name__)

_PROMPT = (Path(__file__).parent.parent / "prompts" / "redactor.md").read_text(encoding="utf-8")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MAX_BUBBLES = 3
_MAX_BUBBLE_CHARS = 420


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def required_fragments(base: list[str], extra: list[str]) -> list[str]:
    frags = [m for t in base for m in _BOLD.findall(t)]
    joined = "\n".join(base)
    frags += [e for e in extra if e and e in joined]
    return list(dict.fromkeys(f for f in frags if f.strip()))


def validate(bubbles: object, base: list[str], extra: list[str]) -> list[str] | None:
    if not isinstance(bubbles, list) or not bubbles:
        return None
    out = [str(b).strip() for b in bubbles if isinstance(b, str) and str(b).strip()]
    if not out or len(out) > _MAX_BUBBLES:
        return None
    if any(len(b) > _MAX_BUBBLE_CHARS or b.count("\n") > 3 for b in out):
        return None
    joined = _norm("\n".join(out))
    for frag in required_fragments(base, extra):
        if _norm(frag) not in joined:
            log.info("redactor_missing_fragment", fragment=frag[:40])
            return None
    if base and "?" in base[-1] and "?" not in out[-1]:
        return None
    cleaned, reasons = output_guard.sanitize_chunks(out)
    if reasons:
        return None
    return cleaned


async def rewrite(
    base: list[str],
    options: list[str],
    user_text: str,
    history: list[dict[str, str]],
    must_keep: list[str],
    chat_id: str,
) -> list[str] | None:
    """Burbujas naturales o None (usar las del guion)."""
    if not base:
        return None
    payload = {
        "historial": history[-8:],
        "mensaje_candidato": wrap_user_message(user_text),
        "mensajes_base": base,
        "botones": options,
    }
    data = await llm.chat_json(
        _PROMPT,
        [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        chat_id=chat_id,
        max_tokens=500,
        purpose="redactor",
    )
    if not data:
        return None
    return validate(data.get("burbujas"), base, must_keep)
