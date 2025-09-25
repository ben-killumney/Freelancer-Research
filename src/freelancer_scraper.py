"""Freelancer.com micro data collection tooling using Selenium.

This module provides a :class:`FreelancerScraper` helper that relies on a
headless Chrome webdriver.  The scraper executes the same internal API calls
that power the Freelancer web application, which makes it possible to download
structured JSON data for research.  The main entry point can be used as a
command line utility to harvest project and bid level data that is relevant to
questions around freelancer welfare, such as the existence of a winner's curse
or access cliffs in bidding behaviour.

The scraper purposefully throttles API calls with a user-configurable pause to
avoid overwhelming the platform and to mimic the browsing speed of a human.
Researchers can further extend the module to capture additional features, such
as employer history or freelancer earnings, by adding new API calls in the
``FreelancerScraper`` class.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from selenium import webdriver
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

try:  # Optional dependency used to discover the correct driver binary.
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pragma: no cover - only triggered when dependency missing.
    ChromeDriverManager = None  # type: ignore


FREELANCER_BASE_URL = "https://www.freelancer.com"
DEFAULT_PROJECT_ENDPOINT = (
    f"{FREELANCER_BASE_URL}/api/projects/0.1/projects/active/"
)
PROJECT_DETAILS_ENDPOINT = (
    f"{FREELANCER_BASE_URL}/api/projects/0.1/projects/{{project_id}}/"
)
PROJECT_BIDS_ENDPOINT = (
    f"{FREELANCER_BASE_URL}/api/projects/0.1/projects/{{project_id}}/bids/"
)
USER_DETAILS_ENDPOINT = (
    f"{FREELANCER_BASE_URL}/api/users/0.1/users/{{user_id}}/"
)


class FreelancerScraperError(RuntimeError):
    """Raised when an API call executed through Selenium fails."""


@dataclass
class ProjectRecord:
    """Representation of a project listing returned by the API."""

    project_id: int
    title: str
    seo_url: Optional[str]
    type: Optional[str]
    status: Optional[str]
    submitted_at: Optional[str]
    budget_min: Optional[float]
    budget_max: Optional[float]
    currency_code: Optional[str]
    average_bid: Optional[float]
    bid_count: Optional[int]
    description: Optional[str]
    skills: Sequence[str]
    owner_id: Optional[int]
    owner_username: Optional[str]
    owner_country: Optional[str]
    owner_rating: Optional[float]
    owner_reviews: Optional[int]
    upgrades: Sequence[str]
    featured: bool
    research_metrics: Dict[str, Any]


@dataclass
class BidRecord:
    """Representation of a bid submitted for a project."""

    project_id: int
    bid_id: Optional[int]
    bidder_id: Optional[int]
    bidder_username: Optional[str]
    bidder_country: Optional[str]
    submitted_at: Optional[str]
    amount: Optional[float]
    currency_code: Optional[str]
    period_days: Optional[int]
    milestone_percent: Optional[float]
    status: Optional[str]
    is_awarded: bool
    is_withdrawn: bool
    bidder_rating: Optional[float]
    bidder_reviews: Optional[int]


@dataclass
class UserRecord:
    """Simplified employer or freelancer profile information."""

    user_id: int
    username: Optional[str]
    display_name: Optional[str]
    country: Optional[str]
    city: Optional[str]
    registration_date: Optional[str]
    rating: Optional[float]
    reviews: Optional[int]
    earnings: Optional[float]
    spent: Optional[float]


class FreelancerScraper:
    """High level helper around Selenium for Freelancer.com.

    The scraper relies on the same API endpoints that power the Freelancer
    single-page application.  The :meth:`search_projects` method yields high
    level listing metadata, while :meth:`fetch_project_bids` and
    :meth:`fetch_user_details` expose bid and profile level information that is
    useful for micro data analysis.
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        driver_path: Optional[str] = None,
        page_load_timeout: int = 30,
        throttle_seconds: float = 1.0,
    ) -> None:
        self._driver = self._init_driver(headless=headless, driver_path=driver_path)
        self._driver.set_page_load_timeout(page_load_timeout)
        self._throttle_seconds = max(throttle_seconds, 0.0)
        self._origin_ready = False

    # ------------------------------------------------------------------
    # Driver lifecycle utilities
    @staticmethod
    def _init_driver(*, headless: bool, driver_path: Optional[str]) -> webdriver.Chrome:
        options = Options()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1200")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-features=VizDisplayCompositor")

        service: Service
        if driver_path:
            service = Service(driver_path)
        else:
            service = FreelancerScraper._auto_service()

        try:
            return webdriver.Chrome(service=service, options=options)
        except WebDriverException as exc:  # pragma: no cover - requires webdriver
            raise FreelancerScraperError(
                "Unable to start Chrome webdriver. Ensure that Chrome/Chromium "
                "is installed and that a matching chromedriver is available."
            ) from exc

    @staticmethod
    def _auto_service() -> Service:
        """Discover a chromedriver binary if webdriver_manager is available."""

        if ChromeDriverManager is None:
            # Fallback to relying on the chromedriver being discoverable in PATH.
            return Service()
        try:  # pragma: no cover - requires network to download driver.
            return Service(ChromeDriverManager().install())
        except Exception as exc:
            raise FreelancerScraperError(
                "webdriver_manager failed to download a chromedriver binary. "
                "Provide an explicit --driver-path pointing to a local "
                "chromedriver executable."
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    def close(self) -> None:
        """Tear down the webdriver instance."""

        try:
            self._driver.quit()
        finally:
            self._origin_ready = False

    def __enter__(self) -> "FreelancerScraper":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    def search_projects(
        self,
        *,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        job_ids: Optional[Sequence[int]] = None,
        languages: Optional[Sequence[str]] = None,
        countries: Optional[Sequence[str]] = None,
        full_description: bool = False,
        include_local_details: bool = True,
        sort_field: str = "time_submitted",
        sort_order: str = "desc",
    ) -> List[ProjectRecord]:
        """Return a list of project summaries.

        Parameters mirror the query parameters used by the Freelancer front end.
        """

        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "full_description": str(full_description).lower(),
            "job_details": "true",
            "local_details": str(include_local_details).lower(),
            "location_details": "true",
            "project_collaboration": "true",
            "sort_field": sort_field,
            "sort_order": sort_order,
        }
        if query:
            params["query"] = query
        if job_ids:
            params["jobs[]"] = list(job_ids)
        if languages:
            params["languages[]"] = list(languages)
        if countries:
            params["countries[]"] = list(countries)

        data = self._call_api(DEFAULT_PROJECT_ENDPOINT, params)
        projects = data.get("result", {}).get("projects", [])
        return [self._parse_project(project) for project in projects]

    def fetch_project_details(self, project_id: int) -> ProjectRecord:
        """Return the most recent details for an individual project."""

        endpoint = PROJECT_DETAILS_ENDPOINT.format(project_id=project_id)
        data = self._call_api(
            endpoint,
            {
                "full_description": "true",
                "job_details": "true",
                "location_details": "true",
                "project_collaboration": "true",
            },
        )
        project = data.get("result", {}).get("project") or data.get("result")
        if not project:
            raise FreelancerScraperError(
                f"Unexpected response payload when requesting project {project_id}."
            )
        return self._parse_project(project)

    def fetch_project_bids(
        self,
        project_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> List[BidRecord]:
        """Fetch bid level information for a project."""

        endpoint = PROJECT_BIDS_ENDPOINT.format(project_id=project_id)
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "compact": "true",
            "project_collaboration": "true",
        }
        if status:
            params["status"] = status

        data = self._call_api(endpoint, params)
        bids = data.get("result", {}).get("bids", [])
        return [self._parse_bid(project_id, bid) for bid in bids]

    def fetch_user_details(self, user_id: int) -> UserRecord:
        """Fetch profile level information for an employer or freelancer."""

        endpoint = USER_DETAILS_ENDPOINT.format(user_id=user_id)
        data = self._call_api(
            endpoint,
            {
                "status": "active",
                "reputation": "true",
                "profile_description": "true",
                "earnings_statistics": "true",
            },
        )
        user = data.get("result", {}).get("user") or data.get("result")
        if not user:
            raise FreelancerScraperError(
                f"Unexpected response payload when requesting user {user_id}."
            )
        return self._parse_user(user)

    # ------------------------------------------------------------------
    # Parsing helpers
    def _parse_project(self, payload: Dict[str, Any]) -> ProjectRecord:
        budget = payload.get("budget", {})
        currency = payload.get("currency", {})
        bid_stats = payload.get("bid_stats", {}) or payload.get("bidStats", {})
        owner = payload.get("owner") or payload.get("employer") or {}
        reputation = owner.get("reputation", {})
        overall_rep = reputation.get("overall") or reputation.get("overall_stats", {})

        submitted_at = _timestamp_to_iso(payload.get("time_submitted"))
        description = (
            payload.get("preview_description")
            or payload.get("description")
            or payload.get("seo_description")
        )

        upgrades = _parse_upgrades(payload.get("upgrades"))
        research_metrics = self._derive_project_metrics(payload, bid_stats)

        return ProjectRecord(
            project_id=payload.get("id"),
            title=payload.get("title"),
            seo_url=payload.get("seo_url"),
            type=payload.get("type") or payload.get("project_type"),
            status=payload.get("status"),
            submitted_at=submitted_at,
            budget_min=_safe_float(budget.get("minimum")),
            budget_max=_safe_float(budget.get("maximum")),
            currency_code=currency.get("code") or currency.get("sign"),
            average_bid=_safe_float(
                bid_stats.get("bid_avg")
                or bid_stats.get("avg_bid")
                or bid_stats.get("bidAverage")
            ),
            bid_count=_safe_int(
                bid_stats.get("bid_count")
                or bid_stats.get("count")
                or bid_stats.get("bidCount")
            ),
            description=description,
            skills=[job.get("name") for job in payload.get("jobs", []) if job.get("name")],
            owner_id=owner.get("id"),
            owner_username=owner.get("username") or owner.get("public_name"),
            owner_country=_extract_country(owner),
            owner_rating=_safe_float(
                (overall_rep or {}).get("overall")
                or (overall_rep or {}).get("rating")
            ),
            owner_reviews=_safe_int(
                (overall_rep or {}).get("count")
                or (overall_rep or {}).get("reviews")
            ),
            upgrades=upgrades,
            featured="featured" in upgrades,
            research_metrics=research_metrics,
        )

    def _derive_project_metrics(
        self, payload: Dict[str, Any], bid_stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        budget = payload.get("budget", {})
        min_budget = _safe_float(budget.get("minimum"))
        max_budget = _safe_float(budget.get("maximum"))
        bid_avg = _safe_float(
            bid_stats.get("bid_avg")
            or bid_stats.get("avg_bid")
            or bid_stats.get("bidAverage")
        )
        bid_count = _safe_int(
            bid_stats.get("bid_count")
            or bid_stats.get("count")
            or bid_stats.get("bidCount")
        )

        metrics: Dict[str, Any] = {}
        if min_budget is not None and max_budget is not None:
            mid_budget = (min_budget + max_budget) / 2.0
            metrics["budget_midpoint"] = mid_budget
            metrics["budget_range"] = max_budget - min_budget
            if bid_avg is not None and mid_budget:
                metrics["avg_bid_to_mid_budget"] = bid_avg / mid_budget
        if bid_count is not None:
            metrics["bid_count"] = bid_count
        if bid_avg is not None:
            metrics["average_bid"] = bid_avg

        return metrics

    def _parse_bid(self, project_id: int, payload: Dict[str, Any]) -> BidRecord:
        amount_info = payload.get("amount") or {}
        bidder = payload.get("bidder") or payload.get("freelancer") or {}
        reputation = bidder.get("reputation", {})
        overall_rep = reputation.get("overall") or reputation.get("overall_stats", {})

        amount = _safe_float(
            amount_info.get("amount")
            or amount_info.get("value")
            or payload.get("amount")
        )
        currency = amount_info.get("currency", {})

        submitted_at = _timestamp_to_iso(payload.get("time_submitted") or payload.get("submit_time"))

        return BidRecord(
            project_id=project_id,
            bid_id=_safe_int(payload.get("id")),
            bidder_id=_safe_int(bidder.get("id")),
            bidder_username=bidder.get("username") or bidder.get("public_name"),
            bidder_country=_extract_country(bidder),
            submitted_at=submitted_at,
            amount=amount,
            currency_code=currency.get("code") or currency.get("sign"),
            period_days=_safe_int(payload.get("period") or payload.get("duration")),
            milestone_percent=_safe_float(
                payload.get("milestone_percentage")
                or payload.get("milestonePercentage")
            ),
            status=payload.get("status"),
            is_awarded=bool(payload.get("is_awarded") or payload.get("awarded")),
            is_withdrawn=bool(payload.get("is_withdrawn") or payload.get("withdrawn")),
            bidder_rating=_safe_float(
                (overall_rep or {}).get("overall")
                or (overall_rep or {}).get("rating")
            ),
            bidder_reviews=_safe_int(
                (overall_rep or {}).get("count")
                or (overall_rep or {}).get("reviews")
            ),
        )

    def _parse_user(self, payload: Dict[str, Any]) -> UserRecord:
        reputation = payload.get("reputation", {})
        overall_rep = reputation.get("overall") or reputation.get("overall_stats", {})
        location = payload.get("location", {})
        city = None
        if isinstance(location, dict):
            city = location.get("city") or location.get("name")

        earnings = payload.get("earnings_statistics", {})
        if not isinstance(earnings, dict):
            earnings = {}

        registration = _timestamp_to_iso(payload.get("registration_date") or payload.get("registered"))

        country = _extract_country(payload)

        return UserRecord(
            user_id=payload.get("id"),
            username=payload.get("username"),
            display_name=payload.get("public_name") or payload.get("display_name"),
            country=country,
            city=city,
            registration_date=registration,
            rating=_safe_float(
                (overall_rep or {}).get("overall")
                or (overall_rep or {}).get("rating")
            ),
            reviews=_safe_int(
                (overall_rep or {}).get("count")
                or (overall_rep or {}).get("reviews")
            ),
            earnings=_safe_float(earnings.get("earnings") or earnings.get("total_earnings")),
            spent=_safe_float(earnings.get("spent") or earnings.get("total_spent")),
        )

    # ------------------------------------------------------------------
    def _call_api(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute an authenticated fetch call inside the browser context."""

        if params is None:
            params = {}

        if not self._origin_ready:
            # Visiting the base page once ensures future fetch calls share the
            # correct origin for CORS purposes.
            self._driver.get(FREELANCER_BASE_URL)
            self._origin_ready = True
            time.sleep(1.0)

        query_string = _encode_query(params)

        script = """
            const [url, query] = arguments;
            const callback = arguments[arguments.length - 1];
            const fullUrl = query ? `${url}?${query}` : url;
            fetch(fullUrl, { credentials: 'include' })
                .then((response) => response.text())
                .then((text) => callback(text))
                .catch((error) => callback(JSON.stringify({
                    __freelancer_scraper_error: error && (error.message || String(error))
                })));
        """
        try:
            raw_response = self._driver.execute_async_script(script, endpoint, query_string)
        except JavascriptException as exc:
            raise FreelancerScraperError(
                f"Failed to execute fetch for {endpoint}: {exc.msg}"
            ) from exc

        time.sleep(self._throttle_seconds)

        if not raw_response:
            raise FreelancerScraperError(f"Empty response received from {endpoint}")

        try:
            data = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise FreelancerScraperError(
                f"Non JSON response received from {endpoint}: {raw_response[:200]}"
            ) from exc

        if isinstance(data, dict) and data.get("__freelancer_scraper_error"):
            raise FreelancerScraperError(data["__freelancer_scraper_error"])
        if isinstance(data, dict) and data.get("status") == "error":
            message = data.get("message") or data.get("error") or "Unknown API error"
            raise FreelancerScraperError(message)

        return data


# ----------------------------------------------------------------------
# Helper utilities

def _encode_query(params: Dict[str, Any]) -> str:
    from urllib.parse import urlencode

    serialisable: Dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            serialisable[key] = value
        else:
            serialisable[key] = str(value)
    return urlencode(serialisable, doseq=True)


def _parse_upgrades(upgrades_payload: Any) -> List[str]:
    if isinstance(upgrades_payload, dict):
        # Older API responses expose upgrades as a mapping of flag to bool.
        return [name for name, enabled in upgrades_payload.items() if enabled]
    if isinstance(upgrades_payload, (list, tuple)):
        upgrades: List[str] = []
        for item in upgrades_payload:
            if isinstance(item, str):
                upgrades.append(item)
            elif isinstance(item, dict) and item.get("name"):
                upgrades.append(str(item.get("name")))
        return upgrades
    return []


def _extract_country(payload: Dict[str, Any]) -> Optional[str]:
    country_info = payload.get("country") or payload.get("location")
    if isinstance(country_info, dict):
        if country_info.get("name"):
            return country_info.get("name")
        nested = country_info.get("country")
        if isinstance(nested, dict) and nested.get("name"):
            return nested.get("name")
    return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(timestamp):
        return None

    if timestamp > 1e12:  # Handle millisecond precision.
        timestamp /= 1000.0
    try:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    return dt.isoformat()


# ----------------------------------------------------------------------
# Research metrics helpers

def compute_bid_metrics(
    project: ProjectRecord,
    bids: Sequence[BidRecord],
) -> Dict[str, Any]:
    """Derive statistics that are useful for welfare research questions."""

    amounts = [bid.amount for bid in bids if isinstance(bid.amount, (int, float))]
    amounts = [float(amount) for amount in amounts if amount is not None]
    if not amounts:
        return {}

    amounts.sort()
    metrics: Dict[str, Any] = {
        "min_bid": min(amounts),
        "max_bid": max(amounts),
        "median_bid": statistics.median(amounts),
        "mean_bid": statistics.mean(amounts),
    }
    if len(amounts) > 1:
        metrics["std_bid"] = statistics.pstdev(amounts)

    budget_min = project.budget_min
    budget_max = project.budget_max
    mid_budget = None
    if budget_min is not None and budget_max is not None:
        mid_budget = (budget_min + budget_max) / 2.0
        metrics["budget_midpoint"] = mid_budget
        metrics["budget_range"] = budget_max - budget_min

    if mid_budget and mid_budget > 0:
        metrics["mean_bid_to_budget_mid"] = metrics["mean_bid"] / mid_budget
        metrics["median_bid_to_budget_mid"] = metrics["median_bid"] / mid_budget

    if budget_min is not None and budget_max is not None and budget_max >= budget_min:
        within_budget = [a for a in amounts if budget_min <= a <= budget_max]
        below_budget = [a for a in amounts if a < budget_min]
        above_budget = [a for a in amounts if a > budget_max]
        total = len(amounts)
        metrics["share_within_budget"] = len(within_budget) / total
        metrics["share_below_budget"] = len(below_budget) / total
        metrics["share_above_budget"] = len(above_budget) / total

    awarded_bids = [bid for bid in bids if bid.is_awarded]
    if awarded_bids:
        awarded_amounts = [bid.amount for bid in awarded_bids if bid.amount is not None]
        if awarded_amounts:
            metrics["awarded_bid_min"] = min(awarded_amounts)
            metrics["awarded_bid_max"] = max(awarded_amounts)
            metrics["awarded_bid_mean"] = statistics.mean(awarded_amounts)
            if mid_budget and mid_budget > 0:
                metrics["awarded_bid_mean_to_mid"] = metrics["awarded_bid_mean"] / mid_budget

    return metrics


# ----------------------------------------------------------------------
# Command line interface

def run_cli(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect project and bid micro data from Freelancer.com using Selenium."
        )
    )
    parser.add_argument(
        "--query",
        help="Keyword search to filter projects (e.g. 'data analysis').",
        default=None,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of projects to retrieve.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Offset to start retrieving projects from.",
    )
    parser.add_argument(
        "--include-bids",
        action="store_true",
        help="Also fetch bid level data for each project.",
    )
    parser.add_argument(
        "--bids-limit",
        type=int,
        default=50,
        help="Maximum number of bids to fetch per project.",
    )
    parser.add_argument(
        "--throttle",
        type=float,
        default=1.0,
        help="Delay in seconds between API calls to remain polite.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("freelancer_projects.json"),
        help="Path to write the project dataset to (JSON).",
    )
    parser.add_argument(
        "--bids-output",
        type=Path,
        default=Path("freelancer_bids.json"),
        help="Path to write the bids dataset to (JSON).",
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=True,
        help="Run Chrome in headless mode (default).",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Run Chrome with a visible window (useful for debugging).",
    )
    parser.add_argument(
        "--driver-path",
        help="Optional explicit path to a chromedriver binary.",
    )

    args = parser.parse_args(argv)

    projects: List[ProjectRecord] = []
    all_bids: List[BidRecord] = []

    with FreelancerScraper(
        headless=args.headless,
        driver_path=args.driver_path,
        throttle_seconds=args.throttle,
    ) as scraper:
        remaining = args.limit
        offset = args.offset
        while remaining > 0:
            batch_size = min(remaining, 50)
            batch = scraper.search_projects(
                query=args.query,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            projects.extend(batch)
            offset += len(batch)
            remaining -= len(batch)

            if args.include_bids:
                for project in batch:
                    bids = scraper.fetch_project_bids(
                        project.project_id,
                        limit=args.bids_limit,
                    )
                    if bids:
                        metrics = compute_bid_metrics(project, bids)
                        project.research_metrics.update(metrics)
                        all_bids.extend(bids)

    _write_json(args.output, [asdict(project) for project in projects])

    if args.include_bids:
        _write_json(args.bids_output, [asdict(bid) for bid in all_bids])

    print(
        f"Saved {len(projects)} projects to {args.output}"
        + (
            f" and {len(all_bids)} bids to {args.bids_output}"
            if args.include_bids
            else ""
        )
    )
    return 0


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(run_cli())
