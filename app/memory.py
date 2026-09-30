"""Historial de la conversación en Supabase (`n8n_chat_histories`).

Mismo formato LangChain que la plantilla (session_id + message jsonb) para
que se vea igual que el historial de los otros bots. El session_id es
`cw:{conversación}:{prospecto}`: cada "reiniciar" empieza un historial nuevo.
"""

from __future__ import annotations

from app.config import get_settings
from app.tools import supa

TABLE = "n8n_chat_histories"
_ROLE_TO_TYPE = {"user": "human", "assistant": "ai"}
_TYPE_TO_ROLE = {"human": "user", "ai": "assistant"}


async def load(session_id: str, limit: int | None = None) -> list[dict[str, str]]:
    n = limit or get_settings().memory_turns
    rows = await supa.select(
        TABLE,
        {
            "select": "message",
            "session_id": f"eq.{session_id}",
            "order": "id.desc",
            "limit": str(n),
        },
    )
    history: list[dict[str, str]] = []
    for r in reversed(rows or []):
        msg = r.get("message") or {}
        role = _TYPE_TO_ROLE.get(msg.get("type"))
        content = (msg.get("data") or {}).get("content") or ""
        if role and content:
            history.append({"role": role, "content": content})
    return history


async def append(session_id: str, role: str, contents: list[str]) -> None:
    rows = [
        {
            "session_id": session_id,
            "message": {
                "type": _ROLE_TO_TYPE.get(role, "human"),
                "data": {"content": c, "additional_kwargs": {}, "response_metadata": {}},
            },
        }
        for c in contents
        if c
    ]
    if rows:
        await supa.insert(TABLE, rows)
