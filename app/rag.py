"""RAG: base de conocimiento de la empresa en Supabase (`documents`).

- Al arrancar, si el archivo `app/knowledge/*.md` cambió (hash distinto al
  guardado), se borran los documentos de esta demo y se vuelven a cargar
  con embeddings de OpenAI. Los documentos de otras demos no se tocan
  (se filtran por metadata `{"demo": "reclutamiento"}`).
- `retrieve()` usa la función existente `match_documents` con ese filtro.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from app import llm
from app.config import get_settings
from app.tools import supa

log = structlog.get_logger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
TABLE = "documents"


def load_chunks() -> tuple[list[dict[str, str]], str]:
    """Parte los .md por secciones "## ". Devuelve (chunks, hash)."""
    chunks: list[dict[str, str]] = []
    digest = hashlib.sha256()
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        digest.update(text.encode())
        title = ""
        current: list[str] = []
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
            elif line.startswith("## "):
                if current:
                    chunks.append({"source": path.stem, "content": "\n".join(current).strip()})
                current = [f"{title} — {line[3:].strip()}"]
            elif current:
                current.append(line)
        if current:
            chunks.append({"source": path.stem, "content": "\n".join(current).strip()})
    return [c for c in chunks if len(c["content"]) > 40], digest.hexdigest()[:16]


async def ensure_ingested() -> str:
    """Carga la base de conocimiento si cambió. Devuelve el estado."""
    s = get_settings()
    if not (supa.ready() and s.openai_api_key):
        return "skipped"
    chunks, digest = load_chunks()
    demo = s.demo_key
    current = await supa.select(
        TABLE,
        {"select": "id", "metadata->>demo": f"eq.{demo}", "metadata->>hash": f"eq.{digest}", "limit": "1"},
    )
    if current is None:
        return "supabase_error"
    if current:
        return "up_to_date"
    vectors = await llm.embed([c["content"] for c in chunks])
    if not vectors or len(vectors) != len(chunks):
        return "embed_failed"
    await supa.delete(TABLE, {"metadata->>demo": f"eq.{demo}"})
    rows = [
        {
            "content": c["content"],
            "metadata": {"demo": demo, "source": c["source"], "hash": digest},
            "embedding": v,
        }
        for c, v in zip(chunks, vectors, strict=True)
    ]
    inserted = await supa.insert(TABLE, rows)
    status = f"ingested:{len(inserted or [])}"
    log.info("rag_ingest", status=status)
    return status


async def retrieve(question: str, k: int = 4, min_similarity: float = 0.2) -> list[str]:
    """Fragmentos relevantes de la base de conocimiento (puede ser [])."""
    if not supa.ready() or not question.strip():
        return []
    vectors = await llm.embed([question])
    if not vectors:
        return []
    rows = await supa.rpc(
        "match_documents",
        {"query_embedding": vectors[0], "match_count": k, "filter": {"demo": get_settings().demo_key}},
    )
    out: list[str] = []
    for r in rows or []:
        if (r.get("similarity") or 0) >= min_similarity and r.get("content"):
            out.append(r["content"])
    return out
