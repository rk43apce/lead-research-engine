from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import aiohttp
from bs4 import BeautifulSoup

from services.models import Lead, PublicSignal, ResearchContext, SearchResult, SignalType
from services.scraper import AsyncScraper
from services.logger import elapsed_ms, log_context, log_step

LOGGER = logging.getLogger(__name__)

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
        with log_step(LOGGER, "research", "research company", website=lead.website):
            context = ResearchContext(lead=lead)
            if lead.website:
                pages = await self.scraper.fetch_home_and_about(lead.website)
                context.homepage_url = pages[0].url if pages else lead.website
                context.about_text = "\n\n".join(page.text for page in pages if page.text)[:15000]
                context.errors.extend(page.error for page in pages if page.error)
                LOGGER.info(
                    "Homepage research complete homepage_url=%s about_chars=%s scrape_errors=%s",
                    context.homepage_url,
                    len(context.about_text),
                    len(context.errors),
                )
            else:
                LOGGER.warning("No website provided; skipping homepage scrape")

            try:
                context.search_results = await self._search_company_signals(lead.company)
            except Exception as exc:  # Keep one bad search from killing the batch.
                LOGGER.exception("Search failed error=%s", exc)
                context.errors.append(f"Search failed: {exc}")

            context.public_signal = self._choose_signal(lead.company, context.search_results)
            LOGGER.info(
                "Research complete search_results=%s has_signal=%s signal_type=%s source_url=%s",
                len(context.search_results),
                bool(context.public_signal.source_url),
                context.public_signal.signal_type.value,
                context.public_signal.source_url or "-",
            )
            return context

    async def _search_company_signals(self, company: str) -> list[SearchResult]:
        with log_context(step="duckduckgo_search"):
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as session:
                queries = [query.format(company=company) for query in SIGNAL_QUERIES]
                LOGGER.info("DuckDuckGo search start query_count=%s", len(queries))
                tasks = [self._duckduckgo_html(session, query) for query in queries]
                nested = await asyncio.gather(*tasks, return_exceptions=True)

            results: list[SearchResult] = []
            seen: set[str] = set()
            failed_queries = 0
            for item in nested:
                if isinstance(item, Exception):
                    failed_queries += 1
                    LOGGER.warning("DuckDuckGo query failed error=%s", item)
                    continue
                for result in item:
                    if result.url not in seen:
                        seen.add(result.url)
                        results.append(result)
            LOGGER.info(
                "DuckDuckGo search complete unique_results=%s failed_queries=%s",
                len(results),
                failed_queries,
            )
            return results

    async def _duckduckgo_html(self, session: aiohttp.ClientSession, query: str) -> list[SearchResult]:
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        started_at = time.perf_counter()
        LOGGER.info("DuckDuckGo query request query=%s", query)
        async with session.get(url) as response:
            html = await response.text(errors="ignore")
            LOGGER.info(
                "DuckDuckGo query response status=%s duration_ms=%s html_chars=%s",
                response.status,
                elapsed_ms(started_at),
                len(html),
            )
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
        LOGGER.info("DuckDuckGo query parsed query=%s result_count=%s", query, len(results))
        return results

    def _clean_duckduckgo_url(self, href: str) -> str:
        parsed = urlparse(href)
        params = parse_qs(parsed.query)
        if "uddg" in params:
            return unquote(params["uddg"][0])
        if href.startswith("//"):
            return f"https:{href}"
        return href

    def _choose_signal(self, company: str, results: list[SearchResult]) -> PublicSignal:
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
            LOGGER.warning("No verifiable public signal selected result_count=%s", len(results))
            return PublicSignal.none()

        score, signal_type, result = best
        summary_source = result.snippet or result.title
        summary = f"{result.title}: {summary_source}"[:400]
        LOGGER.info(
            "Selected public signal signal_type=%s score=%s source_url=%s source_title=%s",
            signal_type.value,
            score,
            result.url,
            result.title[:160],
        )
        return PublicSignal(
            summary=summary,
            source_url=result.url,
            signal_type=signal_type,
            source_title=result.title,
            confidence=min(score / 6, 1.0),
        )
