"""Persistencia en SQLite: sesiones, mensajes ya procesados y temporizadores.

Un solo archivo en /data (volumen de EasyPanel) para que sesiones y
temporizadores sobrevivan a un redeploy. Las operaciones son de
microsegundos, así que se llaman directo desde el event loop.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from app.flow.engine import Session

log = structlog.get_logger(__name__)

_SCHEMA = """
create table if not exists sessions (
    conversation_id integer primary key,
    data            text    not null,
    updated_at      real    not null
);
create table if not exists processed_messages (
    message_id integer primary key,
    created_at real not null
);
create table if not exists jobs (
    id                integer primary key autoincrement,
    conversation_id   integer not null,
    kind              text    not null,
    due_at            real    not null,
    state_at_schedule text,
    status            text    not null default 'pending',
    created_at        real    not null
);
create index if not exists jobs_pending_idx on jobs (status, due_at);
create table if not exists buffer (
    message_id      integer primary key,
    conversation_id integer not null,
    data            text    not null,
    created_at      real    not null
);
create index if not exists jobs_conv_idx on jobs (conversation_id, kind, status);
"""


@dataclass
class Job:
    id: int
    conversation_id: int
    kind: str
    due_at: float
    state_at_schedule: str | None


class Store:
    def __init__(self, path: str) -> None:
        self.path = self._open(path)
        self._lock = threading.Lock()

    def _open(self, path: str) -> str:
        """Abre la base; si la ruta no se puede escribir, usa /tmp y avisa."""
        for candidate in (path, "/tmp/reclutamiento.db"):
            try:
                Path(candidate).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(candidate, check_same_thread=False, isolation_level=None)
                conn.execute("pragma journal_mode=wal")
                conn.executescript(_SCHEMA)
                self._conn = conn
                if candidate != path:
                    log.error("db_fallback_tmp", wanted=path, using=candidate)
                return candidate
            except (OSError, sqlite3.Error) as e:
                log.error("db_open_failed", path=candidate, error=str(e))
        raise RuntimeError("no se pudo abrir la base SQLite")

    # --- sesiones ---------------------------------------------------------

    def get_session(self, conversation_id: int) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                "select data from sessions where conversation_id = ?", (conversation_id,)
            ).fetchone()
        if not row:
            return None
        try:
            return Session.from_dict(json.loads(row[0]))
        except (ValueError, TypeError):
            log.warning("session_corrupt", conversation_id=conversation_id)
            return None

    def save_session(self, conversation_id: int, session: Session) -> None:
        data = json.dumps(session.to_dict(), ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                "insert into sessions (conversation_id, data, updated_at) values (?, ?, ?) "
                "on conflict(conversation_id) do update set data = excluded.data, "
                "updated_at = excluded.updated_at",
                (conversation_id, data, time.time()),
            )

    # --- idempotencia -----------------------------------------------------

    def mark_processed(self, message_id: int) -> bool:
        """True si el mensaje es nuevo; False si ya lo habíamos visto."""
        with self._lock:
            cur = self._conn.execute(
                "insert or ignore into processed_messages (message_id, created_at) values (?, ?)",
                (message_id, time.time()),
            )
            return cur.rowcount == 1

    def prune(self, older_than_seconds: float = 3 * 86400) -> None:
        cutoff = time.time() - older_than_seconds
        with self._lock:
            self._conn.execute("delete from processed_messages where created_at < ?", (cutoff,))
            self._conn.execute(
                "delete from jobs where status != 'pending' and created_at < ?", (cutoff,)
            )

    # --- temporizadores ---------------------------------------------------

    def schedule(
        self, conversation_id: int, kind: str, delay_seconds: float, state: str | None
    ) -> None:
        """Programa un temporizador; reemplaza el pendiente del mismo tipo."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "update jobs set status = 'cancelled' "
                "where conversation_id = ? and kind = ? and status = 'pending'",
                (conversation_id, kind),
            )
            self._conn.execute(
                "insert into jobs (conversation_id, kind, due_at, state_at_schedule, created_at) "
                "values (?, ?, ?, ?, ?)",
                (conversation_id, kind, now + delay_seconds, state, now),
            )

    def cancel(self, conversation_id: int, kinds: list[str] | None = None) -> None:
        with self._lock:
            if kinds is None:
                self._conn.execute(
                    "update jobs set status = 'cancelled' "
                    "where conversation_id = ? and status = 'pending'",
                    (conversation_id,),
                )
            else:
                for kind in kinds:
                    self._conn.execute(
                        "update jobs set status = 'cancelled' "
                        "where conversation_id = ? and kind = ? and status = 'pending'",
                        (conversation_id, kind),
                    )

    def take_due(self, now: float | None = None) -> list[Job]:
        """Marca como 'done' y devuelve los temporizadores vencidos."""
        now = now or time.time()
        with self._lock:
            rows = self._conn.execute(
                "select id, conversation_id, kind, due_at, state_at_schedule from jobs "
                "where status = 'pending' and due_at <= ? order by due_at",
                (now,),
            ).fetchall()
            if rows:
                self._conn.executemany(
                    "update jobs set status = 'done' where id = ?", [(r[0],) for r in rows]
                )
        return [Job(*r) for r in rows]

    def pending_jobs(self, conversation_id: int) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "select id, conversation_id, kind, due_at, state_at_schedule from jobs "
                "where conversation_id = ? and status = 'pending' order by due_at",
                (conversation_id,),
            ).fetchall()
        return [Job(*r) for r in rows]


    # --- buffer de entrada (respaldo por si el contenedor reinicia) --------

    def buffer_add(self, message_id: int, conversation_id: int, data: dict) -> None:
        with self._lock:
            self._conn.execute(
                "insert or ignore into buffer (message_id, conversation_id, data, created_at) "
                "values (?, ?, ?, ?)",
                (message_id, conversation_id, json.dumps(data, ensure_ascii=False), time.time()),
            )

    def buffer_remove(self, message_ids: list[int]) -> None:
        if not message_ids:
            return
        with self._lock:
            self._conn.executemany(
                "delete from buffer where message_id = ?", [(i,) for i in message_ids]
            )

    def buffer_leftovers(self) -> dict[int, list[dict]]:
        with self._lock:
            rows = self._conn.execute(
                "select conversation_id, data from buffer order by message_id"
            ).fetchall()
        out: dict[int, list[dict]] = {}
        for conv, data in rows:
            try:
                out.setdefault(conv, []).append(json.loads(data))
            except ValueError:
                continue
        return out


_STORE: Store | None = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        from app.config import get_settings

        _STORE = Store(get_settings().db_path)
    return _STORE


def set_store(store: Store | None) -> None:
    """Para pruebas."""
    global _STORE
    _STORE = store
