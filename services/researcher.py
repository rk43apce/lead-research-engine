import time

from services.logger import log_info, log_timing, log_warning
from services.models import PublicSignal, ResearchContext
from services.scraper import AsyncScraper


class CompanyResearcher:
    """Collects factual context from the company URL provided in the CSV.

    This version intentionally avoids search engines. It only scrapes the
    landing page URL supplied in `input/leads.csv`.
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

        # Scrape only the landing page from the input CSV.
        page = await self.scraper.fetch_landing_page(lead.website, company=company)

        context.homepage_url = page.url
        context.about_text = page.text[:15000]

        if page.error:
            context.errors.append(page.error)

        # We are not using DuckDuckGo now, so we do not invent a recent signal.
        context.public_signal = PublicSignal.none()

        log_info(
            "Landing page text extracted",
            company=company,
            step="scrape",
            source_url=page.url,
            page_title=page.title or "-",
            status=page.status_code,
            text_chars=len(context.about_text),
            text_preview=context.about_text[:250],
            has_signal=False,
            duration_ms=log_timing(started_at),
        )

        if context.about_text:
            print("Website scrape OK: extracted landing page text for %s" % company)
        else:
            print("Website scrape: no landing page text extracted for %s" % company)

        return context
