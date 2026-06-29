from __future__ import annotations

import json
from functools import lru_cache
from typing import Protocol

from pydantic import BaseModel, Field

from freight_agent.config import Settings, get_settings
from freight_agent.ingestion.extract import (
    CANONICAL_EQUIPMENT,
    KNOWN_INTENTS,
    ExtractedEvent,
)


class TranscriptSegment(BaseModel):
    speaker: str | None = None
    text: str = ""
    start: float | None = None
    end: float | None = None


class Transcript(BaseModel):
    text: str = ""
    segments: list[TranscriptSegment] = Field(default_factory=list)
    model: str | None = None

    def diarized_text(self) -> str:
        if not self.segments:
            return self.text
        lines = []
        for seg in self.segments:
            label = seg.speaker or "SPEAKER"
            lines.append(f"{label}: {seg.text}".strip())
        return "\n".join(lines)


class LLMExtractor(Protocol):
    def extract(self, text: str, *, context: str = "") -> ExtractedEvent: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: str, *, prompt: str = "") -> Transcript: ...


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@lru_cache
def _client_for_key(api_key: str):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI SDK not installed. Run: uv sync --extra ai"
        ) from exc
    return OpenAI(api_key=api_key)


def get_client(settings: Settings | None = None):
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment / .env")
    return _client_for_key(settings.openai_api_key)


_EXTRACTION_SYSTEM = (
    "You extract structured freight data from a single carrier communication "
    "(an email or a phone-call transcript). Return STRICT JSON only.\n"
    "Rules:\n"
    "- Prefer null over guessing. Never invent an MC number, load id, or rate.\n"
    "- mc_numbers: digits only, no dashes or 'MC' prefix. If a spoken number is "
    "garbled or self-corrected, return the corrected digits and note it.\n"
    "- quoted_rate_usd is the CARRIER's asked/agreed number, not the broker's "
    "posted rate. rate_type is one of all_in | per_mile | unknown.\n"
    f"- equipment_type must be one of {CANONICAL_EQUIPMENT} or null.\n"
    f"- intent must be one of {sorted(KNOWN_INTENTS)} or null.\n"
    "- available: true/false/null for whether the carrier can take the load.\n"
    "- Set needs_human_review=true on conflicting or low-confidence evidence.\n"
    "JSON schema fields: mc_numbers (string[]), load_reference (string|null), "
    "intent (string|null), quoted_rate_usd (number|null), rate_type (string|null), "
    "equipment_type (string|null), available (bool|null), pickup_date "
    "(YYYY-MM-DD|null), pickup_window_text (string|null), questions (string[]), "
    "confidence (0-1 number), confidence_notes (string[]), "
    "needs_human_review (bool)."
)


class OpenAIExtractor:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def extract(self, text: str, *, context: str = "") -> ExtractedEvent:
        client = get_client(self.settings)
        user = (text if not context else f"Context: {context}\n\nMessage:\n{text}")
        resp = client.chat.completions.create(
            model=self.settings.extraction_model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        payload = json.loads(resp.choices[0].message.content or "{}")
        payload.setdefault("source", "llm")
        return ExtractedEvent.model_validate(
            {k: v for k, v in payload.items() if k in ExtractedEvent.model_fields}
        )


class OpenAITranscriber:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def transcribe(self, audio_path: str, *, prompt: str = "") -> Transcript:
        client = get_client(self.settings)
        model = self.settings.transcribe_model
        is_diarize = "diarize" in model.lower()
        kwargs: dict = {"model": model, "chunking_strategy": "auto"}
        if is_diarize:
            kwargs["response_format"] = "diarized_json"
        else:
            kwargs["response_format"] = "json"
            if prompt:
                kwargs["prompt"] = prompt
        with open(audio_path, "rb") as fh:
            resp = client.audio.transcriptions.create(file=fh, **kwargs)
        return _parse_transcription(resp, model)


def _parse_transcription(resp: object, model: str) -> Transcript:
    data: dict = {}
    if hasattr(resp, "model_dump"):
        data = resp.model_dump()
    elif isinstance(resp, dict):
        data = resp
    text = data.get("text", "") or getattr(resp, "text", "") or ""
    raw_segments = data.get("segments") or data.get("diarized_segments") or []
    segments: list[TranscriptSegment] = []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        segments.append(
            TranscriptSegment(
                speaker=seg.get("speaker") or seg.get("speaker_label"),
                text=(seg.get("text") or "").strip(),
                start=seg.get("start"),
                end=seg.get("end"),
            )
        )
    if not text and segments:
        text = " ".join(s.text for s in segments).strip()
    return Transcript(text=text, segments=segments, model=model)


class OpenAIEmbedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = get_client(self.settings)
        resp = client.embeddings.create(model=self.settings.embed_model, input=texts)
        return [item.embedding for item in resp.data]
