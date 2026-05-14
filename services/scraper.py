from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from services.models import PageContent
from services.logger import elapsed_ms, log_context, log_step

LOGGER = logging.getLogger(__name__)

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

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> PageContent:
        normalized = normalize_url(url)
        for attempt in range(1, self.max_retries + 2):
            started_at = time.perf_counter()
            LOGGER.info("HTTP request url=%s attempt=%s max_attempts=%s", normalized, attempt, self.max_retries + 1)
            try:
                async with session.get(normalized, allow_redirects=True) as response:
                    content_type = response.headers.get("content-type", "")
                    LOGGER.info(
                        "HTTP response url=%s final_url=%s status=%s content_type=%s duration_ms=%s",
                        normalized,
                        response.url,
                        response.status,
                        content_type.split(";")[0],
                        elapsed_ms(started_at),
                    )
                    if "text/html" not in content_type and response.status < 400:
                        LOGGER.warning(
                            "Unsupported content type url=%s status=%s content_type=%s",
                            response.url,
                            response.status,
                            content_type,
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
                    LOGGER.exception(
                        "HTTP request failed url=%s attempt=%s duration_ms=%s error=%s",
                        normalized,
                        attempt,
                        elapsed_ms(started_at),
                        exc,
                    )
                    return PageContent(url=normalized, error=str(exc))
                LOGGER.warning(
                    "HTTP request retry url=%s attempt=%s duration_ms=%s error=%s",
                    normalized,
                    attempt,
                    elapsed_ms(started_at),
                    exc,
                )
                await asyncio.sleep(0.4 * attempt)
        return PageContent(url=normalized, error="Unknown fetch failure")

    async def fetch_many(self, urls: Iterable[str]) -> list[PageContent]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            return await asyncio.gather(*(self.fetch(session, url) for url in urls))

    async def fetch_home_and_about(self, website: str) -> list[PageContent]:
        with log_context(step="scrape"):
            with log_step(LOGGER, "scrape", "fetch homepage and about pages", website=website):
                homepage = normalize_url(website)
                timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
                async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
                    home = await self.fetch(session, homepage)
                    candidate_urls = self._about_candidates(home)
                    LOGGER.info("About page candidates count=%s urls=%s", len(candidate_urls[:3]), candidate_urls[:3])
                    about_pages = await asyncio.gather(*(self.fetch(session, url) for url in candidate_urls[:3]))
                    pages = [home, *about_pages]
                    LOGGER.info(
                        "Scrape complete page_count=%s successful_pages=%s total_text_chars=%s",
                        len(pages),
                        sum(1 for page in pages if page.text),
                        sum(len(page.text) for page in pages),
                    )
                    return pages

    def _parse_html(self, url: str, html: str, status_code: int) -> PageContent:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        text = " ".join(soup.get_text(" ", strip=True).split())
        LOGGER.info(
            "Parsed HTML url=%s status=%s title=%s extracted_chars=%s",
            url,
            status_code,
            title[:120],
            min(len(text), self.max_chars),
        )
        LOGGER.debug("Extracted content preview url=%s preview=%s", url, text[:160])
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
