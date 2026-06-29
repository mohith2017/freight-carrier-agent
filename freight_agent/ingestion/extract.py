from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

EQUIPMENT_SYNONYMS: dict[str, str] = {
    "box truck": "Box Truck",
    "box": "Box Truck",
    "boxtruck": "Box Truck",
    "sprinter van": "Sprinter Van",
    "sprinter": "Sprinter Van",
    "cargo van": "Sprinter Van",
    "refrigerated": "Refrigerated",
    "reefer": "Refrigerated",
    "fridge": "Refrigerated",
    "flatbed": "Flatbed",
    "flat bed": "Flatbed",
    "flat": "Flatbed",
}

CANONICAL_EQUIPMENT = sorted(set(EQUIPMENT_SYNONYMS.values()))

KNOWN_INTENTS = {"confirm", "terse", "inquiry", "counter", "info", "factoring", "problem"}

_LOAD_REF_RE = re.compile(r"#?\b(\d{8})\b")
_MC_RE = re.compile(r"\bMC\b\D{0,6}?([0-9][0-9\s-]{4,9}[0-9])", re.IGNORECASE)
_MONEY_RE = re.compile(
    r"\$\s?([0-9][0-9,]{1,6}(?:\.[0-9]{1,2})?)|"  # $1,850 / $735 / $2.35
    r"\b([0-9]{1,4}(?:\.[0-9]{1,2})?)\s*(?:/|per\s+)(?:mi|mile|miles)\b",  # 2.35/mile
    re.IGNORECASE,
)
_PER_MILE_HINT = re.compile(r"(?:/|per\s+)(?:mi|mile|miles)", re.IGNORECASE)
_ALL_IN_HINT = re.compile(r"all[\s-]?in|flat|line\s*haul|total", re.IGNORECASE)
_ASK_HINT = re.compile(
    r"can you do|we could do|we can do|agreed on|our floor|floor (?:is|on)|"
    r"counter|asking|need|do\s*\$",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"[^.?!\n]*\?")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_AVAILABLE_POS = re.compile(
    r"\b(available|avail|ready|we're in|we are in|can do|confirmed|interested|"
    r"can cover|good to go)\b",
    re.IGNORECASE,
)
_AVAILABLE_NEG = re.compile(
    r"\b(not available|unavailable|can't|cannot|no longer|booked|pass|decline|"
    r"won't work|doesn't work|not interested)\b",
    re.IGNORECASE,
)


class RateMention(BaseModel):
    amount: float
    rate_type: str = "unknown"
    evidence: str = ""


class ExtractedEvent(BaseModel):
    """Canonical, source-agnostic extraction for one email or call."""

    mc_numbers: list[str] = Field(default_factory=list)
    load_reference: str | None = None
    intent: str | None = None
    quoted_rate_usd: float | None = None
    rate_type: str | None = None
    rate_mentions: list[RateMention] = Field(default_factory=list)
    equipment_type: str | None = None
    available: bool | None = None
    pickup_date: date | None = None
    pickup_window_text: str | None = None
    questions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_notes: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    source: str = "deterministic"
    evidence: dict[str, str] = Field(default_factory=dict)


def normalize_mc(raw: str | None) -> str | None:
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return digits or None


def extract_mc_numbers(text: str) -> list[str]:
    out: list[str] = []
    for m in _MC_RE.finditer(text or ""):
        norm = normalize_mc(m.group(1))
        if norm and norm not in out:
            out.append(norm)
    return out


def extract_load_refs(text: str) -> list[str]:
    out: list[str] = []
    for m in _LOAD_REF_RE.finditer(text or ""):
        ref = m.group(1)
        if ref not in out:
            out.append(ref)
    return out


