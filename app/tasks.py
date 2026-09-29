"""Tareas en segundo plano con referencia fuerte (patrón de la plantilla).

Sin guardar la referencia, el recolector de basura de Python puede matar un
`asyncio.create_task(...)` a media ejecución.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_BG_TASKS: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


def pending_count() -> int:
    return len(_BG_TASKS)
