from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from services.models import PageContent
from services.logger import log_error, log_info, log_timing, log_warning

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def normalize_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return cleaned
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    return cleaned


def same_domain(url: str, candidate: str) -> bool:
    return urlparse(url).netloc.replace("www.", "") == urlparse(candidate).netloc.replace("www.", "")


class AsyncScraper:
    def __init__(
        self,
        timeout_seconds: float = 12,
        max_retries: int = 2,
        max_chars: int = 9000,
    ) -> None:
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
        normalized = normalize_url(url)
        for attempt in range(1, self.max_retries + 2):
            started_at = time.perf_counter()
            try:
                async with session.get(normalized, allow_redirects=True) as response:
                    content_type = response.headers.get("content-type", "")
                    if "text/html" not in content_type and response.status < 400:
                        log_warning(
                            "Unsupported content type",
                            company=company,
                            step="scrape",
                            request_id=request_id,
                            url=response.url,
                            status=response.status,
                            content_type=content_type,
                        )
                        return PageContent(
                            url=str(response.url),
                            status_code=response.status,
                            error=f"Unsupported content type: {content_type}",
                        )
                    html = await response.text(errors="ignore")
                    return self._parse_html(str(response.url), html, response.status)
            except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as exc:
                if attempt > self.max_retries:
                    log_error(
                        "HTTP request failed",
                        company=company,
                        step="scrape",
                        request_id=request_id,
                        url=normalized,
                        attempt=attempt,
                        duration_ms=log_timing(started_at),
                        error=exc,
                    )
                    return PageContent(url=normalized, error=str(exc))
                await asyncio.sleep(0.4 * attempt)
        return PageContent(url=normalized, error="Unknown fetch failure")

    async def fetch_many(self, urls: Iterable[str]) -> list[PageContent]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            return await asyncio.gather(*(self.fetch(session, url) for url in urls))

    async def fetch_home_and_about(self, website: str, company: str = "", request_id: str = "") -> list[PageContent]:
        started_at = time.perf_counter()
        homepage = normalize_url(website)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            home = await self.fetch(session, homepage, company=company, request_id=request_id)
            candidate_urls = self._about_candidates(home)
            about_pages = await asyncio.gather(
                *(self.fetch(session, url, company=company, request_id=request_id) for url in candidate_urls[:3])
            )
            pages = [home, *about_pages]
            log_info(
                "Scrape complete",
                company=company,
                step="scrape",
                request_id=request_id,
                page_count=len(pages),
                successful_pages=sum(1 for page in pages if page.text),
                total_text_chars=sum(len(page.text) for page in pages),
                duration_ms=log_timing(started_at),
            )
            return pages

    def _parse_html(self, url: str, html: str, status_code: int) -> PageContent:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = " ".join(soup.get_text(" ", strip=True).split())
        return PageContent(url=url, title=title, text=text[: self.max_chars], status_code=status_code)

    def _about_candidates(self, page: PageContent) -> list[str]:
        if not page.text or not page.url:
            return []
        common_paths = ["about", "about-us", "company", "who-we-are", "our-story"]
        candidates = [urljoin(page.url, path) for path in common_paths]
        # Keep deterministic order while avoiding duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for candidate in candidates:
            if candidate not in seen and same_domain(page.url, candidate):
                seen.add(candidate)
                unique.append(candidate)
        return unique
