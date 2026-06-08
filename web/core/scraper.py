import asyncio
import re
from urllib.parse import urldefrag, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from web.core.logger import log_error, log_warning
from web.core.models import PageContent, PageLink


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
OBFUSCATED_EMAIL_RE = re.compile(
    r"\b([A-Z0-9._%+-]+)\s*(?:\[\s*at\s*\]|\(\s*at\s*\)|\sat\s)\s*"
    r"([A-Z0-9.-]+)\s*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\sdot\s)\s*([A-Z]{2,})\b",
    re.IGNORECASE,
)


def normalize_url(url: str) -> str:
    # Users may provide "example.com"; aiohttp needs a full URL with a scheme.
    cleaned_url = url.strip()

    if not cleaned_url:
        return cleaned_url

    if not cleaned_url.startswith(("http://", "https://")):
        cleaned_url = "https://%s" % cleaned_url

    return cleaned_url


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
    ) -> PageContent:
        # Step 1: normalize the URL before making the request.
        normalized_url = normalize_url(url)

        # Step 2: try the request with simple retry handling.
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
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
                        url=normalized_url,
                        attempt=attempt,
                        error=exc,
                    )
                    return PageContent(url=normalized_url, error=str(exc))

                await asyncio.sleep(0.4 * attempt)

        # This is a defensive fallback. Normally the loop returns a page or an error.
        return PageContent(url=normalized_url, error="Unknown fetch failure")

    async def fetch_landing_page(self, website: str, company: str = "") -> PageContent:
        landing_page_url = normalize_url(website)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            page = await self.fetch(
                session,
                landing_page_url,
                company=company,
            )
        return page

    async def fetch_pages(self, urls: list[str], company: str = "", concurrency: int = 3) -> list[PageContent]:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        semaphore = asyncio.Semaphore(concurrency)

        async with aiohttp.ClientSession(headers=DEFAULT_HEADERS, timeout=timeout) as session:
            async def fetch_one(url: str) -> PageContent:
                async with semaphore:
                    return await self.fetch(session, url, company=company)

            return await asyncio.gather(*(fetch_one(url) for url in urls))

    def _parse_html(self, url: str, html: str, status_code: int) -> PageContent:
        soup = BeautifulSoup(html, "html.parser")
        links = self._extract_links(url, soup)
        emails = self._extract_emails(soup, html)

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
            links=links,
            emails=emails,
        )

    def _extract_links(self, base_url: str, soup: BeautifulSoup) -> list[PageLink]:
        links: list[PageLink] = []
        seen_urls = set()

        for anchor in soup.find_all("a", href=True):
            raw_href = anchor.get("href", "").strip()
            if not raw_href:
                continue

            parsed_href = urlparse(raw_href)
            if parsed_href.scheme in {"mailto", "tel", "javascript"}:
                continue

            absolute_url = urljoin(base_url, raw_href)
            absolute_url = urldefrag(absolute_url).url
            parsed_url = urlparse(absolute_url)

            if parsed_url.scheme not in {"http", "https"}:
                continue

            if absolute_url in seen_urls:
                continue

            link_text = " ".join(anchor.get_text(" ", strip=True).split())
            links.append(PageLink(url=absolute_url, text=link_text[:180]))
            seen_urls.add(absolute_url)

        return links

    def _extract_emails(self, soup: BeautifulSoup, html: str) -> list[str]:
        emails: list[str] = []
        seen_emails = set()

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if href.lower().startswith("mailto:"):
                raw_email = href.split(":", 1)[1].split("?", 1)[0]
                self._append_email(raw_email, emails, seen_emails)

        for raw_email in EMAIL_RE.findall(html):
            self._append_email(raw_email, emails, seen_emails)

        readable_text = soup.get_text(" ", strip=True)
        for local_part, domain, suffix in OBFUSCATED_EMAIL_RE.findall(readable_text):
            self._append_email("%s@%s.%s" % (local_part, domain, suffix), emails, seen_emails)

        return emails

    def _append_email(self, raw_email: str, emails: list[str], seen_emails: set[str]) -> None:
        email = raw_email.strip().strip(".,;:()[]{}<>\"'").lower()
        if not EMAIL_RE.fullmatch(email):
            return
        if email in seen_emails:
            return

        emails.append(email)
        seen_emails.add(email)
