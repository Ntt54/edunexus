"""PgVector index implementing VectorStore protocol via PostgreSQL + pgvector.

Uses ``psycopg`` with cosine distance operator ``<=>`` and HNSW index.
Falls back gracefully when psycopg is not installed (raises on search).
Dimension is dynamic; default vector(384) but accepts any dimension via DSN table.
"""

from __future__ import annotations

from typing import Any, Sequence


class PgVectorIndex:
    """VectorStore implementation backed by PostgreSQL + pgvector.

    Implements the VectorStore protocol (base.VectorStore) and the VectorIndex
    shape (vector.py). Dimension is not hardcoded; table defaults to vector(384)
    per migration plan but accepts any dimension passed at migration time.
    """

    def __init__(self, dsn: str, subject_id: str, model: str = "") -> None:
        self.dsn = dsn
        self.subject_id = subject_id
        self.model = model

    def _conn(self):
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise RuntimeError("psycopg[binary] not installed") from exc
        return psycopg.connect(self.dsn)

    def add(self, items: Sequence[tuple[Any, Sequence[float]]]) -> None:
        """Insert (id, vector) pairs for the subject (no-op helper)."""
        if not items:
            return
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for cid, vec in items:
                    cur.execute(
                        "INSERT INTO chunks (id, subject_id, book_id, text, embedding) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding",
                        (str(cid), self.subject_id, "", "", list(vec)),
                    )
            conn.commit()
        finally:
            conn.close()

    def search(
        self, query_vector: Sequence[float], k: int, floor: float | None = None
    ) -> list[tuple[Any, float]]:
        """Search via ``ORDER BY embedding <=> %s`` returning cosine similarity.

        Score is ``1 - (embedding <=> query)`` (pgvector cosine distance).
        """
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, 1 - (embedding <=> %s::vector) AS score "
                    "FROM chunks WHERE subject_id = %s AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> %s::vector LIMIT %s",
                    (list(query_vector), self.subject_id, list(query_vector), k),
                )
                rows = cur.fetchall()
                out: list[tuple[Any, float]] = []
                for cid, score in rows:
                    s = float(score) if score is not None else 0.0
                    if floor is not None and s < floor:
                        continue
                    out.append((cid, s))
                return out
        finally:
            conn.close()

    def invalidate(self, subject_id: str | None = None) -> None:
        """No cache to invalidate (PG is authoritative)."""
        return

    # Compatibility alias for VectorIndex protocol expecting subject_id param
    def search_subject(
        self, subject_id: str, query_vec: list[float], k: int, floor: float | None = None
    ):
        self.subject_id = subject_id
        return self.search(query_vec, k, floor=floor)
