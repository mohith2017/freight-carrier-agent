from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


def _blank_to_none(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


def _to_int(v: Any) -> int | None:
    v = _blank_to_none(v)
    if v is None:
        return None
    return int(float(v))


def _to_float(v: Any) -> float | None:
    v = _blank_to_none(v)
    if v is None:
        return None
    return float(v)


def _to_date(v: Any) -> date | None:
    v = _blank_to_none(v)
    if v is None:
        return None
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()


class LoadIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    load_id: str
    origin_city: str | None = None
    origin_state: str | None = None
    origin_zip: str | None = None
    destination_city: str | None = None
    destination_state: str | None = None
    destination_zip: str | None = None
    distance_miles: int | None = None
    equipment_type: str | None = None
    weight_lbs: int | None = None
    pickup_date: date | None = None
    pickup_window: str | None = None
    delivery_date: date | None = None
    offered_rate_usd: float | None = None
    status: str | None = None
    shipper_name: str | None = None
    internal_notes: str | None = None

    @field_validator("distance_miles", "weight_lbs", mode="before")
    @classmethod
    def _ints(cls, v: Any) -> int | None:
        return _to_int(v)

    @field_validator("offered_rate_usd", mode="before")
    @classmethod
    def _floats(cls, v: Any) -> float | None:
        return _to_float(v)

    @field_validator("pickup_date", "delivery_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> date | None:
        return _to_date(v)

    @field_validator(
        "origin_city", "origin_state", "origin_zip", "destination_city",
        "destination_state", "destination_zip", "equipment_type", "pickup_window",
        "status", "shipper_name", "internal_notes", mode="before",
    )
    @classmethod
    def _strs(cls, v: Any) -> Any:
        return _blank_to_none(v)


class RateRowIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    week_start: date | None = None
    origin_state: str | None = None
    destination_state: str | None = None
    equipment_type: str | None = None
    avg_rate_per_mile: float | None = None
    min_rate_per_mile: float | None = None
    max_rate_per_mile: float | None = None
    load_volume: int | None = None

    @field_validator("week_start", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> date | None:
        return _to_date(v)

    @field_validator(
        "avg_rate_per_mile", "min_rate_per_mile", "max_rate_per_mile", mode="before"
    )
    @classmethod
    def _floats(cls, v: Any) -> float | None:
        return _to_float(v)

    @field_validator("load_volume", mode="before")
    @classmethod
    def _ints(cls, v: Any) -> int | None:
        return _to_int(v)


class CarrierIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mc_number: str | None = None
    dot_number: str | None = None
    company_name: str | None = None
    primary_contact: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    equipment_types: list[str] | None = None
    preferred_lanes: list[str] | None = None
    home_base_zip: str | None = None
    factoring_company: str | None = None
    payment_terms_preference: str | None = None
    reliability_score: float | None = None
    loads_completed_with_goodlane: int | None = None
    avg_response_time_hours: float | None = None
    insurance_expiry: date | None = None
    authority_status: str | None = None
    safety_rating: str | None = None
    notes: str | None = None
    onboarded: bool | None = None

    @field_validator("mc_number", "dot_number", mode="before")
    @classmethod
    def _ids_to_str(cls, v: Any) -> str | None:
        v = _blank_to_none(v)
        return None if v is None else str(v).strip()

    @field_validator("insurance_expiry", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> date | None:
        return _to_date(v)

    @field_validator("reliability_score", "avg_response_time_hours", mode="before")
    @classmethod
    def _floats(cls, v: Any) -> float | None:
        return _to_float(v)

    @field_validator("loads_completed_with_goodlane", mode="before")
    @classmethod
    def _ints(cls, v: Any) -> int | None:
        return _to_int(v)
