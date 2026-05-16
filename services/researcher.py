import re
import time
from typing import Optional
from urllib.parse import urlparse

from services.logger import log_info, log_timing, log_warning
from services.models import PageContent, PageLink, PublicSignal, ResearchContext, SignalType
from services.scraper import AsyncScraper


SOURCE_LINK_KEYWORDS = [
    "press release",
    "press-releases",
    "press_release",
    "releases",
    "news release",
    "newsroom",
    "news",
    "media",
    "press",
    "investor relations",
    "investors",
    "investor",
    "ir.",
    "/ir",
    "announcements",
    "updates",
    "events",
    "insights",
    "resources",
    "blog",
    "stories",
    "careers",
    "jobs",
]

SIGNAL_KEYWORDS = {
    SignalType.FRAUD_RISK: ("fraud", "scam", "identity", "aml", "kyc", "risk", "security", "cyber", "financial crime"),
    SignalType.COMPLIANCE: ("compliance", "regulatory", "regulation", "bsa", "aml", "audit", "consent order", "enforcement"),
    SignalType.PAYMENTS: ("ach", "wire", "payment", "payments", "real-time", "instant", "card", "debit", "credit"),
    SignalType.PARTNERSHIP: ("partner", "partnership", "integrat", "collaboration", "alliance", "selected", "chooses"),
    SignalType.EXPANSION: ("expansion", "expand", "launch", "opened", "new market", "growth", "acquisition"),
    SignalType.HIRING: ("job", "jobs", "hiring", "career", "careers", "analyst", "investigator"),
    SignalType.PRESS: ("press release", "announced", "news", "release", "update"),
}

SOURCE_PAGE_BONUS = {
    "press release": 10,
    "news release": 10,
    "newsroom": 9,
    "press": 8,
    "news": 7,
    "media": 7,
    "investor relations": 7,
    "investors": 6,
    "investor": 6,
    "announcements": 6,
    "updates": 4,
    "blog": 3,
    "careers": 3,
    "jobs": 3,
}

ARTICLE_URL_PATTERN = re.compile(r"/(?:20\d{2}|\d{4}/\d{1,2}|news|press|media|investor|blog|career|job)", re.I)


