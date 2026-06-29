from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from freight_agent.db.models import CommEvent, KnowledgeChunk, Offer
from freight_agent.ingestion.extract import deterministic_extract, merge_extractions
from freight_agent.ingestion.llm import LLMExtractor

SOURCE_TYPE = "email"


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def _normalized_text(email: dict) -> str:
    subject = (email.get("subject") or "").strip()
    body = (email.get("body") or "").strip()
    return f"Subject: {subject}\n\n{body}".strip()


def clear_source(session: Session, source_type: str) -> None:
    event_ids = [
        row[0]
        for row in session.query(CommEvent.event_id)
        .filter(CommEvent.source_type == source_type)
        .all()
    ]
    if event_ids:
        session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.event_id.in_(event_ids))
        )
        session.execute(delete(Offer).where(Offer.event_id.in_(event_ids)))
        session.execute(
            delete(CommEvent).where(CommEvent.event_id.in_(event_ids))
        )
    session.commit()


def ingest_emails(
    session: Session,
    dataset_dir: Path,
    *,
    extractor: LLMExtractor | None = None,
    incremental: bool = False,
) -> dict[str, int]:
    emails = json.loads(
        (dataset_dir / "carrier_emails.json").read_text(encoding="utf-8")
    )
    if incremental:
        existing_ids = {
            sid
            for (sid,) in session.query(CommEvent.source_id).filter(
                CommEvent.source_type == SOURCE_TYPE
            )
        }
        emails = [e for e in emails if str(e.get("email_id")) not in existing_ids]
    else:
        clear_source(session, SOURCE_TYPE)

    n_events = 0
    n_offers = 0
    for email in emails:
        text = _normalized_text(email)
        det = deterministic_extract(text, hint_intent=email.get("intent"))
        # Trust the dataset's structured load_reference / equipment when the body
        # is too terse for the regexes to catch them.
        if not det.load_reference and email.get("load_reference"):
            det.load_reference = str(email["load_reference"])
        if not det.equipment_type and email.get("equipment_mentioned"):
            det.equipment_type = email["equipment_mentioned"]

        extracted = det
        if extractor is not None:
            try:
                llm = extractor.extract(text, context="Inbound carrier email")
                extracted = merge_extractions(det, llm)
            except Exception as exc:  # noqa: BLE001 - degrade to deterministic
                det.confidence_notes.append(f"llm extraction failed: {exc}")

        event = CommEvent(
            source_type=SOURCE_TYPE,
            source_id=str(email.get("email_id")),
            occurred_at=_parse_timestamp(email.get("timestamp")),
            direction="inbound",
            normalized_text=text,
            extracted=extracted.model_dump(mode="json"),
            confidence=extracted.confidence,
            raw_payload=email,
        )
        session.add(event)
        session.flush()
        n_events += 1

        session.add(
            Offer(
                event_id=event.event_id,
                quoted_rate_usd=extracted.quoted_rate_usd,
                rate_type=extracted.rate_type,
                availability_date=extracted.pickup_date,
                equipment_type=extracted.equipment_type,
                intent=extracted.intent,
                questions=extracted.questions or None,
                offer_status=extracted.intent or "inquiry",
            )
        )
        n_offers += 1

    session.commit()
    return {"comm_events": n_events, "offers": n_offers}
