from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree
from urllib.parse import urlencode, urljoin, urlparse

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup

from services.llm import OpenAIClient
from services.logger import log_info, log_warning


FDIC_INSTITUTIONS_URL = "https://banks.data.fdic.gov/api/institutions"
NCUA_CALL_REPORT_DATA_URL = "https://ncua.gov/analysis/credit-union-corporate-call-report-data"

GENERIC_EMAIL_PREFIXES = {
    "admin",
    "alerts",
    "banking",
    "careers",
    "compliance",
    "contact",
    "customerservice",
    "help",
    "hello",
    "hr",
    "info",
    "jobs",
    "marketing",
    "media",
    "noreply",
    "no-reply",
    "onlinebanking",
    "privacy",
    "security",
    "service",
    "support",
    "webmaster",
}

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


@dataclass
class SourcedLead:
    company: str
    website: str = ""
    institution_category: str = ""
    city: str = ""
    state: str = ""
    source: str = ""
    source_id: str = ""
    management_email: str = ""
    email_source_url: str = ""

    def to_csv_row(self) -> Dict[str, str]:
        return {
            "company": self.company,
            "website": self.website,
            "institution_category": self.institution_category,
        }


class LeadSourceGenerator:
    """Builds an input lead CSV from grounded public institution data."""

    def __init__(
        self,
        timeout_seconds: float = 20,
        email_pages_per_company: int = 0,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.email_pages_per_company = email_pages_per_company

    async def generate(
        self,
        output_path: Path,
        total: int = 1000,
        banks: int = 1000,
        credit_unions: int = 0,
    ) -> List[SourcedLead]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            bank_leads, credit_union_leads = await asyncio.gather(
                self.fetch_fdic_banks(session, banks),
                self.fetch_ncua_credit_unions(session, credit_unions),
            )

            leads = [lead for lead in self._dedupe(bank_leads + credit_union_leads) if lead.website][:total]

            if self.email_pages_per_company > 0:
                await self.enrich_management_emails(session, leads)

        self.write_csv(output_path, leads)
        return leads

    async def generate_with_openai(
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
        seen = set()
        attempts = 0
        batch_size = max(1, min(batch_size, 100))

        while len(leads) < total and attempts < max_attempts:
            attempts += 1
            count = min(batch_size, total - len(leads))
            prompt = self._openai_lead_prompt(
                count=count,
                institution_type=institution_type,
                existing_companies=sorted(seen)[:300],
            )

            try:
                data = await llm.generate_json(
                    prompt,
                    operation="openai_lead_sourcing",
                    company="lead_sourcing",
                )
            except Exception as exc:
                log_warning("OpenAI lead source batch failed", step="lead_sourcing", attempt=attempts, error=exc)
                continue

            batch = self._parse_openai_leads(data)
            added = 0
            for lead in batch:
                key = lead.company.lower()
                if key in seen:
                    continue
                if not lead.company or not lead.website:
                    continue
                seen.add(key)
                leads.append(lead)
                added += 1

                if len(leads) >= total:
                    break

            log_info(
                "OpenAI lead batch processed",
                step="lead_sourcing",
                attempt=attempts,
                requested=count,
                received=len(batch),
                added=added,
                total=len(leads),
            )

        self.write_csv(output_path, leads)
        return leads

    async def fetch_fdic_banks(self, session: aiohttp.ClientSession, limit: int) -> List[SourcedLead]:
        """Fetch active FDIC-insured community-bank-style leads."""
        if limit <= 0:
            return []

        fields = [
            "NAME",
            "CERT",
            "WEBADDR",
            "CITY",
            "STALP",
            "ASSET",
            "BKCLASS",
            "ACTIVE",
        ]
        fetch_limit = min(max(limit * 5, 100), 10000)
        params = {
            "filters": "ACTIVE:1 AND ASSET:[* TO 10000000]",
            "fields": ",".join(fields),
            "sort_by": "ASSET",
            "sort_order": "ASC",
            "limit": str(fetch_limit),
            "format": "json",
        }
        url = "%s?%s" % (FDIC_INSTITUTIONS_URL, urlencode(params))

        try:
            async with session.get(url) as response:
                response.raise_for_status()
                payload = await response.json()
        except Exception as exc:
            log_warning("FDIC lead source fetch failed", step="lead_sourcing", error=exc)
            return []

        leads: List[SourcedLead] = []
        for item in payload.get("data", []):
            data = item.get("data") if isinstance(item, dict) else {}
            if not isinstance(data, dict):
                continue

            company = self._clean_text(data.get("NAME"))
            if not company:
                continue

            website = self._normalize_website(data.get("WEBADDR"))
            if not website:
                continue

            leads.append(
                SourcedLead(
                    company=company,
                    website=website,
                    institution_category="community_bank",
                    city=self._clean_text(data.get("CITY")),
                    state=self._clean_text(data.get("STALP")),
                    source="FDIC BankFind",
                    source_id=self._clean_text(data.get("CERT")),
                )
            )

            if len(leads) >= limit:
                break

        log_info("FDIC leads fetched", step="lead_sourcing", count=len(leads))
        return leads

    async def fetch_ncua_credit_unions(
        self,
        session: aiohttp.ClientSession,
        limit: int,
    ) -> List[SourcedLead]:
        """Fetch active federally insured credit unions from NCUA's public list."""
        if limit <= 0:
            return []

        try:
            download_url = await self._resolve_ncua_active_list_url(session)
            async with session.get(download_url) as response:
                response.raise_for_status()
                content = await response.read()
        except Exception as exc:
            log_warning("NCUA lead source fetch failed", step="lead_sourcing", error=exc)
            return []

        rows = self._read_first_table_from_zip(content)
        leads: List[SourcedLead] = []

        for row in rows:
            company = self._first_value(
                row,
                "Credit Union Name",
                "CU_NAME",
                "CU Name",
                "Name",
                "CreditUnionName",
            )
            company = self._clean_text(company)
            if not company:
                continue

            leads.append(
                SourcedLead(
                    company=company,
                    website=self._normalize_website(
                        self._first_value(row, "Website", "URL", "Web Site", "WEB_SITE", "Website URL")
                    ),
                    institution_category="credit_union",
                    city=self._clean_text(self._first_value(row, "City", "CITY", "City (Mailing address)")),
                    state=self._clean_text(self._first_value(row, "State", "STATE", "ST", "State (Mailing address)")),
                    source="NCUA Active Federally Insured Credit Unions",
                    source_id=self._clean_text(
                        self._first_value(row, "Charter Number", "Charter number", "CHARTER", "CU_NUMBER", "Charter")
                    ),
                )
            )

            if len(leads) >= limit:
                break

        log_info("NCUA credit union leads fetched", step="lead_sourcing", count=len(leads))
        return leads

    async def _resolve_ncua_active_list_url(self, session: aiohttp.ClientSession) -> str:
        async with session.get(NCUA_CALL_REPORT_DATA_URL) as response:
            response.raise_for_status()
            html = await response.text(errors="ignore")

        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            text = self._clean_text(anchor.get_text(" "))
            if "list of active federally insured credit unions" in text.lower():
                return urljoin(NCUA_CALL_REPORT_DATA_URL, anchor["href"])

        raise RuntimeError("Could not find NCUA active credit union list download link.")

    async def enrich_management_emails(
        self,
        session: aiohttp.ClientSession,
        leads: List[SourcedLead],
    ) -> None:
        tasks = [self._enrich_one_email(session, lead) for lead in leads if lead.website]
        await asyncio.gather(*tasks)

    async def _enrich_one_email(self, session: aiohttp.ClientSession, lead: SourcedLead) -> None:
        urls = self._email_candidate_urls(lead.website)
        urls = urls[: self.email_pages_per_company]

        for url in urls:
            try:
                async with session.get(url, allow_redirects=True) as response:
                    if response.status >= 400:
                        continue
                    text = await response.text(errors="ignore")
            except Exception:
                continue

            email = self._best_management_email(text, lead.website)
            if email:
                lead.management_email = email
                lead.email_source_url = url
                return

    def _email_candidate_urls(self, website: str) -> List[str]:
        root = website.rstrip("/") + "/"
        paths = [
            "",
            "about",
            "about-us",
            "leadership",
            "management",
            "executive-team",
            "team",
            "contact",
            "contact-us",
        ]
        return [urljoin(root, path) for path in paths]

    def _best_management_email(self, html: str, website: str) -> str:
        domain = urlparse(website).netloc.lower().removeprefix("www.")
        candidates = []

        for match in EMAIL_RE.findall(html):
            email = match.lower()
            if self._is_generic_email(email):
                continue
            if domain and not email.endswith("@" + domain):
                continue
            candidates.append(email)

        return candidates[0] if candidates else ""

    def _is_generic_email(self, email: str) -> bool:
        prefix = email.split("@", 1)[0].lower()
        normalized = re.sub(r"[^a-z0-9-]", "", prefix)
        return normalized in GENERIC_EMAIL_PREFIXES

    def _openai_lead_prompt(
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
- Focus only on Community Banks and Credit Unions located in the United States.
- Company name and official website are mandatory.
- Website must be the institution's official website and suitable for further scraping and enrichment.
- Do not include any institution if you are not confident about its official website.
- Do not include duplicate institutions or any company listed in avoid_companies.
- Do not fabricate management contacts or email addresses.
- Use "Unknown" when management email cannot be confidently determined.
- fraud_angle must NEVER be empty and should describe a plausible fraud, compliance, risk-management, identity-verification, AML, account-takeover, payment-fraud, or cyber-security use case relevant to the institution.
- management_email should contain a publicly available executive, management, or general management contact email when available; otherwise use "Unknown".
- customer_segment should describe the primary customer base served by the institution.
- services should only include services that can be reasonably verified or inferred from publicly available information.
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
      "institution_type": "Community Bank | Credit Union",
      "headquarters": "string",
      "customer_segment": "string",
      "services": ["string"],
      "fraud_angle": "string",
      "management_email": "string"
    }}
  ]
}}

