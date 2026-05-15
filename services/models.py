from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SignalType(str, Enum):
    FRAUD_RISK = "fraud_risk"
    COMPLIANCE = "compliance"
    PAYMENTS = "payments"
    PARTNERSHIP = "partnership"
    EXPANSION = "expansion"
    HIRING = "hiring"
    PRESS = "press"
    NONE = "none"


# Lead = one valid input row from the CSV.
@dataclass
class Lead:
    company: str
    website: Optional[str] = None
    request_id: str = ""


# PageContent = one scraped website page after HTML is cleaned into readable text.
@dataclass
class PageContent:
    url: str
    title: str = ""
    text: str = ""
    status_code: Optional[int] = None
    error: Optional[str] = None


# SearchResult = one DuckDuckGo result before we decide whether it is useful.
@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""


# PublicSignal = the one source-backed public signal selected for outreach.
@dataclass
class PublicSignal:
    summary: str
    source_url: str
    signal_type: SignalType = SignalType.NONE
    source_title: str = ""
    confidence: float = 0.0

    @classmethod
    def none(cls):
        # Means no verified public source was found. We use this instead of guessing.
        return cls(
            summary="No recent verifiable public signal found.",
            source_url="",
            signal_type=SignalType.NONE,
            confidence=0.0,
        )


# ResearchContext = all factual context collected for one company.
@dataclass
class ResearchContext:
    lead: Lead
    homepage_url: Optional[str] = None
    about_text: str = ""
    search_results: List[SearchResult] = field(default_factory=list)
    public_signal: PublicSignal = field(default_factory=PublicSignal.none)
    errors: List[str] = field(default_factory=list)


# LLMResearchOutput = company classification returned by Gemini.
@dataclass
class LLMResearchOutput:
    institution_type: str
    customer_segment: str
    services: List[str]
    fraud_angle: str

    @classmethod
    def fallback(cls):
        # Means Gemini failed or the grounded context was not enough to classify safely.
        return cls(
            institution_type="Unknown financial institution",
            customer_segment="Unknown",
            services=[],
            fraud_angle="Unable to determine a specific fraud/risk angle from grounded public context.",
        )


# EmailDraft = generated email plus any warnings from fallback or validation.
@dataclass
class EmailDraft:
    email: str
    warnings: List[str] = field(default_factory=list)


# EnrichedLead = final row written to the output CSV.
@dataclass
class EnrichedLead:
    company: str
    institution_type: str
    fraud_angle: str
    signal: str
    source_url: str
    email: str

    def to_csv_row(self) -> Dict[str, Any]:
        return {
            "company": self.company,
            "institution_type": self.institution_type,
            "fraud_angle": self.fraud_angle,
            "signal": self.signal,
            "source_url": self.source_url,
            "email": self.email,
        }
