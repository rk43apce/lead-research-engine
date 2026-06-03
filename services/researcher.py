import re
import time
from typing import Optional
from urllib.parse import urlparse

from services.logger import log_info, log_timing, log_warning
from services.models import ContactEmail, PageContent, PageLink, PublicSignal, ResearchContext, SignalType
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
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
MAX_SOURCE_PAGES = 3
MAX_DETAIL_PAGES = 3
MAX_THIRD_LEVEL_PAGES = 3
MAX_EMAIL_PAGES = 6

CONTACT_PAGE_BONUS = {
    "leadership": 14,
    "executive": 13,
    "management": 13,
    "team": 10,
    "board": 9,
    "contact us": 9,
    "contact": 8,
    "about us": 7,
    "about": 6,
    "fraud": 6,
    "security": 6,
    "risk": 5,
    "compliance": 5,
    "privacy": 4,
    "email": 4,
}

GENERIC_EMAIL_PREFIXES = {
    "admin",
    "alerts",
    "banking",
    "careers",
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
    "service",
    "support",
    "webmaster",
}

VALUABLE_EMAIL_PREFIX_KEYWORDS = (
    "executive",
    "leadership",
    "management",
    "fraud",
    "risk",
    "security",
    "compliance",
    "operations",
    "treasury",
    "commercial",
    "business",
    "lending",
    "loan",
    "mortgage",
    "bsa",
    "aml",
)

