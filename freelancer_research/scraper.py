"""Utilities for collecting project and bidding data from Freelancer.com.

The module is intentionally written to support welfare-oriented empirical
research questions.  It uses Selenium to drive a real browser session and then
extracts structured project and bid information from the rendered DOM or
bootstrapped JSON payloads exposed by the site.  The selectors prefer semantic
attributes (``data-test`` etc.) and fall back to heuristic extraction so that
slight front-end changes require minimal updates.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from selenium import webdriver
from selenium.common.exceptions import (  # type: ignore
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError as exc:  # pragma: no cover - guidance for missing dependency
    raise ImportError(
        "webdriver-manager is required to automatically provision the Chrome "
        "driver.  Install it via `pip install webdriver-manager`."
    ) from exc


BUDGET_RANGE_RE = re.compile(r"([\\d,.]+)")
CURRENCY_RE = re.compile(r"([A-Z]{3})")
PROJECT_ID_RE = re.compile(r"(?P<project_id>\\d{5,})")
BID_COUNT_RE = re.compile(r"(\\d+)")


def _utc_now_iso() -> str:
    """Return a UTC timestamp (with trailing Z) for serialization."""

    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


@dataclass
class EmployerProfile:
    """Summary information about the employer who posted a project."""

    name: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    location: Optional[str] = None


@dataclass
class BidRecord:
    """Represents a single bid placed on a Freelancer project."""

    username: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    amount: Optional[float] = None
    currency_code: Optional[str] = None
    delivery_days: Optional[int] = None
    status: Optional[str] = None
    observed_at: str = field(default_factory=_utc_now_iso)
    observation_run_id: Optional[str] = None


@dataclass
class StatusEvent:
    """Represents an observation of the project's lifecycle state."""

    status: Optional[str] = None
    observed_at: str = field(default_factory=_utc_now_iso)
    run_id: Optional[str] = None
    bids_count: Optional[int] = None
    average_bid: Optional[float] = None


