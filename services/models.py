from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalType(str, Enum):
    FRAUD_RISK = "fraud_risk"
    COMPLIANCE = "compliance"
    PAYMENTS = "payments"
    PARTNERSHIP = "partnership"
    EXPANSION = "expansion"
    HIRING = "hiring"
    PRESS = "press"
    NONE = "none"


@dataclass
class Lead:
    company: str
    website: str | None = None
    request_id: str = ""


@dataclass
class PageContent:
    url: str
    title: str = ""
    text: str = ""
    status_code: int | None = None
    error: str | None = None


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass
class PublicSignal:
    summary: str
    source_url: str
    signal_type: SignalType = SignalType.NONE
    source_title: str = ""
    confidence: float = 0.0

    @classmethod
    def none(cls) -> "PublicSignal":
        return cls(
            summary="No recent verifiable public signal found.",
            source_url="",
            signal_type=SignalType.NONE,
            confidence=0.0,
        )


@dataclass
class ResearchContext:
    lead: Lead
    homepage_url: str | None = None
    about_text: str = ""
    search_results: list[SearchResult] = field(default_factory=list)
    public_signal: PublicSignal = field(default_factory=PublicSignal.none)
    errors: list[str] = field(default_factory=list)


@dataclass
class LLMResearchOutput:
    institution_type: str
    customer_segment: str
    services: list[str]
    fraud_angle: str

    @classmethod
    def fallback(cls) -> "LLMResearchOutput":
        return cls(
            institution_type="Unknown financial institution",
            customer_segment="Unknown",
            services=[],
            fraud_angle="Unable to determine a specific fraud/risk angle from grounded public context.",
        )


@dataclass
class EmailDraft:
    email: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class EnrichedLead:
    company: str
    institution_type: str
    fraud_angle: str
    signal: str
    source_url: str
    email: str
    warnings: list[str] = field(default_factory=list)

    def to_csv_row(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "institution_type": self.institution_type,
            "fraud_angle": self.fraud_angle,
            "signal": self.signal,
            "source_url": self.source_url,
            "email": self.email,
        }
