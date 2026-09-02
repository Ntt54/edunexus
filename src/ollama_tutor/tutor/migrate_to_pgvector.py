"""Migrate SQLite chunks+embeddings BLOBs to PostgreSQL + pgvector.

Reads BLOB float32 from SQLite ``chunks.embedding`` and inserts into PG
``chunks(embedding vector(...))``. Reports counts, errors, duration.
Dimension is inferred from first vector; defaults to 384 per plan.

Usage:
    python -m ollama_tutor.tutor.migrate_to_pgvector [--dsn DSN] [--config-dir DIR]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np


def migrate(
    config_dir: Path | None = None,
    dsn: str = "postgresql://postgres:postgres@localhost:5432/edunexus",
    dim: int | None = None,
) -> dict:
    """Run migration, return report dict."""
    from .store import LibraryStore

    if config_dir is None:
        from ..utils.platform import get_config_dir

        config_dir = get_config_dir()
    store = LibraryStore(config_dir)
    # detect dimension from first chunk if not provided
    if dim is None:
        rows = store._conn.execute("SELECT embedding FROM chunks WHERE embedding IS NOT NULL LIMIT 1").fetchone()
        if rows and rows["embedding"]:
            vec = np.frombuffer(rows["embedding"], dtype=np.float32)
            dim = int(vec.shape[0])
        else:
            dim = 384

    # Init PG schema with detected dim
    pg_store = LibraryStore(config_dir, pgvector_enabled=True, pgvector_dsn=dsn, pgvector_dim=dim)
    t0 = time.time()
    pg_store.init_pgvector_schema(dim=dim)

    # Read all chunks
    rows = store._conn.execute(
        "SELECT id, subject_id, book_id, text, chapter, page, section, position, embedding FROM chunks"
    ).fetchall()
    total = len(rows)
    inserted = 0
    errors: list[str] = []
    # batch insert via pg connection
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        return {"total": total, "inserted": 0, "errors": [str(exc)], "duration_s": 0, "dim": dim}

    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            for r in rows:
                try:
                    emb = r["embedding"]
                    if emb is None:
                        vec = None
                    else:
                        vec = np.frombuffer(emb, dtype=np.float32).tolist()
                    cur.execute(
                        "INSERT INTO chunks (id, subject_id, book_id, text, chapter, page, section, position, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding, text = EXCLUDED.text",
                        (
                            r["id"],
                            r["subject_id"],
                            r["book_id"],
                            r["text"],
                            r["chapter"],
                            r["page"],
                            r["section"],
                            r["position"],
                            vec,
                        ),
                    )
                    inserted += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{r['id']}: {exc}")
        conn.commit()
    finally:
        conn.close()
    duration = time.time() - t0
    report = {
        "total": total,
        "inserted": inserted,
        "errors": errors,
        "duration_s": round(duration, 2),
        "dim": dim,
        "dsn": dsn,
    }
    # HNSW index already created in init_pgvector_schema
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate SQLite -> PGVector")
    parser.add_argument("--dsn", default="postgresql://postgres:postgres@localhost:5432/edunexus")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--dim", type=int, default=None, help="vector dimension (auto-detect if omitted)")
    args = parser.parse_args()
    cfg = Path(args.config_dir) if args.config_dir else None
    report = migrate(config_dir=cfg, dsn=args.dsn, dim=args.dim)
    print(f"Migration report: {report}")


if __name__ == "__main__":
    main()
