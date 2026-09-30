"""Llamadas a OpenAI con límite de tiempo y red de seguridad.

`completion_params` viene de la plantilla (uvalek/chatbot): los modelos de
razonamiento (gpt-5.x) no aceptan `temperature`, usan
`max_completion_tokens` y aceptan `reasoning_effort`.

Regla de oro: si la IA falla, tarda o devuelve algo raro, las funciones de
aquí devuelven None y el bot usa sus textos fijos. Nunca se queda mudo.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import structlog
from openai import AsyncOpenAI

from app.config import get_settings
from app.security import token_budget

log = structlog.get_logger(__name__)

_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
_CLIENT: AsyncOpenAI | None = None


def is_reasoning_model(model: str | None) -> bool:
    return (model or "").lower().startswith(_REASONING_PREFIXES)


def completion_params(
    model: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    s = get_settings()
    params: dict[str, Any] = {}
    if is_reasoning_model(model):
        effort = reasoning_effort or s.openai_reasoning_effort
        if effort:
            params["reasoning_effort"] = effort
        if max_tokens is not None:
            params["max_completion_tokens"] = max(max_tokens, s.openai_reasoning_max_tokens_floor)
    else:
        if temperature is not None:
            params["temperature"] = temperature
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
    return params


def client() -> AsyncOpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=1)
    return _CLIENT


def set_client(c: Any) -> None:
    """Para pruebas."""
    global _CLIENT
    _CLIENT = c


def available(chat_id: str = "") -> bool:
    """¿Se puede usar la IA ahora? (llave, interruptor y presupuesto)."""
    s = get_settings()
    if not s.ai_ready:
        return False
    if token_budget.global_over_budget(s.global_token_budget_per_day):
        log.warning("ai_global_budget_exhausted")
        return False
    if chat_id and token_budget.chat_over_budget(chat_id, s.chat_token_budget_per_day):
        log.warning("ai_chat_budget_exhausted")
        return False
    return True


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)


def parse_json(text: str) -> Any | None:
    cleaned = _FENCE.sub("", (text or "").strip()).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        m = re.search(r"\{.*\}", cleaned, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                return None
    return None


async def chat_json(
    system: str,
    messages: list[dict[str, str]],
    *,
    chat_id: str = "",
    max_tokens: int = 600,
    purpose: str = "llm",
) -> dict[str, Any] | None:
    """Pide al modelo un objeto JSON. None si algo falla."""
    if not available(chat_id):
        return None
    s = get_settings()
    msgs = [{"role": "system", "content": system}, *messages]
    try:
        resp = await asyncio.wait_for(
            client().chat.completions.create(
                model=s.openai_model,
                messages=msgs,  # type: ignore[arg-type]
                response_format={"type": "json_object"},
                **completion_params(s.openai_model, temperature=0.4, max_tokens=max_tokens),
            ),
            timeout=s.llm_timeout_seconds,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("llm_failed", purpose=purpose, error=str(e)[:200])
        return None
    try:
        usage = getattr(resp, "usage", None)
        if usage and chat_id:
            token_budget.record(chat_id, int(getattr(usage, "total_tokens", 0) or 0))
    except Exception:  # noqa: BLE001
        pass
    content = (resp.choices[0].message.content or "") if resp.choices else ""
    data = parse_json(content)
    if not isinstance(data, dict):
        log.warning("llm_bad_json", purpose=purpose, preview=content[:120])
        return None
    return data


async def embed(texts: list[str]) -> list[list[float]] | None:
    s = get_settings()
    if not s.openai_api_key or not texts:
        return None
    try:
        resp = await asyncio.wait_for(
            client().embeddings.create(model=s.openai_embedding_model, input=texts),
            timeout=max(20.0, s.llm_timeout_seconds),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("embed_failed", error=str(e)[:200])
        return None
    return [d.embedding for d in resp.data]
