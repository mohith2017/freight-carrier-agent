from __future__ import annotations

from dataclasses import dataclass


def flat_to_per_mile(flat_usd: float | None, distance_miles: int | None) -> float | None:
    if not flat_usd or not distance_miles:
        return None
    return round(flat_usd / distance_miles, 4)


@dataclass
class MarketVerdict:
    per_mile: float | None
    avg_rate_per_mile: float | None
    position: str  # "below" | "near" | "above" | "unknown"


def assess_offer(
    flat_usd: float | None,
    distance_miles: int | None,
    avg_rate_per_mile: float | None,
    tolerance: float = 0.10,
) -> MarketVerdict:
    per_mile = flat_to_per_mile(flat_usd, distance_miles)
    if per_mile is None or not avg_rate_per_mile:
        return MarketVerdict(per_mile, avg_rate_per_mile, "unknown")
    lo = avg_rate_per_mile * (1 - tolerance)
    hi = avg_rate_per_mile * (1 + tolerance)
    if per_mile < lo:
        position = "below"
    elif per_mile > hi:
        position = "above"
    else:
        position = "near"
    return MarketVerdict(per_mile, avg_rate_per_mile, position)
