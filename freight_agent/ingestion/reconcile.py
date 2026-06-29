from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from freight_agent.db.models import Carrier, CommEvent, Load, Offer

GENERIC_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "me.com", "proton.me", "protonmail.com",
}

_NAME_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|"
    r"trucking|transport|transportation|logistics|freight|express|carriers?|"
    r"services?|solutions?|group|enterprises?)\b",
    re.IGNORECASE,
)


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    low = name.lower()
    low = _NAME_SUFFIXES.sub(" ", low)
    low = re.sub(r"[^a-z0-9 ]", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def email_domain(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


def name_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def closest_mc(candidate: str, known_mcs: list[str]) -> tuple[str | None, float]:
    cand = re.sub(r"\D", "", candidate or "")
    if not cand or not known_mcs:
        return None, 0.0
    best: str | None = None
    best_score = 0.0
    for mc in known_mcs:
        norm = re.sub(r"\D", "", mc or "")
        if not norm:
            continue
        score = SequenceMatcher(None, cand, norm).ratio()
        if len(cand) == len(norm) and sorted(cand) == sorted(norm):
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = norm, score
    return best, round(best_score, 3)


@dataclass
class CarrierRef:
    carrier_id: int
    mc_number: str | None
    email: str | None
    company_name: str | None


@dataclass
class CarrierIndex:
    by_mc: dict[str, int] = field(default_factory=dict)
    by_domain: dict[str, int] = field(default_factory=dict)
    refs: list[CarrierRef] = field(default_factory=list)


def build_carrier_index(carriers: list[CarrierRef]) -> CarrierIndex:
    idx = CarrierIndex()
    for c in carriers:
        if c.mc_number:
            norm = re.sub(r"\D", "", c.mc_number)
            if norm:
                idx.by_mc.setdefault(norm, c.carrier_id)
        dom = email_domain(c.email)
        if dom and dom not in GENERIC_DOMAINS:
            idx.by_domain.setdefault(dom, c.carrier_id)
        idx.refs.append(c)
    return idx


@dataclass
class Resolution:
    carrier_id: int | None = None
    method: str = "none"
    score: float = 0.0
    note: str = ""


def resolve_carrier(
    mc_numbers: list[str],
    email: str | None,
    from_name: str | None,
    index: CarrierIndex,
    *,
    fuzzy_threshold: float = 0.82,
) -> Resolution:
    for mc in mc_numbers:
        norm = re.sub(r"\D", "", mc or "")
        if norm in index.by_mc:
            return Resolution(index.by_mc[norm], "mc", 1.0, f"MC {norm}")

    dom = email_domain(email)
    if dom and dom in index.by_domain:
        return Resolution(index.by_domain[dom], "domain", 0.9, f"domain {dom}")

    best_ref: CarrierRef | None = None
    best_score = 0.0
    for ref in index.refs:
        score = name_similarity(from_name, ref.company_name)
        if score > best_score:
            best_ref, best_score = ref, score
    if best_ref and best_score >= fuzzy_threshold:
        return Resolution(
            best_ref.carrier_id,
            "fuzzy_name",
            round(best_score, 3),
            f"~{best_ref.company_name}",
        )
    return Resolution(note="no match")


@dataclass
class ReconcileReport:
    events: int = 0
    carrier_linked: int = 0
    load_linked: int = 0
    by_method: dict[str, int] = field(default_factory=dict)
    cross_channel_carrier_ids: list[int] = field(default_factory=list)


def reconcile(session: Session) -> ReconcileReport:
    carriers = [
        CarrierRef(c.carrier_id, c.mc_number, c.email, c.company_name)
        for c in session.scalars(select(Carrier))
    ]
    index = build_carrier_index(carriers)
    valid_loads = {lid for (lid,) in session.execute(select(Load.load_id))}

    report = ReconcileReport()
    seen_by_channel: dict[int, set[str]] = {}

    events = list(session.scalars(select(CommEvent)))
    for event in events:
        report.events += 1
        extracted = event.extracted or {}
        mc_numbers = extracted.get("mc_numbers") or []
        from_email = (event.raw_payload or {}).get("from_email")
        from_name = (event.raw_payload or {}).get("from_name")

        res = resolve_carrier(mc_numbers, from_email, from_name, index)
        if res.carrier_id is not None:
            event.carrier_id = res.carrier_id
            report.carrier_linked += 1
            seen_by_channel.setdefault(res.carrier_id, set()).add(event.source_type)
        report.by_method[res.method] = report.by_method.get(res.method, 0) + 1

        load_ref = extracted.get("load_reference")
        if load_ref and str(load_ref) in valid_loads:
            event.load_id = str(load_ref)
            report.load_linked += 1

        event.extracted = {
            **extracted,
            "_reconciliation": {
                "carrier_id": res.carrier_id,
                "method": res.method,
                "score": res.score,
                "note": res.note,
            },
        }

    cross = [cid for cid, chans in seen_by_channel.items() if len(chans) > 1]
    report.cross_channel_carrier_ids = sorted(cross)

    for event in events:
        extracted = event.extracted or {}
        recon = extracted.get("_reconciliation", {})
        is_cross = event.carrier_id in cross if event.carrier_id else False
        recon["cross_channel"] = is_cross
        event.extracted = {**extracted, "_reconciliation": recon}
        for offer in session.scalars(
            select(Offer).where(Offer.event_id == event.event_id)
        ):
            offer.carrier_id = event.carrier_id
            offer.load_id = event.load_id

    session.commit()
    return report