class CompanyResearcher:
    """Collects factual context from the company URL provided in the CSV.

    The flow avoids search engines. It scrapes the supplied landing page, then
    follows likely press/news/investor links discovered on that page.
    """

    def __init__(self, scraper: AsyncScraper) -> None:
        self.scraper = scraper

    async def research(self, lead):
        started_at = time.perf_counter()
        company = lead.company
        context = ResearchContext(lead=lead)

        if not lead.website:
            log_warning("No website provided; skipping scrape", company=company, step="scrape")
            context.public_signal = PublicSignal.none()
            return context

        page = await self.scraper.fetch_landing_page(lead.website, company=company)

        context.homepage_url = page.url
        context.about_text = page.text[:15000]

        if page.error:
            context.errors.append(page.error)

        signal = await self._find_public_signal(page, company=company)
        context.public_signal = signal

        if signal.source_url:
            context.about_text = self._append_signal_context(context.about_text, signal, limit=15000)

        log_info(
            "Research context extracted",
            company=company,
            step="scrape",
            source_url=page.url,
            page_title=page.title or "-",
            status=page.status_code,
            text_chars=len(context.about_text),
            text_preview=context.about_text[:250],
            discovered_links=len(page.links),
            has_signal=bool(signal.source_url),
            signal_url=signal.source_url or "-",
            duration_ms=log_timing(started_at),
        )

        if context.about_text:
            print("Website scrape OK: extracted landing page text for %s" % company)
        else:
            print("Website scrape: no landing page text extracted for %s" % company)

        return context

    async def _find_public_signal(self, landing_page: PageContent, company: str) -> PublicSignal:
        candidate_links = self._rank_source_links(landing_page.links, landing_page.url)
        candidate_urls = [link.url for link in candidate_links[:10]]

        if not candidate_urls:
            log_warning(
                "No press/news/investor links found on landing page",
                company=company,
                step="signal_discovery",
                landing_page=landing_page.url,
            )
            best_homepage_signal = self._select_signal_page([landing_page])
            if best_homepage_signal:
                signal_type = self._classify_signal_type(best_homepage_signal)
                return PublicSignal(
                    summary=self._summarize_signal(best_homepage_signal, signal_type),
                    source_url=best_homepage_signal.url,
                    signal_type=signal_type,
                    source_title=best_homepage_signal.title,
                    confidence=0.45,
                )
            return PublicSignal.none()

        log_info(
            "Signal source links selected",
            company=company,
            step="signal_discovery",
            candidate_count=len(candidate_urls),
            candidate_urls=", ".join(candidate_urls[:10]),
        )
        print("Signal discovery: checking %s website source pages for %s" % (len(candidate_urls), company))

        pages = await self.scraper.fetch_pages(candidate_urls, company=company)
        detail_urls = self._discover_detail_urls(pages)
        detail_pages = []

        if detail_urls:
            log_info(
                "Signal detail links selected",
                company=company,
                step="signal_discovery",
                detail_count=len(detail_urls[:10]),
                detail_urls=", ".join(detail_urls[:10]),
            )
            print("Signal discovery: checking %s detail pages for %s" % (len(detail_urls[:10]), company))
            detail_pages = await self.scraper.fetch_pages(detail_urls[:10], company=company)

        best_page = self._select_signal_page(detail_pages + pages + [landing_page])

        if not best_page:
            log_warning(
                "No usable signal page found",
                company=company,
                step="signal_discovery",
                candidate_count=len(candidate_urls),
            )
            return PublicSignal.none()

        signal_type = self._classify_signal_type(best_page)
        summary = self._summarize_signal(best_page, signal_type)

        return PublicSignal(
            summary=summary,
            source_url=best_page.url,
            signal_type=signal_type,
            source_title=best_page.title,
            confidence=0.72,
        )

    def _rank_source_links(self, links: list[PageLink], landing_url: str) -> list[PageLink]:
        scored_links = []
        seen_urls = set()

        for link in links:
            if link.url in seen_urls:
                continue

            if not self._same_company_site(landing_url, link.url):
                continue

            haystack = "%s %s" % (link.text.lower(), link.url.lower())
            score = 0
            for keyword, bonus in SOURCE_PAGE_BONUS.items():
                if keyword in haystack:
                    score += bonus

            for keywords in SIGNAL_KEYWORDS.values():
                for keyword in keywords:
                    if keyword in haystack:
                        score += 3

            if ARTICLE_URL_PATTERN.search(link.url):
                score += 2

            if score <= 0:
                continue

            scored_links.append((score, link))
            seen_urls.add(link.url)

        scored_links.sort(key=lambda item: item[0], reverse=True)
        return [link for _, link in scored_links]

    def _discover_detail_urls(self, pages: list[PageContent]) -> list[str]:
        scored_links = []
        seen_urls = set(page.url for page in pages)

        for page in pages:
            for link in page.links:
                if link.url in seen_urls:
                    continue

                if not self._same_company_site(page.url, link.url):
                    continue

                haystack = "%s %s" % (link.text.lower(), link.url.lower())
                score = 0

                for keyword, bonus in SOURCE_PAGE_BONUS.items():
                    if keyword in haystack:
                        score += bonus

                for keywords in SIGNAL_KEYWORDS.values():
                    for keyword in keywords:
                        if keyword in haystack:
                            score += 4

                if ARTICLE_URL_PATTERN.search(link.url):
                    score += 3

                if score <= 0:
                    continue

                scored_links.append((score, link.url))
                seen_urls.add(link.url)

        scored_links.sort(key=lambda item: item[0], reverse=True)
        return [url for _, url in scored_links]

    def _select_signal_page(self, pages: list[PageContent]) -> Optional[PageContent]:
        usable_pages = [page for page in pages if page.text and not page.error and (page.status_code or 0) < 400]
        if not usable_pages:
            return None

        scored_pages = [(self._score_signal_page(page), page) for page in usable_pages]
        scored_pages.sort(key=lambda item: item[0], reverse=True)

        best_score, best_page = scored_pages[0]
        if best_score <= 0:
            return None

        return best_page

    def _score_signal_page(self, page: PageContent) -> int:
        haystack = "%s %s %s" % (page.title.lower(), page.url.lower(), page.text[:5000].lower())
        score = 0

        for keyword, bonus in SOURCE_PAGE_BONUS.items():
            if keyword in haystack:
                score += bonus

        if ARTICLE_URL_PATTERN.search(page.url):
            score += 3

        for keywords in SIGNAL_KEYWORDS.values():
            for keyword in keywords:
                if keyword in haystack:
                    score += 4

        return score

    def _classify_signal_type(self, page: PageContent) -> SignalType:
        haystack = "%s %s %s" % (page.title.lower(), page.url.lower(), page.text[:5000].lower())

        scored_types = []

        for signal_type, keywords in SIGNAL_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in haystack)
            if signal_type != SignalType.PRESS:
                score *= 2
            scored_types.append((score, signal_type))

        scored_types.sort(key=lambda item: item[0], reverse=True)
        best_score, best_type = scored_types[0]
        if best_score <= 0:
            return SignalType.PRESS

        return best_type

    def _summarize_signal(self, page: PageContent, signal_type: SignalType) -> str:
        title = page.title.strip()
        first_sentence = self._first_sentence(page.text)

        if title and first_sentence and first_sentence.lower() not in title.lower():
            summary = "%s: %s" % (title, first_sentence)
        else:
            summary = title or first_sentence or "Public update related to %s." % signal_type.value

        return summary[:450].rstrip(" ,.;:") + "."

    def _first_sentence(self, text: str) -> str:
        cleaned = " ".join(text.split())
        for separator in [". ", "! ", "? "]:
            if separator in cleaned:
                return cleaned.split(separator, 1)[0][:260].strip(" ,.;:")

        return cleaned[:260].strip(" ,.;:")

    def _append_signal_context(self, about_text: str, signal: PublicSignal, limit: int) -> str:
        signal_context = "\n\nPublic signal from %s: %s" % (signal.source_url, signal.summary)
        return (about_text + signal_context)[:limit]

    def _same_company_site(self, source_url: str, target_url: str) -> bool:
        source_domain = urlparse(source_url).netloc.lower().removeprefix("www.")
        target_domain = urlparse(target_url).netloc.lower().removeprefix("www.")
        source_root = self._root_domain(source_domain)
        target_root = self._root_domain(target_domain)
        return bool(source_root and target_root and source_root == target_root)

    def _root_domain(self, domain: str) -> str:
        parts = [part for part in domain.split(".") if part]
        if len(parts) <= 2:
            return domain

        if parts[-2] in {"co", "com", "org", "net"} and len(parts) >= 3:
            return ".".join(parts[-3:])

        return ".".join(parts[-2:])
