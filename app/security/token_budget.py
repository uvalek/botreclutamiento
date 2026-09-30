"""Control de consumo de tokens (anti-abuso económico).

Dos niveles de protección:
- **Per-chat**: cada conversación tiene un cupo diario. Si lo agota, el
  bot responde con un mensaje de "límite alcanzado" hasta el reset.
- **Global**: circuit breaker — si el gasto agregado del bot pasa un umbral
  diario, paramos todo hasta el siguiente día (UTC).

Estado in-memory por simplicidad — se pierde al reiniciar el contenedor.
En un entorno multi-réplica habría que mover esto a Redis/Postgres.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from datetime import UTC, datetime

_lock = threading.Lock()
_per_chat: dict[tuple[str, str], int] = defaultdict(int)  # (chat_id, YYYY-MM-DD) -> tokens
_global: dict[str, int] = defaultdict(int)                # YYYY-MM-DD -> tokens


def _today_key() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def record(chat_id: str, tokens: int) -> None:
    """Suma `tokens` al contador del chat y al global."""
    if tokens <= 0 or not chat_id:
        return
    key = _today_key()
    with _lock:
        _per_chat[(chat_id, key)] += tokens
        _global[key] += tokens


def chat_usage(chat_id: str) -> int:
    if not chat_id:
        return 0
    with _lock:
        return _per_chat.get((chat_id, _today_key()), 0)


def global_usage() -> int:
    with _lock:
        return _global.get(_today_key(), 0)


def chat_over_budget(chat_id: str, daily_limit: int) -> bool:
    if daily_limit <= 0:
        return False
    return chat_usage(chat_id) >= daily_limit


def global_over_budget(daily_limit: int) -> bool:
    if daily_limit <= 0:
        return False
    return global_usage() >= daily_limit


# Aproximación cuando el modelo no devuelve usage real (raro pero ocurre con
# tool-calling parcial). 4 caracteres ≈ 1 token en español/inglés.
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)