@dataclass
class ProjectSummary:
    """Structured representation of a Freelancer project listing."""

    project_id: str
    title: str
    url: str
    description: Optional[str] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    currency_code: Optional[str] = None
    currency_symbol: Optional[str] = None
    bids_count: Optional[int] = None
    average_bid: Optional[float] = None
    posted_time: Optional[str] = None
    project_type: Optional[str] = None
    skills: List[str] = field(default_factory=list)
    employer: EmployerProfile = field(default_factory=EmployerProfile)
    raw_attributes: Dict[str, Any] = field(default_factory=dict)
    bids: List[BidRecord] = field(default_factory=list)
    observed_at: str = field(default_factory=_utc_now_iso)
    observation_run_id: Optional[str] = None
    status_events: List[StatusEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the dataclass (including nested dataclasses) to a dictionary."""

        payload = asdict(self)
        payload["employer"] = asdict(self.employer)
        payload["bids"] = [asdict(bid) for bid in self.bids]
        payload["status_events"] = [asdict(event) for event in self.status_events]
        return payload


class FreelancerScraper:
    """High-level helper that orchestrates Selenium driven scraping sessions."""

    BASE_URL = "https://www.freelancer.com"
    JOB_SEARCH_PATH = "/jobs/"

    def __init__(
        self,
        *,
        headless: bool = True,
        driver: Optional[WebDriver] = None,
        timeout: int = 20,
        min_delay: float = 1.0,
        max_delay: float = 3.0,
    ) -> None:
        self._driver = driver
        self.timeout = timeout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.headless = headless

    # ------------------------------------------------------------------
    # Driver / context management
    # ------------------------------------------------------------------
    def __enter__(self) -> "FreelancerScraper":
        if self._driver is None:
            self._driver = self._build_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    @property
    def driver(self) -> WebDriver:
        if self._driver is None:
            self._driver = self._build_driver()
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except WebDriverException:
                pass
            finally:
                self._driver = None

    def _build_driver(self) -> WebDriver:
        options = ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--lang=en-US")
        service = ChromeService(executable_path=ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def login(self, email: str, password: str) -> None:
        """Login to Freelancer using provided credentials.

        Parameters
        ----------
        email:
            Account email/username.
        password:
            Account password.
        """

        self.driver.get(f"{self.BASE_URL}/login")
        wait = WebDriverWait(self.driver, self.timeout)
        email_field = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
        )
        email_field.clear()
        email_field.send_keys(email)

        password_field = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='password']"))
        )
        password_field.clear()
        password_field.send_keys(password)

        submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        submit_button.click()

        wait.until(EC.url_contains("/dashboard"))
        self._human_delay()

    def collect_projects(
        self,
        search_terms: Sequence[str],
        *,
        pages_per_term: int = 1,
        results_per_page: int = 20,
        include_bids: bool = False,
        max_bids: Optional[int] = None,
        observation_run_id: Optional[str] = None,
    ) -> List[ProjectSummary]:
        """Collect project listings for the supplied search terms.

        Parameters
        ----------
        search_terms:
            Keywords to query via Freelancer's job search.  Each term is scraped
            separately.  The results are concatenated in the order provided.
        pages_per_term:
            Number of pages to collect for each query.
        results_per_page:
            Pagination parameter used to request more (or fewer) results per
            page.  Values between 20 and 100 are a good compromise between
            payload size and request count.
        include_bids:
            When ``True`` the scraper opens each project detail page and
            attempts to capture individual bid data, including bid amount and
            bidder reputation metrics.  This significantly slows down the
            process but is required for questions related to winner's curse.
        max_bids:
            Optional upper bound for the number of bids captured per project.
        """

        projects: List[ProjectSummary] = []
        for term in search_terms:
            for page_index in range(pages_per_term):
                offset = page_index * results_per_page
                page_url = self._build_search_url(term, results_per_page, offset)
                try:
                    summaries = self._collect_search_page(page_url)
                except TimeoutException:
                    continue

                for summary in summaries:
                    observed_at = _utc_now_iso()
                    summary.observed_at = observed_at
                    summary.observation_run_id = observation_run_id
                    status_event = StatusEvent(
                        status=summary.raw_attributes.get("status")
                        or summary.project_type,
                        observed_at=observed_at,
                        run_id=observation_run_id,
                        bids_count=summary.bids_count,
                        average_bid=summary.average_bid,
                    )
                    summary.status_events.append(status_event)
                    if include_bids:
                        try:
                            bid_records = self._collect_bids(
                                summary.url,
                                max_bids,
                                observed_at=observed_at,
                                observation_run_id=observation_run_id,
                            )
                            summary.bids = bid_records
                            summary.bids_count = summary.bids_count or len(bid_records)
                            status_event.bids_count = summary.bids_count
                        except TimeoutException:
                            pass
                        finally:
                            self._close_extra_tabs()
                    projects.append(summary)
                self._human_delay()
        return projects

    def dump_to_json(
        self,
        projects: Iterable[ProjectSummary],
        output_path: Path,
        *,
        append: bool = False,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        new_records = [project.to_dict() for project in projects]
        existing: List[Dict[str, Any]] = []
        if append and output_path.exists():
            try:
                with output_path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                    if isinstance(loaded, list):
                        existing = loaded
            except json.JSONDecodeError:
                existing = []
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(existing + new_records, fh, indent=2)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _human_delay(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _build_search_url(self, search_term: str, results: int, offset: int) -> str:
        from urllib.parse import quote_plus

        keyword = quote_plus(search_term)
        return (
            f"{self.BASE_URL}{self.JOB_SEARCH_PATH}?keyword={keyword}&results={results}&offset={offset}"
        )

    def _wait_for_project_cards(self) -> None:
        wait = WebDriverWait(self.driver, self.timeout)
        wait.until(
            lambda driver: driver.execute_script(
                "return document.querySelectorAll('app-project-card, "
                "[data-test=\'project-card\'], .ProjectCard, li[data-card-type=\'project\']').length"
            )
        )

    def _collect_search_page(self, url: str) -> List[ProjectSummary]:
        self.driver.get(url)
        self._wait_for_project_cards()
        project_payloads = self.driver.execute_script(
            """
            const cardSelector = 'app-project-card, [data-test="project-card"], .ProjectCard, li[data-card-type="project"]';
            return Array.from(document.querySelectorAll(cardSelector)).map(card => {
                const link = card.querySelector('a[href*="/projects/"]');
                const budget = card.querySelector('[data-test="project-card-budget"], .ProjectCard-budget, .JobSearchCard-primary-bid span');
                const bids = card.querySelector('[data-test="project-card-bids"], .ProjectCard-bids, .JobSearchCard-primary-bid strong');
                const employer = card.querySelector('[data-test="project-card-employer"], .ProjectCard-byline, .EmployerInfo');
                const description = card.querySelector('[data-test="project-card-description"], .ProjectCard-description, .JobSearchCard-secondary-description');
                const meta = card.querySelector('[data-test="project-card-meta"], .ProjectCard-meta, .JobSearchCard-primary-info');
                const avgBid = card.querySelector('[data-test="project-card-avg-bid"], .ProjectCard-average, .JobSearchCard-average-bid strong');
                const skills = Array.from(card.querySelectorAll('a[href*="/jobs/"], [data-test="project-skill"], .ProjectCard-skill')).map(el => el.textContent.trim());
                return {
                    title: link ? link.textContent.trim() : null,
                    url: link ? link.href : null,
                    budget: budget ? budget.textContent.trim() : null,
                    bidsText: bids ? bids.textContent.trim() : null,
                    employer: employer ? employer.textContent.trim() : null,
                    description: description ? description.textContent.trim() : null,
                    meta: meta ? meta.textContent.trim() : null,
                    avgBid: avgBid ? avgBid.textContent.trim() : null,
                    skills: skills,
                };
            });
            """
        )

        summaries: List[ProjectSummary] = []
        for payload in project_payloads:
            summary = self._payload_to_summary(payload)
            if summary is not None:
                summaries.append(summary)
        return summaries

    def _payload_to_summary(self, payload: Dict[str, Any]) -> Optional[ProjectSummary]:
        url = payload.get("url")
        title = payload.get("title")
        if not url or not title:
            return None

        project_id_match = PROJECT_ID_RE.search(url)
        if not project_id_match:
            return None

        budget_min, budget_max, symbol, currency_code = self._parse_budget(payload.get("budget"))
        avg_bid_val, avg_bid_currency = self._parse_amount(payload.get("avgBid"))
        bids_count = self._parse_bids_count(payload.get("bidsText"))
        employer_profile = self._parse_employer(payload.get("employer"))
        summary = ProjectSummary(
            project_id=project_id_match.group("project_id"),
            title=title,
            url=url,
            description=payload.get("description"),
            budget_min=budget_min,
            budget_max=budget_max,
            currency_code=currency_code or avg_bid_currency,
            currency_symbol=symbol,
            bids_count=bids_count,
            average_bid=avg_bid_val,
            posted_time=payload.get("meta"),
            skills=[skill for skill in (payload.get("skills") or []) if skill],
            employer=employer_profile,
            raw_attributes={key: val for key, val in payload.items() if key not in {"skills"}},
        )
        return summary

    def _parse_budget(
        self, budget_text: Optional[str]
    ) -> (Optional[float], Optional[float], Optional[str], Optional[str]):
        if not budget_text:
            return None, None, None, None

        cleaned = budget_text.replace("Budget", "").replace("Avg Bid", "").strip()
        currency_symbol = None
        if cleaned and not cleaned[0].isdigit():
            currency_symbol = cleaned[0]
        numbers = [self._to_float(value) for value in BUDGET_RANGE_RE.findall(cleaned)]
        currency_code_match = CURRENCY_RE.search(cleaned)
        currency_code = currency_code_match.group(1) if currency_code_match else None
        if not numbers:
            return None, None, currency_symbol, currency_code
        if len(numbers) == 1:
            return numbers[0], numbers[0], currency_symbol, currency_code
        return numbers[0], numbers[1], currency_symbol, currency_code

    def _parse_amount(self, amount_text: Optional[str]) -> (Optional[float], Optional[str]):
        if not amount_text:
            return None, None
        numbers = [self._to_float(value) for value in BUDGET_RANGE_RE.findall(amount_text)]
        currency_code_match = CURRENCY_RE.search(amount_text)
        currency_code = currency_code_match.group(1) if currency_code_match else None
        return (numbers[0] if numbers else None, currency_code)

    def _parse_bids_count(self, bids_text: Optional[str]) -> Optional[int]:
        if not bids_text:
            return None
        match = BID_COUNT_RE.search(bids_text.replace(",", ""))
        return int(match.group(1)) if match else None

    def _parse_employer(self, employer_text: Optional[str]) -> EmployerProfile:
        if not employer_text:
            return EmployerProfile()

        rating_match = re.search(r"([0-9]+(?:\\.[0-9]+)?)\s*/\s*5", employer_text)
        review_match = re.search(r"(\\d+)\s*(?:reviews|Review|Ratings?)", employer_text, re.IGNORECASE)
        name = employer_text.split("\n")[0].strip()
        location_match = re.search(r"from\s+(.+)", employer_text, re.IGNORECASE)
        return EmployerProfile(
            name=name or None,
            rating=float(rating_match.group(1)) if rating_match else None,
            review_count=int(review_match.group(1)) if review_match else None,
            location=location_match.group(1).strip() if location_match else None,
        )

    def _collect_bids(
        self,
        project_url: str,
        max_bids: Optional[int],
        *,
        observed_at: Optional[str] = None,
        observation_run_id: Optional[str] = None,
    ) -> List[BidRecord]:
        self._open_in_new_tab(project_url)
        wait = WebDriverWait(self.driver, self.timeout)
        wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")
        data_blobs = self.driver.execute_script(
            """
            const results = [];
            if (window.__NUXT__) { results.push(JSON.stringify(window.__NUXT__)); }
            if (window.__INITIAL_STATE__) { results.push(JSON.stringify(window.__INITIAL_STATE__)); }
            if (window.__APP_INITIAL_STATE__) { results.push(JSON.stringify(window.__APP_INITIAL_STATE__)); }
            const jsonScripts = Array.from(document.querySelectorAll('script[type="application/json"]'));
            jsonScripts.forEach(script => results.push(script.textContent));
            const nextData = document.querySelector('#__NEXT_DATA__');
            if (nextData) { results.push(nextData.textContent); }
            return results;
            """
        )

        for blob in data_blobs:
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            bid_records = self._extract_bids_from_blob(
                data,
                max_bids,
                observed_at=observed_at,
                observation_run_id=observation_run_id,
            )
            if bid_records:
                return bid_records
        return []

    def _extract_bids_from_blob(
        self,
        data: Any,
        max_bids: Optional[int],
        *,
        observed_at: Optional[str] = None,
        observation_run_id: Optional[str] = None,
    ) -> List[BidRecord]:
        bids: List[BidRecord] = []

        def traverse(node: Any) -> None:
            if isinstance(node, dict):
                if {"bids", "project"}.issubset(node.keys()):
                    bids.extend(
                        self._convert_bid_payloads(
                            node["bids"],
                            max_bids,
                            observed_at=observed_at,
                            observation_run_id=observation_run_id,
                        )
                    )
                elif "bids" in node and isinstance(node["bids"], list):
                    bids.extend(
                        self._convert_bid_payloads(
                            node["bids"],
                            max_bids,
                            observed_at=observed_at,
                            observation_run_id=observation_run_id,
                        )
                    )
                for value in node.values():
                    traverse(value)
            elif isinstance(node, list):
                for value in node:
                    traverse(value)

        traverse(data)
        return bids[:max_bids] if max_bids else bids

    def _convert_bid_payloads(
        self,
        payloads: Sequence[Any],
        max_bids: Optional[int],
        *,
        observed_at: Optional[str] = None,
        observation_run_id: Optional[str] = None,
    ) -> List[BidRecord]:
        bid_records: List[BidRecord] = []
        for payload in payloads[: max_bids or None]:
            if not isinstance(payload, dict):
                continue
            amount = payload.get("amount") or payload.get("bid_amount")
            currency = None
            if isinstance(amount, dict):
                currency = amount.get("currency") or amount.get("currency_code")
                value = amount.get("amount") or amount.get("value")
            else:
                value = amount
            bidder = payload.get("bidder") or payload.get("user") or {}
            rating_info = bidder.get("reputation") or payload.get("reputation") or {}
            bid_record = BidRecord(
                username=bidder.get("username"),
                rating=self._safe_float(rating_info.get("overall")),
                review_count=self._safe_int(rating_info.get("review_count")),
                amount=self._safe_float(value),
                currency_code=currency,
                delivery_days=self._safe_int(payload.get("period") or payload.get("delivery_time")),
                status=payload.get("status"),
            )
            if observed_at:
                bid_record.observed_at = observed_at
            bid_record.observation_run_id = observation_run_id
            bid_records.append(bid_record)
        return bid_records

    def _open_in_new_tab(self, url: str) -> None:
        self.driver.execute_script("window.open(arguments[0], '_blank');", url)
        self.driver.switch_to.window(self.driver.window_handles[-1])
        self._human_delay()

    def _close_extra_tabs(self) -> None:
        while len(self.driver.window_handles) > 1:
            handle = self.driver.window_handles[-1]
            self.driver.switch_to.window(handle)
            self.driver.close()
        self.driver.switch_to.window(self.driver.window_handles[0])

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_int(self, value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_float(self, value: str) -> Optional[float]:
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None


def default_scraper_from_env() -> FreelancerScraper:
    """Factory that optionally performs a login using environment variables."""

    scraper = FreelancerScraper()
    email = os.getenv("FREELANCER_EMAIL")
    password = os.getenv("FREELANCER_PASSWORD")
    if email and password:
        with scraper:
            scraper.login(email, password)
    return scraper


def save_projects_to_disk(
    projects: Iterable[ProjectSummary],
    output: Path,
    *,
    append: bool = False,
) -> None:
    """Persist collected project data to JSON."""

    scraper = FreelancerScraper()
    scraper.dump_to_json(projects, output, append=append)


__all__ = [
    "FreelancerScraper",
    "ProjectSummary",
    "BidRecord",
    "EmployerProfile",
    "StatusEvent",
    "default_scraper_from_env",
    "save_projects_to_disk",
]
