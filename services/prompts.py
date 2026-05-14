from __future__ import annotations

import json

from services.models import ResearchContext


def research_prompt(context: ResearchContext) -> str:
    payload = {
        "company": context.lead.company,
        "website": context.lead.website,
        "homepage_url": context.homepage_url,
        "homepage_and_about_text": context.about_text[:12000],
        "public_signal": {
            "summary": context.public_signal.summary,
            "source_url": context.public_signal.source_url,
            "signal_type": context.public_signal.signal_type.value,
        },
    }
    return f"""
You are classifying a financial institution for a B2B lead research workflow.

Rules:
- Use only the supplied context.
- Do not invent services, facts, customer segments, or signals.
- If the context is insufficient, use "Unknown" or a cautious phrase.
- Return valid JSON only.

JSON schema:
{{
  "institution_type": "string",
  "customer_segment": "string",
  "services": ["string"],
  "fraud_angle": "string"
}}

Context:
{json.dumps(payload, ensure_ascii=True)}
""".strip()


def email_prompt(context: ResearchContext, institution_type: str, fraud_angle: str) -> str:
    signal = context.public_signal
    payload = {
        "company": context.lead.company,
        "institution_type": institution_type,
        "fraud_angle": fraud_angle,
        "signal_summary": signal.summary,
        "source_url": signal.source_url,
        "has_signal": bool(signal.source_url),
        "signal_type": signal.signal_type.value,
    }
    return f"""
Write one conservative B2B cold email for The PreCogs, an AI fraud prevention company.

Hard rules:
- Under 120 words.
- Mention the recent signal first only if has_signal is true.
- If has_signal is false, say you could not find a recent public signal and do not pretend otherwise.
- Do not cite facts beyond the supplied payload.
- Connect the signal or institution context to a likely fraud/risk challenge.
- Position The PreCogs as augmenting existing teams and workflows, not replacing people.
- Mention the no-PII angle naturally.
- End with a low-friction CTA.
- Return valid JSON only: {{"email": "string"}}

Payload:
{json.dumps(payload, ensure_ascii=True)}
""".strip()
