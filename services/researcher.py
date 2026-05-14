from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import aiohttp
from bs4 import BeautifulSoup

from services.models import Lead, PublicSignal, ResearchContext, SearchResult, SignalType
from services.scraper import AsyncScraper
from services.logger import log_error, log_info, log_timing, log_warning

SIGNAL_QUERIES = (
    '"{company}" fraud risk banking',
    '"{company}" compliance risk financial institution',
    '"{company}" ACH wire payments launch',
    '"{company}" partnership fintech payments',
    '"{company}" press release expansion bank credit union',
    '"{company}" fraud analyst job',
)

SIGNAL_KEYWORDS: dict[SignalType, tuple[str, ...]] = {
    SignalType.FRAUD_RISK: ("fraud", "scam", "identity", "aml", "kyc", "risk"),
    SignalType.COMPLIANCE: ("compliance", "regulatory", "bsa", "aml", "audit"),
    SignalType.PAYMENTS: ("ach", "wire", "payment", "payments", "real-time", "instant"),
    SignalType.PARTNERSHIP: ("partner", "partnership", "integrat", "collaboration"),
    SignalType.EXPANSION: ("expansion", "launch", "opened", "new market", "growth"),
    SignalType.HIRING: ("job", "hiring", "career", "analyst", "investigator"),
    SignalType.PRESS: ("press release", "announced", "news"),
}


class DuckDuckGoResearcher:
    def __init__(
        self,
        scraper: AsyncScraper,
        timeout_seconds: float = 12,
        results_per_query: int = 5,
    ) -> None:
        self.scraper = scraper
        self.timeout_seconds = timeout_seconds
        self.results_per_query = results_per_query

    async def research(self, lead: Lead) -> ResearchContext:
        started_at = time.perf_counter()
        context = ResearchContext(lead=lead)
        request_id = lead.request_id

        if lead.website:
            pages = await self.scraper.fetch_home_and_about(lead.website, company=lead.company, request_id=request_id)
            context.homepage_url = pages[0].url if pages else lead.website
            context.about_text = "\n\n".join(page.text for page in pages if page.text)[:15000]
            context.errors.extend(page.error for page in pages if page.error)
            if context.errors:
                log_warning("Homepage scrape completed with errors", company=lead.company, step="research", request_id=request_id, scrape_errors=len(context.errors))
        else:
            log_warning("No website provided; skipping scrape", company=lead.company, step="research", request_id=request_id)

        try:
            context.search_results = await self._search_company_signals(lead.company, request_id)
        except Exception as exc:  # Keep one bad search from killing the batch.
            log_error("Search failed", company=lead.company, step="duckduckgo_search", request_id=request_id, error=exc, exc_info=True)
            context.errors.append(f"Search failed: {exc}")

        context.public_signal = self._choose_signal(lead.company, context.search_results, request_id)
        log_info(
            "Research complete",
            company=lead.company,
            step="research",
            request_id=request_id,
            search_results=len(context.search_results),
            has_signal=bool(context.public_signal.source_url),
            signal_type=context.public_signal.signal_type.value,
            source_url=context.public_signal.source_url or "-",
            duration_ms=log_timing(started_at),
        )
        return context

    async def _search_company_signals(self, company: str, request_id: str) -> list[SearchResult]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as session:
            queries = [query.format(company=company) for query in SIGNAL_QUERIES]
            tasks = [self._duckduckgo_html(session, query, company) for query in queries]
            nested = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[SearchResult] = []
        seen: set[str] = set()
        failed_queries = 0
        for item in nested:
            if isinstance(item, Exception):
                failed_queries += 1
                continue
            for result in item:
                if result.url not in seen:
                    seen.add(result.url)
                    results.append(result)
        if failed_queries:
            log_warning("Some public signal searches failed", company=company, step="duckduckgo_search", request_id=request_id, failed_queries=failed_queries)
        log_info("Public signal search complete", company=company, step="duckduckgo_search", request_id=request_id, results=len(results), failed_queries=failed_queries)
        return results

    async def _duckduckgo_html(self, session: aiohttp.ClientSession, query: str, company: str) -> list[SearchResult]:
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        async with session.get(url) as response:
            html = await response.text(errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        results: list[SearchResult] = []
        for node in soup.select(".result")[: self.results_per_query]:
            link = node.select_one(".result__a")
            snippet_node = node.select_one(".result__snippet")
            if not link:
                continue
            href = link.get("href", "")
            title = link.get_text(" ", strip=True)
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            href = self._clean_duckduckgo_url(href)
            if href.startswith("http"):
                results.append(SearchResult(title=title, url=href, snippet=snippet))
        return results

    def _clean_duckduckgo_url(self, href: str) -> str:
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        if "uddg" in params:
            return unquote(params["uddg"][0])
        if href.startswith("//"):
            return f"https:{href}"
        return href

    def _choose_signal(self, company: str, results: list[SearchResult], request_id: str) -> PublicSignal:
        company_tokens = [token.lower() for token in re.findall(r"[a-zA-Z0-9]+", company) if len(token) > 2]
        best: tuple[int, SignalType, SearchResult] | None = None
        for result in results:
            haystack = f"{result.title} {result.snippet}".lower()
            if company_tokens and not any(token in haystack for token in company_tokens[:3]):
                continue
            for signal_type, keywords in SIGNAL_KEYWORDS.items():
                keyword_hits = sum(1 for keyword in keywords if keyword in haystack)
                if not keyword_hits:
                    continue
                score = keyword_hits + (2 if any(word in haystack for word in ("2024", "2025", "2026", "recent")) else 0)
                if best is None or score > best[0]:
                    best = (score, signal_type, result)

        if best is None:
            log_warning("No verifiable public signal selected", company=company, step="signal_selection", request_id=request_id, result_count=len(results))
            return PublicSignal.none()

        score, signal_type, result = best
        summary_source = result.snippet or result.title
        summary = f"{result.title}: {summary_source}"[:400]
        log_info(
            "Selected public signal",
            company=company,
            step="signal_selection",
            request_id=request_id,
            signal_type=signal_type.value,
            score=score,
            source_url=result.url,
            source_title=result.title[:160],
        )
        return PublicSignal(
            summary=summary,
            source_url=result.url,
            signal_type=signal_type,
            source_title=result.title,
            confidence=min(score / 6, 1.0),
        )
