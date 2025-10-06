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
from datetime import datetime, timezone
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


@dataclass
class StatusSnapshot:
    """Represents the observed status of a project at a point in time."""

    status: Optional[str] = None
    observed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"status": self.status, "observed_at": self.observed_at}


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
    project_status: Optional[str] = None
    status_history: List[StatusSnapshot] = field(default_factory=list)
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    skills: List[str] = field(default_factory=list)
    employer: EmployerProfile = field(default_factory=EmployerProfile)
    raw_attributes: Dict[str, Any] = field(default_factory=dict)
    bids: List[BidRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the dataclass (including nested dataclasses) to a dictionary."""

        payload = asdict(self)
        payload["employer"] = asdict(self.employer)
        payload["bids"] = [asdict(bid) for bid in self.bids]
        payload["status_history"] = [snapshot.to_dict() for snapshot in self.status_history]
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
        capture_status_history: bool = True,
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
        capture_status_history:
            When ``True`` the scraper records the project status at the time of
            scraping, mirroring the panel structure in Gao et al. (2025).
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
                    if include_bids or capture_status_history:
                        try:
                            detail_data = self._collect_project_page_details(
                                summary.url,
                                include_bids=include_bids,
                                max_bids=max_bids,
                                existing_history=summary.status_history,
                                existing_type=summary.project_type,
                            )
                            if include_bids:
                                bid_records = detail_data.get("bids", [])
                                summary.bids = bid_records
                                summary.bids_count = summary.bids_count or len(bid_records)
                            if detail_data.get("project_type"):
                                summary.project_type = detail_data["project_type"]
                            if detail_data.get("project_status"):
                                summary.project_status = detail_data["project_status"]
                            if "status_history" in detail_data:
                                summary.status_history = detail_data["status_history"]
                                if summary.status_history:
                                    summary.project_status = summary.status_history[-1].status
                        except TimeoutException:
                            pass
                        finally:
                            self._close_extra_tabs()
                    summary.project_type = summary.project_type or "open"
                    projects.append(summary)
                self._human_delay()
        return projects

    def dump_to_json(self, projects: Iterable[ProjectSummary], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump([project.to_dict() for project in projects], fh, indent=2)

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
                const status = card.querySelector('[data-test="project-card-status"], .ProjectCard-status, .JobSearchCard-status, .JobSearchCard-badge--status');
                const badges = Array.from(card.querySelectorAll('[data-test="project-card-badges"] *, [data-test="project-card-badge"], .ProjectCard-badges span, .JobSearchCard-tags span, .JobSearchCard-badges span, .JobSearchCard-badges a')).map(el => el.textContent.trim()).filter(Boolean);
                const cardType = card.getAttribute('data-project-type') || (card.dataset ? (card.dataset.projectType || card.dataset.cardType) : null);
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
                    statusText: status ? status.textContent.trim() : null,
                    badges: badges,
                    cardType: cardType,
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
        status_text = self._clean_status_text(payload.get("statusText"))
        scraped_at = self._current_timestamp()
        project_type = self._infer_project_type(payload)
        status_history = (
            [StatusSnapshot(status=status_text, observed_at=scraped_at)]
            if status_text
            else []
        )
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
            project_type=project_type,
            project_status=status_text,
            status_history=status_history,
            scraped_at=scraped_at,
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

    def _collect_project_page_details(
        self,
        project_url: str,
        *,
        include_bids: bool,
        max_bids: Optional[int],
        existing_history: Optional[Sequence[StatusSnapshot]] = None,
        existing_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._open_in_new_tab(project_url)
        wait = WebDriverWait(self.driver, self.timeout)
        wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete")

        detail_payload = self.driver.execute_script(
            """
            const status = document.querySelector('[data-test="project-status"], [data-test="project-card-status"], .ProjectView-status, .ProjectHeader-status, .StatusLabel, .ProjectStatus, .JobView-status');
            const meta = document.querySelector('[data-test="project-meta"], .ProjectMeta, .ProjectHeader-meta, .JobView-meta, .JobInfo');
            const auctionType = document.querySelector('[data-test="project-auction-type"], .ProjectView-privacy span, .ProjectView-badges span, .ProjectHeader-badge, .JobView-badge');
            const badges = Array.from(document.querySelectorAll('[data-test="project-badge"], .ProjectView-badges span, .ProjectHeader-badge, .JobView-badges span, .Badge, .Badges span, .ProjectHeader-tags span')).map(el => el.textContent.trim()).filter(Boolean);
            const typeContainer = document.querySelector('[data-project-type], [data-auction-type], [data-test="project-type"]');
            const cardType = typeContainer ? (typeContainer.getAttribute('data-project-type') || typeContainer.getAttribute('data-auction-type') || typeContainer.dataset?.projectType) : null;
            return {
                statusText: status ? status.textContent.trim() : null,
                metaText: meta ? meta.textContent.trim() : null,
                auctionTypeText: auctionType ? auctionType.textContent.trim() : null,
                badges: badges,
                cardType: cardType,
            };
            """
        ) or {}

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

        bids: List[BidRecord] = []
        if include_bids:
            bids = self._collect_bids_from_blobs(data_blobs, max_bids)

        status_text = self._clean_status_text(
            detail_payload.get("statusText") or detail_payload.get("metaText")
        )
        project_type = self._infer_project_type(detail_payload, fallback=existing_type)
        scraped_at = self._current_timestamp()
        history_seed = list(existing_history or [])
        additions: List[StatusSnapshot] = []
        if status_text:
            additions.append(StatusSnapshot(status=status_text, observed_at=scraped_at))
        merged_history = (
            self._merge_status_history(history_seed, additions)
            if additions
            else history_seed
        )
        project_status = merged_history[-1].status if merged_history else status_text

        return {
            "bids": bids,
            "project_type": project_type,
            "project_status": project_status,
            "status_history": merged_history,
        }

    def _collect_bids(self, project_url: str, max_bids: Optional[int]) -> List[BidRecord]:
        details = self._collect_project_page_details(
            project_url,
            include_bids=True,
            max_bids=max_bids,
        )
        return details.get("bids", [])

    def _collect_bids_from_blobs(
        self, blobs: Sequence[str], max_bids: Optional[int]
    ) -> List[BidRecord]:
        for blob in blobs:
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            bid_records = self._extract_bids_from_blob(data, max_bids)
            if bid_records:
                return bid_records
        return []

    def _merge_status_history(
        self,
        base: Sequence[StatusSnapshot],
        additions: Sequence[StatusSnapshot],
    ) -> List[StatusSnapshot]:
        merged: List[StatusSnapshot] = list(base)
        seen = {(snap.status, snap.observed_at) for snap in merged}
        for snapshot in additions:
            key = (snapshot.status, snapshot.observed_at)
            if key in seen:
                continue
            merged.append(snapshot)
            seen.add(key)
        merged.sort(key=lambda snap: ((snap.observed_at or ""), snap.status or ""))
        return merged

    def _infer_project_type(
        self, payload: Dict[str, Any], *, fallback: Optional[str] = None
    ) -> Optional[str]:
        tokens: List[str] = []
        candidate_keys = (
            "project_type",
            "cardType",
            "statusText",
            "meta",
            "metaText",
            "auctionTypeText",
            "badges",
            "description",
            "title",
        )
        for key in candidate_keys:
            value = payload.get(key)
            if isinstance(value, str):
                if value:
                    tokens.append(value.lower())
            elif isinstance(value, (list, tuple, set)):
                tokens.extend(str(item).lower() for item in value if item)
        if any(re.search(r"sealed|private|confidential|hidden", text) for text in tokens):
            return "sealed"
        if any(re.search(r"open|public", text) for text in tokens):
            return "open"
        if fallback:
            return fallback
        if tokens:
            return "open"
        return None

    def _clean_status_text(self, status: Optional[str]) -> Optional[str]:
        if not status:
            return None
        cleaned = re.sub(r"\s+", " ", status).strip()
        return cleaned or None

    def _current_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _extract_bids_from_blob(self, data: Any, max_bids: Optional[int]) -> List[BidRecord]:
        bids: List[BidRecord] = []

        def traverse(node: Any) -> None:
            if isinstance(node, dict):
                if {"bids", "project"}.issubset(node.keys()):
                    bids.extend(self._convert_bid_payloads(node["bids"], max_bids))
                elif "bids" in node and isinstance(node["bids"], list):
                    bids.extend(self._convert_bid_payloads(node["bids"], max_bids))
                for value in node.values():
                    traverse(value)
            elif isinstance(node, list):
                for value in node:
                    traverse(value)

        traverse(data)
        return bids[:max_bids] if max_bids else bids

    def _convert_bid_payloads(
        self, payloads: Sequence[Any], max_bids: Optional[int]
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
) -> None:
    """Persist collected project data to JSON."""

    scraper = FreelancerScraper()
    scraper.dump_to_json(projects, output)


__all__ = [
    "FreelancerScraper",
    "ProjectSummary",
    "BidRecord",
    "StatusSnapshot",
    "EmployerProfile",
    "default_scraper_from_env",
    "save_projects_to_disk",
]
