from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    types,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class VectorType(types.TypeDecorator):
    impl = JSON
    cache_ok = True

    def __init__(self, dim: int = 1536, *args, **kwargs) -> None:
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector

                return dialect.type_descriptor(Vector(self.dim))
            except ImportError:
                return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(JSON())


class Carrier(Base):
    __tablename__ = "carriers"

    carrier_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mc_number: Mapped[str | None] = mapped_column(String, index=True)
    dot_number: Mapped[str | None] = mapped_column(String)
    company_name: Mapped[str | None] = mapped_column(String, index=True)
    primary_contact: Mapped[str | None] = mapped_column(String)
    email: Mapped[str | None] = mapped_column(String, index=True)
    phone: Mapped[str | None] = mapped_column(String)
    address: Mapped[str | None] = mapped_column(String)
    equipment_types: Mapped[list | None] = mapped_column(JSON)
    preferred_lanes: Mapped[list | None] = mapped_column(JSON)
    home_base_zip: Mapped[str | None] = mapped_column(String)
    factoring_company: Mapped[str | None] = mapped_column(String)
    payment_terms_preference: Mapped[str | None] = mapped_column(String)
    reliability_score: Mapped[float | None] = mapped_column(Float)
    loads_completed_with_goodlane: Mapped[int | None] = mapped_column(Integer)
    avg_response_time_hours: Mapped[float | None] = mapped_column(Float)
    insurance_expiry: Mapped[date | None] = mapped_column(Date)
    authority_status: Mapped[str | None] = mapped_column(String, index=True)
    safety_rating: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    onboarded: Mapped[bool | None] = mapped_column(types.Boolean)
    profile_raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Load(Base):
    __tablename__ = "loads"

    load_id: Mapped[str] = mapped_column(String, primary_key=True)
    origin_city: Mapped[str | None] = mapped_column(String)
    origin_state: Mapped[str | None] = mapped_column(String, index=True)
    origin_zip: Mapped[str | None] = mapped_column(String)
    destination_city: Mapped[str | None] = mapped_column(String)
    destination_state: Mapped[str | None] = mapped_column(String, index=True)
    destination_zip: Mapped[str | None] = mapped_column(String)
    distance_miles: Mapped[int | None] = mapped_column(Integer)
    equipment_type: Mapped[str | None] = mapped_column(String, index=True)
    weight_lbs: Mapped[int | None] = mapped_column(Integer)
    pickup_date: Mapped[date | None] = mapped_column(Date)
    pickup_window: Mapped[str | None] = mapped_column(String)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    offered_rate_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String, index=True)
    shipper_name: Mapped[str | None] = mapped_column(String)
    internal_notes: Mapped[str | None] = mapped_column(Text)
    load_raw: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RateHistory(Base):
    __tablename__ = "rate_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_start: Mapped[date | None] = mapped_column(Date)
    origin_state: Mapped[str | None] = mapped_column(String)
    destination_state: Mapped[str | None] = mapped_column(String)
    equipment_type: Mapped[str | None] = mapped_column(String)
    avg_rate_per_mile: Mapped[float | None] = mapped_column(Float)
    min_rate_per_mile: Mapped[float | None] = mapped_column(Float)
    max_rate_per_mile: Mapped[float | None] = mapped_column(Float)
    load_volume: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_rate_lane", "origin_state", "destination_state", "equipment_type"),
    )


class CommEvent(Base):
    __tablename__ = "comm_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime)
    carrier_id: Mapped[int | None] = mapped_column(ForeignKey("carriers.carrier_id"), index=True)
    load_id: Mapped[str | None] = mapped_column(ForeignKey("loads.load_id"), index=True)
    direction: Mapped[str] = mapped_column(String, default="inbound")
    normalized_text: Mapped[str | None] = mapped_column(Text)
    extracted: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    offers: Mapped[list[Offer]] = relationship(back_populates="event")
    chunks: Mapped[list[KnowledgeChunk]] = relationship(back_populates="event")


class Offer(Base):
    __tablename__ = "offers"

    offer_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("comm_events.event_id"), index=True)
    carrier_id: Mapped[int | None] = mapped_column(ForeignKey("carriers.carrier_id"), index=True)
    load_id: Mapped[str | None] = mapped_column(ForeignKey("loads.load_id"), index=True)
    quoted_rate_usd: Mapped[float | None] = mapped_column(Float)
    rate_type: Mapped[str | None] = mapped_column(String)  # all_in | per_mile | unknown
    availability_date: Mapped[date | None] = mapped_column(Date)
    equipment_type: Mapped[str | None] = mapped_column(String)
    intent: Mapped[str | None] = mapped_column(String)
    questions: Mapped[list | None] = mapped_column(JSON)
    offer_status: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped[CommEvent] = relationship(back_populates="offers")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    chunk_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("comm_events.event_id"), index=True)
    chunk_type: Mapped[str | None] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict | None] = mapped_column("metadata", JSON)
    embedding: Mapped[list | None] = mapped_column(VectorType(1536))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    event: Mapped[CommEvent | None] = relationship(back_populates="chunks")
