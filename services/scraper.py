import asyncio
import time
from typing import List
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from services.logger import log_error, log_info, log_timing, log_warning
from services.models import PageContent


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def normalize_url(url: str) -> str:
    # Users may provide "example.com"; aiohttp needs a full URL with a scheme.
    cleaned_url = url.strip()

    if not cleaned_url:
        return cleaned_url

    if not cleaned_url.startswith(("http://", "https://")):
        cleaned_url = "https://%s" % cleaned_url

    return cleaned_url


def same_domain(url: str, candidate: str) -> bool:
    original_domain = urlparse(url).netloc.replace("www.", "")
    candidate_domain = urlparse(candidate).netloc.replace("www.", "")
    return original_domain == candidate_domain


class AsyncScraper:
    def __init__(self, timeout_seconds: float = 12, max_retries: int = 2, max_chars: int = 9000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_chars = max_chars

    async def fetch(
        self,
        session: aiohttp.ClientSession,
        url: str,
        company: str = "",
        request_id: str = "",
    ) -> PageContent:
        # Step 1: normalize the URL before making the request.
        normalized_url = normalize_url(url)

        # Step 2: try the request with simple retry handling.
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            started_at = time.perf_counter()
            log_info(
                "Fetch started",
                company=company,
                step="scrape",
                request_id=request_id,
                url=normalized_url,
                attempt=attempt,
            )

            try:
                async with session.get(normalized_url, allow_redirects=True) as response:
                    content_type = response.headers.get("content-type", "")

                    # Step 3: only parse HTML. PDFs/images/etc. are not useful for this assignment.
                    is_html = "text/html" in content_type
                    if not is_html and response.status < 400:
                        log_warning(
                            "Unsupported content type",
                            company=company,
                            step="scrape",
                            request_id=request_id,
                            status=response.status,
                            content_type=content_type,
                        )
                        return PageContent(
                            url=str(response.url),
                            status_code=response.status,
                            error="Unsupported content type: %s" % content_type,
                        )

                    # Step 4: parse the HTML into clean readable text.
                    html = await response.text(errors="ignore")
                    page = self._parse_html(str(response.url), html, response.status)
                    return page

            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as exc:
                # Step 5: retry temporary network failures, then return an error page.
                is_last_attempt = attempt == total_attempts

                if is_last_attempt:
                    log_error(
                        "Fetch failed",
                        company=company,
                        step="scrape",
                        request_id=request_id,
                        url=normalized_url,
                        attempt=attempt,
                        duration_ms=log_timing(started_at),
                        error=exc,
                    )
                    return PageContent(url=normalized_url, error=str(exc))

                await asyncio.sleep(0.4 * attempt)

        # This is a defensive fallback. Normally the loop returns a page or an error.
        return PageContent(url=normalized_url, error="Unknown fetch failure")

    async def fetch_home_and_about(self, website: str, company: str = "", request_id: str = "") -> List[PageContent]:
        started_at = time.perf_counter()
        homepage_url = normalize_url(website)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        # Homepage and about pages are enough for this assignment because we only need
        # basic company context, not a full website crawl.
        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            home_page = await self.fetch(
                session,
                homepage_url,
                company=company,
                request_id=request_id,
            )

            about_urls = self._about_candidates(home_page)
            about_urls = about_urls[:3]

            about_tasks = []
            for about_url in about_urls:
                about_tasks.append(
                    self.fetch(
                        session,
                        about_url,
                        company=company,
                        request_id=request_id,
                    )
                )

            about_pages = await asyncio.gather(*about_tasks)

        pages = [home_page]
        pages.extend(about_pages)

        successful_pages = 0
        total_text_chars = 0
        for page in pages:
            if page.text:
                successful_pages += 1
                total_text_chars += len(page.text)

        log_info(
            "Scrape completed",
            company=company,
            step="scrape",
            request_id=request_id,
            page_count=len(pages),
            successful_pages=successful_pages,
            total_text_chars=total_text_chars,
            duration_ms=log_timing(started_at),
        )
        return pages

    def _parse_html(self, url: str, html: str, status_code: int) -> PageContent:
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content tags so the LLM receives readable page text.
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        title = ""
        if soup.title:
            title = soup.title.get_text(" ", strip=True)

        text = soup.get_text(" ", strip=True)
        text = " ".join(text.split())

        # Limit text size so prompts stay fast, cheaper, and easier to validate.
        text = text[: self.max_chars]

        return PageContent(
            url=url,
            title=title,
            text=text,
            status_code=status_code,
        )

    def _about_candidates(self, page: PageContent) -> List[str]:
        if not page.text:
            return []

        if not page.url:
            return []

        common_paths = ["about", "about-us", "company", "who-we-are", "our-story"]
        candidates = []

        for path in common_paths:
            candidate_url = urljoin(page.url, path)
            candidates.append(candidate_url)

        # Keep the order predictable and avoid fetching the same URL twice.
        seen = set()
        unique_candidates = []

        for candidate_url in candidates:
            already_seen = candidate_url in seen
            belongs_to_same_domain = same_domain(page.url, candidate_url)

            if not already_seen and belongs_to_same_domain:
                seen.add(candidate_url)
                unique_candidates.append(candidate_url)

        return unique_candidates