def extract_equipment(text: str) -> str | None:
    low = (text or "").lower()
    for token in sorted(EQUIPMENT_SYNONYMS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", low):
            return EQUIPMENT_SYNONYMS[token]
    return None


def _classify_rate_type(window: str) -> str:
    if _PER_MILE_HINT.search(window):
        return "per_mile"
    if _ALL_IN_HINT.search(window):
        return "all_in"
    return "unknown"


def extract_rates(text: str) -> list[RateMention]:
    text = text or ""
    mentions: list[RateMention] = []
    for m in _MONEY_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        if raw is None:
            continue
        try:
            amount = float(raw.replace(",", ""))
        except ValueError:
            continue
        if amount <= 0 or amount > 1_000_000:
            continue
        start, end = max(0, m.start() - 25), min(len(text), m.end() + 25)
        window = text[start:end]
        mentions.append(
            RateMention(
                amount=amount,
                rate_type=_classify_rate_type(window),
                evidence=text[m.start():m.end()].strip(),
            )
        )
    return mentions


def _pick_quoted_rate(text: str, mentions: list[RateMention]) -> RateMention | None:
    if not mentions:
        return None
    for mention in mentions:
        idx = text.find(mention.evidence)
        if idx == -1:
            continue
        window = text[max(0, idx - 30): idx + len(mention.evidence) + 5]
        if _ASK_HINT.search(window):
            return mention
    return mentions[-1]


def detect_availability(text: str) -> bool | None:
    """Best-effort availability signal: None when ambiguous."""
    text = text or ""
    neg = bool(_AVAILABLE_NEG.search(text))
    pos = bool(_AVAILABLE_POS.search(text))
    if neg:
        return False
    if pos:
        return True
    return None


def extract_questions(text: str) -> list[str]:
    return [q.strip() for q in _QUESTION_RE.findall(text or "") if q.strip()]


def _first_iso_date(text: str) -> date | None:
    m = _ISO_DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%Y-%m-%d").date()
    except ValueError:
        return None


def deterministic_extract(text: str, *, hint_intent: str | None = None) -> ExtractedEvent:
    text = text or ""
    mc_numbers = extract_mc_numbers(text)
    load_refs = extract_load_refs(text)
    rate_mentions = extract_rates(text)
    quoted = _pick_quoted_rate(text, rate_mentions)
    equipment = extract_equipment(text)
    available = detect_availability(text)

    notes: list[str] = []
    if len(load_refs) > 1:
        notes.append(f"multiple load refs in text: {load_refs}")
    if len(mc_numbers) > 1:
        notes.append(f"multiple MC numbers in text: {mc_numbers}")

    score = 0.0
    score += 0.35 if mc_numbers else 0.0
    score += 0.20 if load_refs else 0.0
    score += 0.20 if quoted else 0.0
    score += 0.15 if equipment else 0.0
    score += 0.10 if available is not None else 0.0

    intent = hint_intent if hint_intent in KNOWN_INTENTS else None

    return ExtractedEvent(
        mc_numbers=mc_numbers,
        load_reference=load_refs[0] if load_refs else None,
        intent=intent,
        quoted_rate_usd=quoted.amount if quoted else None,
        rate_type=quoted.rate_type if quoted else None,
        rate_mentions=rate_mentions,
        equipment_type=equipment,
        available=available,
        pickup_date=_first_iso_date(text),
        questions=extract_questions(text),
        confidence=round(min(score, 1.0), 2),
        confidence_notes=notes,
        needs_human_review=len(load_refs) > 1 or len(mc_numbers) > 1,
        source="deterministic",
        evidence={
            "mc": ", ".join(mc_numbers),
            "rate": quoted.evidence if quoted else "",
            "load_ref": load_refs[0] if load_refs else "",
        },
    )


def _coalesce(primary: Any, secondary: Any) -> Any:
    return primary if primary not in (None, [], "") else secondary


def merge_extractions(det: ExtractedEvent, llm: ExtractedEvent | None) -> ExtractedEvent:
    if llm is None:
        return det

    merged_mc = det.mc_numbers or llm.mc_numbers
    merged = ExtractedEvent(
        mc_numbers=merged_mc,
        load_reference=_coalesce(det.load_reference, llm.load_reference),
        intent=_coalesce(llm.intent, det.intent),
        quoted_rate_usd=_coalesce(det.quoted_rate_usd, llm.quoted_rate_usd),
        rate_type=_coalesce(det.rate_type, llm.rate_type),
        rate_mentions=det.rate_mentions or llm.rate_mentions,
        equipment_type=_coalesce(det.equipment_type, llm.equipment_type),
        available=det.available if det.available is not None else llm.available,
        pickup_date=_coalesce(det.pickup_date, llm.pickup_date),
        pickup_window_text=_coalesce(llm.pickup_window_text, det.pickup_window_text),
        questions=det.questions or llm.questions,
        confidence=round(max(det.confidence, llm.confidence), 2),
        confidence_notes=det.confidence_notes + llm.confidence_notes,
        needs_human_review=det.needs_human_review or llm.needs_human_review,
        source="merged",
        evidence={**llm.evidence, **det.evidence},
    )
    return merged
