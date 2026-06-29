from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from freight_agent.db.models import Carrier, CommEvent, Load, Offer, RateHistory
from freight_agent.ingestion.extract import extract_equipment
from freight_agent.ingestion.reconcile import name_similarity
from freight_agent.rates import assess_offer, flat_to_per_mile
from freight_agent.retrieval import CommHit, SearchFilters, search_communications


class LoadInfo(BaseModel):
    load_id: str
    lane: str
    origin_state: str | None = None
    destination_state: str | None = None
    equipment_type: str | None = None
    distance_miles: int | None = None
    offered_rate_usd: float | None = None
    offered_rate_per_mile: float | None = None
    status: str | None = None
    pickup_date: date | None = None
    pickup_window: str | None = None
    weight_lbs: int | None = None
    shipper_name: str | None = None


class CarrierInfo(BaseModel):
    carrier_id: int
    mc_number: str | None = None
    company_name: str | None = None
    primary_contact: str | None = None
    email: str | None = None
    phone: str | None = None
    equipment_types: list[str] = Field(default_factory=list)
    authority_status: str | None = None
    insurance_expiry: date | None = None
    reliability_score: float | None = None
    loads_completed_with_goodlane: int | None = None
    match_method: str = "exact"
    compliance_flags: list[str] = Field(default_factory=list)


class CarrierHistory(BaseModel):
    carrier_id: int
    company_name: str | None = None
    loads_completed_with_goodlane: int | None = None
    reliability_score: float | None = None
    recent_events: list[str] = Field(default_factory=list)
    offer_count: int = 0


class RateContext(BaseModel):
    lane: str
    equipment_type: str | None = None
    avg_rate_per_mile: float | None = None
    min_rate_per_mile: float | None = None
    max_rate_per_mile: float | None = None
    weeks_of_data: int = 0
    total_load_volume: int | None = None
    quoted_per_mile: float | None = None
    market_position: str | None = None


class OfferInfo(BaseModel):
    offer_id: int
    load_id: str | None = None
    carrier_id: int | None = None
    company_name: str | None = None
    quoted_rate_usd: float | None = None
    rate_type: str | None = None
    quoted_per_mile: float | None = None
    market_position: str | None = None
    intent: str | None = None
    source_event_id: int | None = None


class CarrierAvailability(BaseModel):
    carrier_id: int
    company_name: str | None = None
    load_id: str | None = None
    equipment_type: str | None = None
    availability_date: date | None = None
    intent: str | None = None
    authority_status: str | None = None
    compliance_flags: list[str] = Field(default_factory=list)


def compliance_flags(carrier: Carrier, *, as_of: date | None = None) -> list[str]:
    today = as_of or date.today()
    flags: list[str] = []
    status = (carrier.authority_status or "").upper()
    if status != "ACTIVE":
        flags.append(f"authority_status={carrier.authority_status or 'UNKNOWN'}")
    if carrier.insurance_expiry is None:
        flags.append("insurance_expiry=missing")
    elif carrier.insurance_expiry < today:
        flags.append(f"insurance_expired={carrier.insurance_expiry.isoformat()}")
    if carrier.onboarded is False:
        flags.append("not_onboarded")
    return flags


def get_load(session: Session, load_id: str) -> LoadInfo | None:
    load = session.get(Load, str(load_id).strip().lstrip("#"))
    if load is None:
        return None
    lane = f"{load.origin_state}->{load.destination_state}"
    return LoadInfo(
        load_id=load.load_id,
        lane=lane,
        origin_state=load.origin_state,
        destination_state=load.destination_state,
        equipment_type=load.equipment_type,
        distance_miles=load.distance_miles,
        offered_rate_usd=load.offered_rate_usd,
        offered_rate_per_mile=flat_to_per_mile(load.offered_rate_usd, load.distance_miles),
        status=load.status,
        pickup_date=load.pickup_date,
        pickup_window=load.pickup_window,
        weight_lbs=load.weight_lbs,
        shipper_name=load.shipper_name,
    )