ROLE_EMAIL_PREFIXES = {
    "bsa",
    "aml",
    "fraud",
    "risk",
    "security",
    "compliance",
    "operations",
    "treasury",
    "commercial",
    "business",
    "lending",
    "loans",
    "mortgage",
    "mortgages",
    "executive",
    "management",
}


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
        context.contact_email = await self._find_contact_email(page, company=company)

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
            recipient_email=context.contact_email.email or "-",
            recipient_email_source_url=context.contact_email.source_url or "-",
            duration_ms=log_timing(started_at),
        )

        print(
            "Research: %s scraped %s chars, %s links, signal=%s, email=%s"
            % (
                company,
                len(context.about_text),
                len(page.links),
                "yes" if signal.source_url else "no",
                "yes" if context.contact_email.email else "no",
            )
        )

        return context

    async def _find_public_signal(self, landing_page: PageContent, company: str) -> PublicSignal:
        candidate_links = self._rank_source_links(landing_page.links, landing_page.url)
        candidate_urls = [link.url for link in candidate_links[:MAX_SOURCE_PAGES]]

        if not candidate_urls:
            log_warning(
                "No press/news/investor links found on landing page",
                company=company,
                step="signal_discovery",
                landing_page=landing_page.url,
            )
            signal = self._landing_page_context_signal(landing_page)
            if signal:
                self._log_signal_found(company, signal, source="homepage")
                return signal
            return PublicSignal.none()

        log_info(
            "Signal source links selected",
            company=company,
            step="signal_discovery",
            candidate_count=len(candidate_urls),
            candidate_urls=", ".join(candidate_urls),
        )
        print("Signal discovery: %s checking %s source pages" % (company, len(candidate_urls)))

        pages = await self.scraper.fetch_pages(candidate_urls, company=company)
        detail_urls = self._discover_detail_urls(pages)
        detail_urls = detail_urls[:MAX_DETAIL_PAGES]
        detail_pages = []
        third_level_pages = []

        if detail_urls:
            log_info(
                "Signal detail links selected",
                company=company,
                step="signal_discovery",
                detail_count=len(detail_urls),
                detail_urls=", ".join(detail_urls),
            )
            print("Signal discovery: %s checking %s detail pages" % (company, len(detail_urls)))
            detail_pages = await self.scraper.fetch_pages(detail_urls, company=company)
            third_level_urls = self._discover_detail_urls(detail_pages)[:MAX_THIRD_LEVEL_PAGES]

            if third_level_urls:
                log_info(
                    "Signal third-level links selected",
                    company=company,
                    step="signal_discovery",
                    third_level_count=len(third_level_urls),
                    third_level_urls=", ".join(third_level_urls),
                )
                print("Signal discovery: %s checking %s third-level pages" % (company, len(third_level_urls)))
                third_level_pages = await self.scraper.fetch_pages(third_level_urls, company=company)

        best_page = self._select_signal_page(third_level_pages + detail_pages + pages + [landing_page])

        if not best_page:
            log_warning(
                "No usable signal page found",
                company=company,
                step="signal_discovery",
                candidate_count=len(candidate_urls),
            )
            signal = self._landing_page_context_signal(landing_page)
            if signal:
                self._log_signal_found(company, signal, source="homepage")
                return signal
            return PublicSignal.none()

        if best_page.url == landing_page.url:
            signal = self._landing_page_context_signal(landing_page)
            if signal:
                self._log_signal_found(company, signal, source="homepage")
                return signal

        signal_type = self._classify_signal_type(best_page)
        summary = self._summarize_signal(best_page, signal_type)

        signal = PublicSignal(
            summary=summary,
            source_url=best_page.url,
            signal_type=signal_type,
            source_title=best_page.title,
            confidence=0.72,
        )
        self._log_signal_found(company, signal, source="source_page")
        return signal

    async def _find_contact_email(self, landing_page: PageContent, company: str) -> ContactEmail:
        pages = [landing_page]
        seen_urls = {landing_page.url}
        contact_urls = self._rank_contact_links(landing_page.links, landing_page.url, seen_urls)
        contact_urls = contact_urls[:MAX_EMAIL_PAGES]

        if contact_urls:
            log_info(
                "Contact email level-one pages selected",
                company=company,
                step="email_discovery",
                candidate_count=len(contact_urls),
                candidate_urls=", ".join(contact_urls),
            )
            level_one_pages = await self.scraper.fetch_pages(contact_urls, company=company)
            pages.extend(level_one_pages)

            level_two_urls = self._discover_contact_urls(level_one_pages, seen_urls)[:MAX_EMAIL_PAGES]
            if level_two_urls:
                log_info(
                    "Contact email level-two pages selected",
                    company=company,
                    step="email_discovery",
                    candidate_count=len(level_two_urls),
                    candidate_urls=", ".join(level_two_urls),
                )
                level_two_pages = await self.scraper.fetch_pages(level_two_urls, company=company)
                pages.extend(level_two_pages)

                level_three_urls = self._discover_contact_urls(level_two_pages, seen_urls)[:MAX_EMAIL_PAGES]
                if level_three_urls:
                    log_info(
                        "Contact email level-three pages selected",
                        company=company,
                        step="email_discovery",
                        candidate_count=len(level_three_urls),
                        candidate_urls=", ".join(level_three_urls),
                    )
                    pages.extend(await self.scraper.fetch_pages(level_three_urls, company=company))

        best = self._select_contact_email(pages, landing_page.url, company=company)
        if best.email:
            log_info(
                "Recipient email detected",
                company=company,
                step="email_discovery",
                recipient_email=best.email,
                recipient_email_source_url=best.source_url,
                confidence=best.confidence,
            )
            print("Recipient email detected: %s -> %s" % (company, best.email))
            return best

        log_warning("No non-generic recipient email found", company=company, step="email_discovery")
        return ContactEmail()

    def _discover_contact_urls(self, pages: list[PageContent], seen_urls: set[str]) -> list[str]:
        urls = []
        for page in pages:
            urls.extend(self._rank_contact_links(page.links, page.url, seen_urls))
        return urls

    def _rank_contact_links(self, links: list[PageLink], landing_url: str, seen_urls: set[str]) -> list[str]:
        scored_links = []

        for link in links:
            if link.url in seen_urls:
                continue
            if not self._same_company_site(landing_url, link.url):
                continue

            haystack = "%s %s" % (link.text.lower(), link.url.lower())
            score = 0
            for keyword, bonus in CONTACT_PAGE_BONUS.items():
                if keyword in haystack:
                    score += bonus

            if score <= 0:
                continue

            scored_links.append((score, link.url))
            seen_urls.add(link.url)

        scored_links.sort(key=lambda item: item[0], reverse=True)
        return [url for _, url in scored_links]

    def _select_contact_email(self, pages: list[PageContent], landing_url: str, company: str = "") -> ContactEmail:
        scored_emails = []
        seen_emails = set()
        raw_email_count = 0
        rejected_email_count = 0

        for page in pages:
            if page.error or (page.status_code or 0) >= 400:
                continue

            for email in page.emails:
                raw_email_count += 1
                normalized_email = email.lower().strip()
                if normalized_email in seen_emails:
                    continue
                if not self._is_usable_recipient_email(normalized_email, landing_url):
                    rejected_email_count += 1
                    continue

                score = self._score_contact_email(normalized_email, page)
                if score <= 0:
                    continue

                scored_emails.append((score, normalized_email, page.url))
                seen_emails.add(normalized_email)

        if not scored_emails:
            log_warning(
                "No usable recipient email after filtering",
                company=company,
                step="email_discovery",
                pages_checked=len(pages),
                raw_email_count=raw_email_count,
                rejected_email_count=rejected_email_count,
            )
            return ContactEmail()

        scored_emails.sort(key=lambda item: item[0], reverse=True)
        score, email, source_url = scored_emails[0]
        return ContactEmail(email=email, source_url=source_url, confidence=min(score / 30, 0.95))

    def _is_usable_recipient_email(self, email: str, landing_url: str) -> bool:
        if not EMAIL_RE.fullmatch(email):
            return False

        prefix, domain = email.split("@", 1)
        normalized_prefix = re.sub(r"[^a-z0-9-]", "", prefix.lower())
        if normalized_prefix in GENERIC_EMAIL_PREFIXES:
            return False

        email_root = self._root_domain(domain.lower())
        landing_root = self._root_domain(urlparse(landing_url).netloc.lower().removeprefix("www."))
        if landing_root and email_root != landing_root:
            return False

        return True

    def _score_contact_email(self, email: str, page: PageContent) -> int:
        prefix = email.split("@", 1)[0].lower()
        normalized_prefix = re.sub(r"[^a-z0-9-]", "", prefix)
        haystack = "%s %s %s" % (page.title.lower(), page.url.lower(), page.text[:3000].lower())
        score = 10

        for keyword, bonus in CONTACT_PAGE_BONUS.items():
            if keyword in haystack:
                score += bonus

        for keyword in VALUABLE_EMAIL_PREFIX_KEYWORDS:
            if keyword in prefix:
                score += 8

        if normalized_prefix in ROLE_EMAIL_PREFIXES:
            score += 10

        if "." in prefix or "-" in prefix:
            score += 4

        return score

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

    def _landing_page_context_signal(self, landing_page: PageContent) -> Optional[PublicSignal]:
        if landing_page.error:
            return None

        if (landing_page.status_code or 0) >= 400:
            return None

        if len(landing_page.text.strip()) < 80:
            return None

        summary = self._summarize_signal(landing_page, SignalType.WEBSITE_CONTEXT)
        return PublicSignal(
            summary="Website context: %s" % summary,
            source_url=landing_page.url,
            signal_type=SignalType.WEBSITE_CONTEXT,
            source_title=landing_page.title,
            confidence=0.35,
        )

    def _log_signal_found(self, company: str, signal: PublicSignal, source: str) -> None:
        log_info(
            "Public signal detected",
            company=company,
            step="signal_discovery",
            signal_found=True,
            signal_type=signal.signal_type.value,
            source_url=signal.source_url,
            source=source,
            confidence=signal.confidence,
            summary=signal.summary,
        )
        print("Signal detected: %s -> %s" % (company, signal.signal_type.value))

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
