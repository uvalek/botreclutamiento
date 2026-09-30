"""Cliente mínimo de Supabase (PostgREST) con httpx.

Se usa la API REST directamente en lugar de supabase-py: la plantilla tuvo
que "endurecer" ese cliente por desconexiones HTTP/2; aquí usamos HTTP/1.1
desde el inicio. Todas las funciones devuelven None ante error: Supabase es
un apoyo (memoria, RAG, contactos, buffer), no un punto único de falla.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

_HTTP: httpx.AsyncClient | None = None


def ready() -> bool:
    return get_settings().supabase_ready


def _client() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None:
        s = get_settings()
        _HTTP = httpx.AsyncClient(
            base_url=f"{s.supabase_url.rstrip('/')}/rest/v1",
            timeout=10.0,
            http2=False,
            headers={
                "apikey": s.supabase_service_key,
                "Authorization": f"Bearer {s.supabase_service_key}",
                "Content-Type": "application/json",
            },
        )
    return _HTTP


async def close() -> None:
    global _HTTP
    if _HTTP is not None:
        await _HTTP.aclose()
        _HTTP = None


async def _call(method: str, path: str, **kw: Any) -> Any:
    if not ready():
        return None
    try:
        r = await _client().request(method, path, **kw)
    except httpx.HTTPError as e:
        log.warning("supabase_unreachable", path=path.split("?")[0], error=str(e)[:160])
        return None
    if r.status_code >= 400:
        log.warning("supabase_error", path=path.split("?")[0], status=r.status_code, body=r.text[:200])
        return None
    if not r.content:
        return []
    try:
        return r.json()
    except ValueError:
        return []


async def insert(table: str, rows: dict[str, Any] | list[dict[str, Any]]) -> list[dict] | None:
    return await _call("POST", f"/{table}", json=rows, headers={"Prefer": "return=representation"})


async def select(table: str, params: dict[str, str]) -> list[dict] | None:
    return await _call("GET", f"/{table}", params=params)


async def update(table: str, match: dict[str, str], values: dict[str, Any]) -> list[dict] | None:
    return await _call(
        "PATCH", f"/{table}", params=match, json=values, headers={"Prefer": "return=representation"}
    )


async def delete(table: str, match: dict[str, str]) -> list[dict] | None:
    return await _call("DELETE", f"/{table}", params=match)


async def rpc(fn: str, args: dict[str, Any]) -> Any:
    return await _call("POST", f"/rpc/{fn}", json=args)
