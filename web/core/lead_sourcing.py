from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from web.core.llm import OpenAIClient
from web.core.logger import log_info, log_warning


@dataclass
class SourcedLead:
    company: str
    website: str
    institution_category: str

    def to_csv_row(self) -> Dict[str, str]:
        return {
            "company": self.company,
            "website": self.website,
            "institution_category": self.institution_category,
        }


class LeadSourceGenerator:
    """Builds an input lead CSV using an LLM-generated lead list."""

    def __init__(self, timeout_seconds: float = 20) -> None:
        self.timeout_seconds = timeout_seconds

    async def generate(
        self,
        output_path: Path,
        api_key: str,
        model: str,
        total: int = 100,
        institution_type: str = "both",
        batch_size: int = 50,
        max_attempts: int = 8,
    ) -> List[SourcedLead]:
        llm = OpenAIClient(
            api_key=api_key,
            model=model,
            timeout_seconds=max(self.timeout_seconds, 60),
            max_retries=1,
        )

        leads: List[SourcedLead] = []
        seen_companies = set()
        attempts = 0
        batch_size = max(1, min(batch_size, 100))

        while len(leads) < total and attempts < max_attempts:
            attempts += 1
            count = min(batch_size, total - len(leads))
            prompt = self._lead_prompt(
                count=count,
                institution_type=institution_type,
                existing_companies=sorted(seen_companies)[:300],
            )

            try:
                data = await llm.generate_json(
                    prompt,
                    operation="llm_lead_sourcing",
                    company="lead_sourcing",
                )
            except Exception as exc:
                log_warning("LLM lead source batch failed", step="lead_sourcing", attempt=attempts, error=exc)
                continue

            batch = self._parse_leads(data)
            added = 0
            for lead in batch:
                key = lead.company.lower()
                if key in seen_companies:
                    continue

                seen_companies.add(key)
                leads.append(lead)
                added += 1

                if len(leads) >= total:
                    break

            log_info(
                "LLM lead batch processed",
                step="lead_sourcing",
                attempt=attempts,
                requested=count,
                received=len(batch),
                added=added,
                total=len(leads),
            )

        if leads:
            self.write_csv(output_path, leads)
        return leads

    def _lead_prompt(
        self,
        count: int,
        institution_type: str,
        existing_companies: List[str],
    ) -> str:
        payload = {
            "count": count,
            "institution_type": institution_type,
            "avoid_companies": existing_companies,
        }

        return f"""
You are an expert B2B Lead Generation and Financial Institution Research Agent.

Objective:
Identify and return U.S.-based Community Banks and Credit Unions for sales prospecting, lead generation, and market research.

Instructions:
- Generate exactly the requested count when possible.
- Focus only on Community Banks and Credit Unions located in the United States of America.
- Include community banks and credit unions when they match the requested institution_type.
- Company name and official website are mandatory.
- Website must be the institution's official website and suitable for further scraping and enrichment.
- Do not include any institution if you are not confident about its official website.
- Do not include duplicate institutions or any company listed in avoid_companies.
- Do not include fintech platforms, payment processors, software companies, or non-bank/non-credit-union companies.
- Return ONLY valid JSON.
- Do not include explanations, markdown, comments, or code fences.

Output Requirements:
- Return a JSON object with one key: "institutions".
- "institutions" must be an array.
- Each institution must follow the schema below.
- Name and website are mandatory for every record.
- Avoid duplicate institutions.

Schema:
{{
  "institutions": [
    {{
      "company": "string",
      "website": "string",
      "institution_type": "Community Bank | Credit Union"
    }}
  ]
}}

Context:
{json.dumps(payload, ensure_ascii=True)}
""".strip()

    def _parse_leads(self, data: Dict[str, Any]) -> List[SourcedLead]:
        institutions = data.get("institutions", [])
        if not isinstance(institutions, list):
            return []

        leads: List[SourcedLead] = []
        for item in institutions:
            if not isinstance(item, dict):
                continue

            company = self._clean_text(item.get("company"))
            website = self._normalize_website(item.get("website"))
            institution_type = self._clean_text(item.get("institution_type"))

            if not company or not website:
                continue

            category = self._institution_category(institution_type)
            if not category:
                continue

            leads.append(SourcedLead(company=company, website=website, institution_category=category))

        return leads

    def write_csv(self, output_path: Path, leads: List[SourcedLead]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = self._backup_existing_csv(output_path)
        rows = [lead.to_csv_row() for lead in leads]
        pd.DataFrame(rows).to_csv(output_path, index=False)
        log_info(
            "Generated lead input CSV",
            step="lead_sourcing",
            path=output_path,
            backup_path=backup_path or "-",
            count=len(leads),
        )

    def _backup_existing_csv(self, output_path: Path) -> str:
        if not output_path.exists():
            return ""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = output_path.with_name("%s_%s%s" % (output_path.stem, timestamp, output_path.suffix))
        suffix = 1

        while backup_path.exists():
            backup_path = output_path.with_name(
                "%s_%s_%s%s" % (output_path.stem, timestamp, suffix, output_path.suffix)
            )
            suffix += 1

        output_path.rename(backup_path)
        return str(backup_path)

    def _institution_category(self, institution_type: str) -> str:
        normalized = institution_type.lower().replace("-", " ").replace("_", " ")
        if "credit union" in normalized:
            return "credit_union"
        if "community bank" in normalized or "bank" in normalized:
            return "community_bank"
        return ""

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def _normalize_website(self, value: Any) -> str:
        website = self._clean_text(value)
        if not website:
            return ""
        if website.lower() in {"nan", "none", "null", "n/a", "unknown"}:
            return ""
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        return website