def _to_info(carrier: Carrier, method: str) -> CarrierInfo:
    return CarrierInfo(
        carrier_id=carrier.carrier_id,
        mc_number=carrier.mc_number,
        company_name=carrier.company_name,
        primary_contact=carrier.primary_contact,
        email=carrier.email,
        phone=carrier.phone,
        equipment_types=list(carrier.equipment_types or []),
        authority_status=carrier.authority_status,
        insurance_expiry=carrier.insurance_expiry,
        reliability_score=carrier.reliability_score,
        loads_completed_with_goodlane=carrier.loads_completed_with_goodlane,
        match_method=method,
        compliance_flags=compliance_flags(carrier),
    )


def resolve_carrier(session: Session, query: str) -> CarrierInfo | None:
    q = (query or "").strip()
    if not q:
        return None

    digits = re.sub(r"\D", "", q)
    if digits:
        by_mc = session.scalars(
            select(Carrier).where(Carrier.mc_number == digits)
        ).first()
        if by_mc:
            return _to_info(by_mc, "mc")
        by_phone = session.scalars(
            select(Carrier).where(Carrier.phone.isnot(None))
        ).all()
        for c in by_phone:
            if re.sub(r"\D", "", c.phone or "") == digits and len(digits) >= 7:
                return _to_info(c, "phone")

    if "@" in q:
        by_email = session.scalars(
            select(Carrier).where(Carrier.email.ilike(q))
        ).first()
        if by_email:
            return _to_info(by_email, "email")

    best: Carrier | None = None
    best_score = 0.0
    for c in session.scalars(select(Carrier)):
        score = name_similarity(q, c.company_name)
        if score > best_score:
            best, best_score = c, score
    if best and best_score >= 0.82:
        info = _to_info(best, "fuzzy_name")
        return info
    return None


def get_carrier_history(session: Session, carrier_id: int) -> CarrierHistory | None:
    carrier = session.get(Carrier, carrier_id)
    if carrier is None:
        return None
    events = session.scalars(
        select(CommEvent)
        .where(CommEvent.carrier_id == carrier_id)
        .order_by(CommEvent.event_id.desc())
        .limit(5)
    ).all()
    n_offers = session.scalar(
        select(func.count()).select_from(Offer).where(Offer.carrier_id == carrier_id)
    )
    recent = [
        f"{e.source_type}:{e.source_id} {(e.normalized_text or '')[:80]}".strip()
        for e in events
    ]
    return CarrierHistory(
        carrier_id=carrier_id,
        company_name=carrier.company_name,
        loads_completed_with_goodlane=carrier.loads_completed_with_goodlane,
        reliability_score=carrier.reliability_score,
        recent_events=recent,
        offer_count=n_offers or 0,
    )


def _canon_equip(equipment: str | None) -> str | None:
    if not equipment:
        return None
    return extract_equipment(equipment) or equipment


def get_rate_context(
    session: Session,
    origin_state: str,
    destination_state: str,
    equipment_type: str | None = None,
    *,
    flat_usd: float | None = None,
    distance_miles: int | None = None,
) -> RateContext:
    equip = _canon_equip(equipment_type)
    lane = f"{origin_state}->{destination_state}"
    stmt = select(RateHistory).where(
        RateHistory.origin_state == origin_state,
        RateHistory.destination_state == destination_state,
    )
    if equip:
        stmt = stmt.where(RateHistory.equipment_type == equip)
    rows = session.scalars(stmt).all()

    avgs = [r.avg_rate_per_mile for r in rows if r.avg_rate_per_mile is not None]
    mins = [r.min_rate_per_mile for r in rows if r.min_rate_per_mile is not None]
    maxs = [r.max_rate_per_mile for r in rows if r.max_rate_per_mile is not None]
    vols = [r.load_volume for r in rows if r.load_volume is not None]

    avg = round(sum(avgs) / len(avgs), 4) if avgs else None
    ctx = RateContext(
        lane=lane,
        equipment_type=equip,
        avg_rate_per_mile=avg,
        min_rate_per_mile=min(mins) if mins else None,
        max_rate_per_mile=max(maxs) if maxs else None,
        weeks_of_data=len(rows),
        total_load_volume=sum(vols) if vols else None,
    )
    if flat_usd and distance_miles:
        verdict = assess_offer(flat_usd, distance_miles, avg)
        ctx.quoted_per_mile = verdict.per_mile
        ctx.market_position = verdict.position
    return ctx


