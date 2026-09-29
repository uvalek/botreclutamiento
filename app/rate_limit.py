"""Límite de mensajes en memoria (copiado de la plantilla uvalek/chatbot).

Alcance de un solo proceso: suficiente porque el bot corre con 1 réplica.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

# bucket key -> deque of recent timestamps (monotonic seconds)
_BUCKETS: defaultdict[str, deque[float]] = defaultdict(deque)


def hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Registra un evento y dice si está permitido.

    Devuelve (permitido, segundos_para_reintentar).
    """
    if limit <= 0 or window_seconds <= 0:
        return True, 0
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket and now - bucket[0] >= window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        oldest = bucket[0]
        retry_after = max(1, int(window_seconds - (now - oldest)))
        return False, retry_after
    bucket.append(now)
    return True, 0