Context:
{json.dumps(payload, ensure_ascii=True)}
""".strip()

    def _parse_openai_leads(self, data: Dict[str, Any]) -> List[SourcedLead]:
        institutions = data.get("institutions", [])
        if not isinstance(institutions, list):
            return []

        leads: List[SourcedLead] = []
        for item in institutions:
            if not isinstance(item, dict):
                continue

            company = self._clean_text(item.get("company"))
            website = self._normalize_website(item.get("website"))
            if not company or not website:
                continue

            institution_type = self._clean_text(item.get("institution_type"))
            category = "credit_union" if "credit" in institution_type.lower() else "community_bank"
            management_email = self._clean_text(item.get("management_email"))
            if (
                management_email.lower() == "unknown"
                or not EMAIL_RE.fullmatch(management_email)
                or self._is_generic_email(management_email)
            ):
                management_email = ""

            leads.append(
                SourcedLead(
                    company=company,
                    website=website,
                    institution_category=category,
                    city=self._clean_text(item.get("headquarters")),
                    state="",
                    source="OpenAI generated lead list",
                    source_id="",
                    management_email=management_email,
                    email_source_url=website if management_email else "",
                )
            )

        return leads

    def _read_first_table_from_zip(self, content: bytes) -> List[Dict[str, Any]]:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith((".csv", ".txt"))]
            xlsx_names = [name for name in archive.namelist() if name.lower().endswith(".xlsx")]

            if xlsx_names:
                return self._read_xlsx_rows(archive.read(xlsx_names[0]))

            if not csv_names:
                return []

            with archive.open(csv_names[0]) as handle:
                raw = handle.read()

        text = raw.decode("utf-8-sig", errors="replace")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel

        return list(csv.DictReader(io.StringIO(text), dialect=dialect))

    def _read_xlsx_rows(self, content: bytes) -> List[Dict[str, Any]]:
        with zipfile.ZipFile(io.BytesIO(content)) as workbook:
            shared_strings = self._read_shared_strings(workbook)
            sheet_name = self._first_sheet_name(workbook)
            sheet_xml = workbook.read(sheet_name)

        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        root = ElementTree.fromstring(sheet_xml)
        rows: List[List[str]] = []

        for row in root.findall(".//x:sheetData/x:row", namespace):
            values: Dict[int, str] = {}
            for cell in row.findall("x:c", namespace):
                ref = cell.attrib.get("r", "")
                column = self._xlsx_column_index(ref)
                if column < 0:
                    continue
                values[column] = self._xlsx_cell_value(cell, shared_strings, namespace)

            if values:
                max_column = max(values)
                rows.append([values.get(index, "") for index in range(max_column + 1)])

        if not rows:
            return []

        header = [self._clean_text(value) for value in rows[0]]
        records = []
        for row in rows[1:]:
            record = {}
            for index, column in enumerate(header):
                if column:
                    record[column] = row[index] if index < len(row) else ""
            if any(str(value).strip() for value in record.values()):
                records.append(record)

        return records

    def _read_shared_strings(self, workbook: zipfile.ZipFile) -> List[str]:
        try:
            xml = workbook.read("xl/sharedStrings.xml")
        except KeyError:
            return []

        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        root = ElementTree.fromstring(xml)
        strings = []
        for item in root.findall("x:si", namespace):
            parts = [node.text or "" for node in item.findall(".//x:t", namespace)]
            strings.append("".join(parts))
        return strings

    def _first_sheet_name(self, workbook: zipfile.ZipFile) -> str:
        names = [name for name in workbook.namelist() if name.startswith("xl/worksheets/sheet")]
        if not names:
            raise RuntimeError("XLSX workbook did not include a worksheet.")
        return sorted(names)[0]

    def _xlsx_cell_value(
        self,
        cell: ElementTree.Element,
        shared_strings: List[str],
        namespace: Dict[str, str],
    ) -> str:
        cell_type = cell.attrib.get("t", "")

        if cell_type == "inlineStr":
            parts = [node.text or "" for node in cell.findall(".//x:t", namespace)]
            return self._clean_text("".join(parts))

        value_node = cell.find("x:v", namespace)
        if value_node is None or value_node.text is None:
            return ""

        value = value_node.text
        if cell_type == "s":
            try:
                return self._clean_text(shared_strings[int(value)])
            except (IndexError, ValueError):
                return ""

        return self._clean_text(value)

    def _xlsx_column_index(self, cell_ref: str) -> int:
        letters = re.sub(r"[^A-Z]", "", cell_ref.upper())
        if not letters:
            return -1

        index = 0
        for letter in letters:
            index = index * 26 + (ord(letter) - ord("A") + 1)
        return index - 1

    def _dedupe(self, leads: Iterable[SourcedLead]) -> List[SourcedLead]:
        seen = set()
        deduped: List[SourcedLead] = []

        for lead in leads:
            key = (lead.company.lower(), lead.state.lower())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(lead)

        return deduped

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

    def _first_value(self, row: Dict[str, Any], *names: str) -> str:
        normalized = {self._column_key(key): value for key, value in row.items()}
        for name in names:
            value = normalized.get(self._column_key(name))
            if value is not None:
                return str(value)
        return ""

    def _column_key(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def _normalize_website(self, value: Any) -> str:
        website = self._clean_text(value)
        if not website:
            return ""
        if website.lower() in {"nan", "none", "null", "n/a"}:
            return ""
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        return website