def search_comms(
    session: Session,
    query: str,
    query_vector: list[float] | None = None,
    *,
    load_id: str | None = None,
    carrier_id: int | None = None,
    source_type: str | None = None,
    equipment_type: str | None = None,
    limit: int = 5,
) -> list[CommHit]:
    filters = SearchFilters(
        load_id=load_id,
        carrier_id=carrier_id,
        source_type=source_type,
        equipment_type=_canon_equip(equipment_type),
    )
    return search_communications(
        session, query, query_vector, filters=filters, limit=limit
    )


def best_offer_for_load(session: Session, load_id: str) -> OfferInfo | None:
    lid = str(load_id).strip().lstrip("#")
    offers = session.scalars(
        select(Offer).where(
            Offer.load_id == lid, Offer.quoted_rate_usd.isnot(None)
        )
    ).all()
    if not offers:
        return None
    best = min(offers, key=lambda o: o.quoted_rate_usd or float("inf"))

    load = session.get(Load, lid)
    per_mile = None
    position = None
    if load and best.rate_type != "per_mile":
        per_mile = flat_to_per_mile(best.quoted_rate_usd, load.distance_miles)
        ctx = get_rate_context(
            session,
            load.origin_state or "",
            load.destination_state or "",
            load.equipment_type,
            flat_usd=best.quoted_rate_usd,
            distance_miles=load.distance_miles,
        )
        position = ctx.market_position

    company = None
    if best.carrier_id is not None:
        carrier = session.get(Carrier, best.carrier_id)
        company = carrier.company_name if carrier else None

    return OfferInfo(
        offer_id=best.offer_id,
        load_id=best.load_id,
        carrier_id=best.carrier_id,
        company_name=company,
        quoted_rate_usd=best.quoted_rate_usd,
        rate_type=best.rate_type,
        quoted_per_mile=per_mile,
        market_position=position,
        intent=best.intent,
        source_event_id=best.event_id,
    )


def carriers_available_for_lane(
    session: Session,
    origin_state: str,
    destination_state: str,
    equipment_type: str | None = None,
    *,
    limit: int = 20,
) -> list[CarrierAvailability]:
    equip = _canon_equip(equipment_type)
    load_stmt = select(Load.load_id).where(
        Load.origin_state == origin_state,
        Load.destination_state == destination_state,
    )
    if equip:
        load_stmt = load_stmt.where(Load.equipment_type == equip)
    load_ids = {lid for (lid,) in session.execute(load_stmt)}
    if not load_ids:
        return []

    offers = session.scalars(
        select(Offer).where(
            Offer.load_id.in_(load_ids),
            Offer.carrier_id.isnot(None),
            or_(
                Offer.availability_date.isnot(None),
                Offer.intent.in_(["confirm", "inquiry", "counter"]),
            ),
        )
    ).all()

    seen: set[tuple[int, str | None]] = set()
    out: list[CarrierAvailability] = []
    for o in offers:
        if o.carrier_id is None:
            continue
        key = (o.carrier_id, o.load_id)
        if key in seen:
            continue
        seen.add(key)
        carrier = session.get(Carrier, o.carrier_id)
        out.append(
            CarrierAvailability(
                carrier_id=o.carrier_id,
                company_name=carrier.company_name if carrier else None,
                load_id=o.load_id,
                equipment_type=o.equipment_type or equip,
                availability_date=o.availability_date,
                intent=o.intent,
                authority_status=carrier.authority_status if carrier else None,
                compliance_flags=compliance_flags(carrier) if carrier else [],
            )
        )
        if len(out) >= limit:
            break
    return out
