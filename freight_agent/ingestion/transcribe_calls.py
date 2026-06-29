from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from freight_agent.db.models import Carrier, CommEvent, Load, Offer
from freight_agent.ingestion.extract import deterministic_extract, merge_extractions
from freight_agent.ingestion.llm import LLMExtractor, Transcriber, Transcript
from freight_agent.ingestion.parse_emails import clear_source

SOURCE_TYPE = "call"


def parse_call_filename(name: str) -> tuple[str, str]:
    stem = Path(name).stem
    parts = stem.split("_")
    call_type = "_".join(parts[2:]) if len(parts) > 2 else "unknown"
    return stem, call_type


def build_glossary(session: Session, *, limit: int = 60) -> str:
    equipment = sorted(
        {e for (e,) in session.execute(select(Load.equipment_type)) if e}
    )
    lanes = sorted(
        {
            f"{o}-{d}"
            for (o, d) in session.execute(
                select(Load.origin_state, Load.destination_state)
            )
            if o and d
        }
    )
    shippers = sorted(
        {s for (s,) in session.execute(select(Load.shipper_name)) if s}
    )[:limit]
    carriers = sorted(
        {c for (c,) in session.execute(select(Carrier.company_name)) if c}
    )[:limit]
    mcs = [m for (m,) in session.execute(select(Carrier.mc_number)) if m][:limit]

    terms = (
        ["Goodlane Logistics", "MC number", "all-in rate", "per mile"]
        + equipment
        + lanes
        + shippers
        + carriers
        + [f"MC {m}" for m in mcs]
    )
    return (
        "Freight broker-carrier phone call. Domain terms and likely entities: "
        + "; ".join(terms)
        + ". Transcribe spoken MC numbers as digits."
    )


def _cache_path(transcripts_dir: Path, stem: str) -> Path:
    return transcripts_dir / f"{stem}.json"


def transcribe_one(
    audio_path: Path,
    transcripts_dir: Path,
    *,
    transcriber: Transcriber | None,
    prompt: str = "",
) -> Transcript:
    stem = audio_path.stem
    cache = _cache_path(transcripts_dir, stem)
    if cache.exists():
        return Transcript.model_validate_json(cache.read_text(encoding="utf-8"))
    if transcriber is None:
        raise RuntimeError(
            f"No cached transcript for {stem} and no transcriber provided. "
            "Run with OPENAI_API_KEY set (uv sync --extra ai) to transcribe."
        )
    transcript = transcriber.transcribe(str(audio_path), prompt=prompt)
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    return transcript


def transcribe_all(
    dataset_dir: Path,
    transcripts_dir: Path,
    *,
    transcriber: Transcriber | None,
    prompt: str = "",
) -> dict[str, Transcript]:
    wavs = sorted((dataset_dir / "call_recordings").glob("*.wav"))
    out: dict[str, Transcript] = {}
    for wav in wavs:
        out[wav.stem] = transcribe_one(
            wav, transcripts_dir, transcriber=transcriber, prompt=prompt
        )
    return out


def ingest_calls(
    session: Session,
    dataset_dir: Path,
    transcripts_dir: Path,
    *,
    transcriber: Transcriber | None = None,
    extractor: LLMExtractor | None = None,
    incremental: bool = False,
) -> dict[str, int]:
    from freight_agent.ingestion.reconcile import closest_mc

    known_mcs = [m for (m,) in session.execute(select(Carrier.mc_number)) if m]
    prompt = build_glossary(session)
    wavs = sorted((dataset_dir / "call_recordings").glob("*.wav"))

    if incremental:
        existing_ids = {
            sid
            for (sid,) in session.query(CommEvent.source_id).filter(
                CommEvent.source_type == SOURCE_TYPE
            )
        }
        wavs = [w for w in wavs if w.stem not in existing_ids]
    else:
        clear_source(session, SOURCE_TYPE)
    n_events = 0
    n_offers = 0
    for wav in wavs:
        stem, call_type = parse_call_filename(wav.name)
        transcript = transcribe_one(
            wav, transcripts_dir, transcriber=transcriber, prompt=prompt
        )
        text = transcript.diarized_text()
        det = deterministic_extract(text)

        # Correct garbled spoken MC numbers against the known roster.
        corrected: list[str] = []
        for mc in det.mc_numbers:
            best, score = closest_mc(mc, known_mcs)
            if best and best != mc and score >= 0.8:
                det.confidence_notes.append(
                    f"corrected spoken MC {mc} -> {best} (score {score:.2f})"
                )
                corrected.append(best)
            else:
                corrected.append(mc)
        det.mc_numbers = corrected

        extracted = det
        if extractor is not None:
            try:
                llm = extractor.extract(text, context=f"Phone call ({call_type})")
                extracted = merge_extractions(det, llm)
            except Exception as exc:  # noqa: BLE001 - degrade to deterministic
                det.confidence_notes.append(f"llm extraction failed: {exc}")

        event = CommEvent(
            source_type=SOURCE_TYPE,
            source_id=stem,
            direction="inbound",
            normalized_text=text,
            extracted=extracted.model_dump(mode="json"),
            confidence=extracted.confidence,
            raw_payload={
                "call_type": call_type,
                "audio_file": wav.name,
                "transcript_model": transcript.model,
                "segments": [s.model_dump() for s in transcript.segments],
            },
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
                intent=extracted.intent or call_type,
                questions=extracted.questions or None,
                offer_status=extracted.intent or call_type,
            )
        )
        n_offers += 1

    session.commit()
    return {"comm_events": n_events, "offers": n_offers}
