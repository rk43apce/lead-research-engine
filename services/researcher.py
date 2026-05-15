import asyncio
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import aiohttp
from bs4 import BeautifulSoup

from services.logger import log_error, log_info, log_timing, log_warning
from services.models import Lead, PublicSignal, ResearchContext, SearchResult, SignalType
from services.scraper import AsyncScraper


SIGNAL_QUERIES = (
    '"{company}" fraud risk banking',
    '"{company}" compliance risk financial institution',
    '"{company}" ACH wire payments launch',
    '"{company}" partnership fintech payments',
    '"{company}" press release expansion bank credit union',
    '"{company}" fraud analyst job',
)

SIGNAL_KEYWORDS: Dict[SignalType, Tuple[str, ...]] = {
    SignalType.FRAUD_RISK: ("fraud", "scam", "identity", "aml", "kyc", "risk"),
    SignalType.COMPLIANCE: ("compliance", "regulatory", "bsa", "aml", "audit"),
    SignalType.PAYMENTS: ("ach", "wire", "payment", "payments", "real-time", "instant"),
    SignalType.PARTNERSHIP: ("partner", "partnership", "integrat", "collaboration"),
    SignalType.EXPANSION: ("expansion", "launch", "opened", "new market", "growth"),
    SignalType.HIRING: ("job", "hiring", "career", "analyst", "investigator"),
    SignalType.PRESS: ("press release", "announced", "news"),
}


