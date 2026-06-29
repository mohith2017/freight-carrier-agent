from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from freight_agent.db.models import KnowledgeChunk

VECTOR_WEIGHT = 0.55
LEXICAL_WEIGHT = 0.25
METADATA_WEIGHT = 0.20

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_LOAD_RE = re.compile(r"\b\d{8}\b")
_MC_RE = re.compile(r"\bMC\b|\bmc\s*#?\s*\d", re.IGNORECASE)
_STATE_RE = re.compile(r"\b[A-Z]{2}\b")
_DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}\b|"
    r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b",
    re.IGNORECASE,
)


@dataclass
class SearchFilters:
    load_id: str | None = None
    carrier_id: int | None = None
    source_type: str | None = None
    equipment_type: str | None = None


@dataclass
class CommHit:
    chunk_id: int
    event_id: int | None
    source_type: str | None
    source_id: str | None
    carrier_id: int | None
    load_id: str | None
    text: str
    score: float
    components: dict[str, float] = field(default_factory=dict)


def looks_structured(query: str) -> bool:
    q = query or ""
    return bool(
        _LOAD_RE.search(q)
        or _MC_RE.search(q)
        or _DATE_RE.search(q)
        or len(_STATE_RE.findall(q)) >= 2
    )


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def cosine(a: Iterable[float] | None, b: Iterable[float] | None) -> float:
    if a is None or b is None:
        return 0.0
    va = [float(x) for x in a]
    vb = [float(x) for x in b]
    if not va or not vb or len(va) != len(vb):
        return 0.0
    dot = sum(x * y for x, y in zip(va, vb, strict=True))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _lexical(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    hits = query_tokens & _tokens(text)
    return len(hits) / len(query_tokens)


def _metadata_boost(query: str, chunk: KnowledgeChunk, filters: SearchFilters) -> float:
    boost = 0.0
    meta = chunk.meta or {}
    q = query or ""

    for load_id in _LOAD_RE.findall(q):
        if str(meta.get("load_id")) == load_id or load_id in (chunk.text or ""):
            boost += 0.5
            break

    equip = (meta.get("equipment_type") or "").lower()
    if equip and equip in q.lower():
        boost += 0.3

    if filters.carrier_id is not None and meta.get("carrier_id") == filters.carrier_id:
        boost += 0.2
    if filters.load_id and str(meta.get("load_id")) == filters.load_id:
        boost += 0.5
    if filters.equipment_type and equip == filters.equipment_type.lower():
        boost += 0.3

    return min(boost, 1.0)


def _candidates(session: Session, filters: SearchFilters) -> list[KnowledgeChunk]:
    stmt = select(KnowledgeChunk)
    rows = list(session.scalars(stmt))
    out = []
    for c in rows:
        meta = c.meta or {}
        if filters.source_type and c.chunk_type != filters.source_type:
            if meta.get("source_type") != filters.source_type:
                continue
        if filters.load_id and str(meta.get("load_id")) != filters.load_id:
            continue
        if filters.carrier_id is not None and meta.get("carrier_id") != filters.carrier_id:
            continue
        if filters.equipment_type and (meta.get("equipment_type") or "").lower() != (
            filters.equipment_type.lower()
        ):
            continue
        out.append(c)
    return out


def search_communications(
    session: Session,
    query: str,
    query_vector: list[float] | None = None,
    *,
    filters: SearchFilters | None = None,
    limit: int = 5,
) -> list[CommHit]:
    filters = filters or SearchFilters()
    qtokens = _tokens(query)
    candidates = _candidates(session, filters)
    has_vec = query_vector is not None and len(query_vector) > 0

    hits: list[CommHit] = []
    for c in candidates:
        vec = cosine(query_vector, c.embedding) if has_vec else 0.0
        vec01 = (vec + 1.0) / 2.0 if has_vec else 0.0
        lex = _lexical(qtokens, c.text)
        boost = _metadata_boost(query, c, filters)

        if has_vec:
            score = VECTOR_WEIGHT * vec01 + LEXICAL_WEIGHT * lex + METADATA_WEIGHT * boost
        else:
            score = 0.7 * lex + 0.3 * boost

        meta = c.meta or {}
        hits.append(
            CommHit(
                chunk_id=c.chunk_id,
                event_id=c.event_id,
                source_type=meta.get("source_type") or c.chunk_type,
                source_id=meta.get("source_id"),
                carrier_id=meta.get("carrier_id"),
                load_id=meta.get("load_id"),
                text=c.text,
                score=round(score, 4),
                components={
                    "vector": round(vec01, 4),
                    "lexical": round(lex, 4),
                    "metadata": round(boost, 4),
                },
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
