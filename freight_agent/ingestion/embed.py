from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from freight_agent.db.models import CommEvent, KnowledgeChunk
from freight_agent.ingestion.llm import Embedder

_DB_WRITE_BATCH = 20
_DB_MAX_RETRIES = 4


@dataclass
class Chunk:
    text: str
    chunk_type: str
    metadata: dict


def _write_chunks(session: Session, rows: list[dict]) -> None:
    for attempt in range(1, _DB_MAX_RETRIES + 1):
        try:
            session.add_all([KnowledgeChunk(**row) for row in rows])
            session.commit()
            return
        except (OperationalError, DBAPIError):
            session.rollback()
            bind = session.get_bind()
            if isinstance(bind, Engine):
                bind.dispose()
            if attempt == _DB_MAX_RETRIES:
                raise
            time.sleep(min(2**attempt, 8))


def chunk_event(event: CommEvent) -> list[Chunk]:
    extracted = event.extracted or {}
    base_meta = {
        "event_id": event.event_id,
        "source_type": event.source_type,
        "source_id": event.source_id,
        "carrier_id": event.carrier_id,
        "load_id": event.load_id,
        "intent": extracted.get("intent"),
        "equipment_type": extracted.get("equipment_type"),
    }

    if event.source_type == "call":
        segments = (event.raw_payload or {}).get("segments") or []
        chunks: list[Chunk] = []
        for i, seg in enumerate(segments):
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            meta = {**base_meta, "segment": i, "speaker": seg.get("speaker")}
            chunks.append(Chunk(text=text, chunk_type="utterance", metadata=meta))
        if chunks:
            return chunks

    text = (event.normalized_text or "").strip()
    if not text:
        return []
    return [Chunk(text=text, chunk_type=event.source_type, metadata=base_meta)]


def embed_events(
    session: Session,
    embedder: Embedder,
    *,
    batch_size: int = 64,
    incremental: bool = False,
) -> dict[str, int]:
    if incremental:
        embedded_event_ids = {
            eid
            for (eid,) in session.query(KnowledgeChunk.event_id).filter(
                KnowledgeChunk.event_id.isnot(None)
            )
        }
        events = [
            e
            for e in session.scalars(select(CommEvent))
            if e.event_id not in embedded_event_ids
        ]
    else:
        session.execute(delete(KnowledgeChunk))
        session.commit()
        events = list(session.scalars(select(CommEvent)))

    all_chunks: list[Chunk] = []
    for event in events:
        all_chunks.extend(chunk_event(event))

    written = 0
    pending: list[dict] = []
    for start in range(0, len(all_chunks), batch_size):
        batch = all_chunks[start: start + batch_size]
        vectors = embedder.embed([c.text for c in batch])
        for chunk, vec in zip(batch, vectors, strict=True):
            pending.append(
                {
                    "event_id": chunk.metadata.get("event_id"),
                    "chunk_type": chunk.chunk_type,
                    "text": chunk.text,
                    "meta": chunk.metadata,
                    "embedding": vec,
                }
            )
        while len(pending) >= _DB_WRITE_BATCH:
            _write_chunks(session, pending[:_DB_WRITE_BATCH])
            written += _DB_WRITE_BATCH
            del pending[:_DB_WRITE_BATCH]

    if pending:
        _write_chunks(session, pending)
        written += len(pending)

    return {"chunks": written, "events": len(events)}