class DuckDuckGoResearcher:
    """Collects factual public research for one company.

    This service does not generate email copy. It only returns source-backed
    research context that the LLM can use later.
    """

    def __init__(
        self,
        scraper: AsyncScraper,
        timeout_seconds: float = 12,
        results_per_query: int = 5,
    ) -> None:
        self.scraper = scraper
        self.timeout_seconds = timeout_seconds
        self.results_per_query = results_per_query

    async def research(self, lead):
        started_at = time.perf_counter()
        company = lead.company
        request_id = lead.request_id
        context = ResearchContext(lead=lead)

        log_info("Research started", company=company, step="research", request_id=request_id)

        # Step 1: scrape homepage/about pages if a website was provided.
        if lead.website:
            log_info("Scraping company website", company=company, step="scrape", request_id=request_id)
            pages = await self.scraper.fetch_home_and_about(lead.website, company=company, request_id=request_id)
            context.homepage_url = pages[0].url if pages else lead.website
            context.about_text = "\n\n".join(page.text for page in pages if page.text)[:15000]
            context.errors.extend(page.error for page in pages if page.error)
            log_info(
                "Website scrape completed",
                company=company,
                step="scrape",
                request_id=request_id,
                pages=len(pages),
                text_chars=len(context.about_text),
                errors=len(context.errors),
            )
        else:
            log_warning("No website provided; skipping scrape", company=company, step="scrape", request_id=request_id)

        # Step 2: search public web signals. DuckDuckGo is used because it is free
        # and works for the take-home without a paid search API.
        try:
            context.search_results = await self._search_company_signals(company, request_id)
        except Exception as exc:
            log_error("Public signal search failed", company=company, step="duckduckgo_search", request_id=request_id, error=exc)
            context.errors.append("Search failed: %s" % exc)

        # Step 3/4: choose one source-backed signal, or safely return no signal.
        context.public_signal = self._choose_signal(company, context.search_results, request_id)

        log_info(
            "Research completed",
            company=company,
            step="research",
            request_id=request_id,
            search_results=len(context.search_results),
            has_signal=bool(context.public_signal.source_url),
            duration_ms=log_timing(started_at),
        )
        return context

    async def _search_company_signals(self, company: str, request_id: str):
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        queries = []
        for query_template in SIGNAL_QUERIES:
            queries.append(query_template.format(company=company))

        log_info("Public signal search started", company=company, step="duckduckgo_search", request_id=request_id, query_count=len(queries))

        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "Mozilla/5.0"}) as session:
            tasks = []
            for query in queries:
                tasks.append(self._duckduckgo_html(session, query))

            # Searches are I/O-bound, so we run them concurrently for speed.
            search_outputs = await asyncio.gather(*tasks, return_exceptions=True)

        # Step 3: deduplicate URLs so the same source does not appear many times.
        results: List[SearchResult] = []
        seen_urls = set()
        failed_queries = 0

        for output in search_outputs:
            if isinstance(output, Exception):
                failed_queries += 1
                continue

            for result in output:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                results.append(result)

        if failed_queries:
            log_warning("Some public signal searches failed", company=company, step="duckduckgo_search", request_id=request_id, failed_queries=failed_queries)

        log_info("Public signal search completed", company=company, step="duckduckgo_search", request_id=request_id, results=len(results))
        return results

    async def _duckduckgo_html(self, session: aiohttp.ClientSession, query: str):
        url = "https://duckduckgo.com/html/?q=%s" % quote_plus(query)
        log_info("DuckDuckGo search URL created", step="duckduckgo_search", url=url)
        print("DuckDuckGo URL: %s" % url)

        async with session.get(url) as response:
            html = await response.text(errors="ignore")

        soup = BeautifulSoup(html, "html.parser")
        results: List[SearchResult] = []

        for node in soup.select(".result")[: self.results_per_query]:
            link = node.select_one(".result__a")
            snippet_node = node.select_one(".result__snippet")

            if not link:
                continue

            title = link.get_text(" ", strip=True)
            href = link.get("href", "")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            source_url = self._clean_duckduckgo_url(href)

            if source_url.startswith("http"):
                results.append(SearchResult(title=title, url=source_url, snippet=snippet))

        return results

    def _clean_duckduckgo_url(self, href: str):
        parsed = urlparse(href)
        params = parse_qs(parsed.query)

        if "uddg" in params:
            return unquote(params["uddg"][0])

        if href.startswith("//"):
            return "https:%s" % href

        return href

    def _choose_signal(self, company: str, results: List[SearchResult], request_id: str):
        best_result: Optional[SearchResult] = None
        best_signal_type = SignalType.NONE
        best_score = 0

        for result in results:
            if not result.url:
                continue

            text = ("%s %s" % (result.title, result.snippet)).lower()

            # Only select a signal if the result appears to be about the company.
            # This prevents unrelated fraud/risk news from being attached to the lead.
            if not self._company_name_appears(company, text):
                continue

            for signal_type, keywords in SIGNAL_KEYWORDS.items():
                score = 0
                for keyword in keywords:
                    if keyword in text:
                        score += 1

                # Small recency boost for recent years in titles/snippets.
                if "2024" in text or "2025" in text or "2026" in text or "recent" in text:
                    score += 2

                if score > best_score:
                    best_score = score
                    best_signal_type = signal_type
                    best_result = result

        # Anti-hallucination rule: if we do not have a source URL, do not guess.
        if best_result is None:
            log_warning("No source-backed signal found", company=company, step="signal_selection", request_id=request_id, result_count=len(results))
            return PublicSignal.none()

        summary_text = best_result.snippet or best_result.title
        summary = "%s: %s" % (best_result.title, summary_text)

        log_info(
            "Selected public signal",
            company=company,
            step="signal_selection",
            request_id=request_id,
            signal_type=best_signal_type.value,
            score=best_score,
            source_url=best_result.url,
        )

        return PublicSignal(
            summary=summary[:400],
            source_url=best_result.url,
            signal_type=best_signal_type,
            source_title=best_result.title,
            confidence=min(best_score / 6, 1.0),
        )

    def _company_name_appears(self, company: str, text: str):
        words = re.findall(r"[a-zA-Z0-9]+", company.lower())
        meaningful_words = []

        for word in words:
            if len(word) > 2:
                meaningful_words.append(word)

        for word in meaningful_words[:3]:
            if word in text:
                return True

        return False
