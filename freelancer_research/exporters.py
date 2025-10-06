"""Utilities for exporting scraped Freelancer data to common formats."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Optional

from .scraper import BidRecord, ProjectSummary


def export_projects_to_csv(projects: Iterable[ProjectSummary], output_path: Path) -> None:
    """Write high-level project summaries to a CSV file."""

    fieldnames = [
        "project_id",
        "title",
        "url",
        "description",
        "budget_min",
        "budget_max",
        "currency_code",
        "currency_symbol",
        "bids_count",
        "average_bid",
        "posted_time",
        "project_type",
        "skills",
        "employer_name",
        "employer_rating",
        "employer_review_count",
        "employer_location",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for project in projects:
            writer.writerow(
                {
                    "project_id": project.project_id,
                    "title": project.title,
                    "url": project.url,
                    "description": project.description,
                    "budget_min": project.budget_min,
                    "budget_max": project.budget_max,
                    "currency_code": project.currency_code,
                    "currency_symbol": project.currency_symbol,
                    "bids_count": project.bids_count,
                    "average_bid": project.average_bid,
                    "posted_time": project.posted_time,
                    "project_type": _gao2025_project_type(project.project_type),
                    "skills": "|".join(project.skills),
                    "employer_name": project.employer.name,
                    "employer_rating": project.employer.rating,
                    "employer_review_count": project.employer.review_count,
                    "employer_location": project.employer.location,
                }
            )


def export_bids_to_csv(projects: Iterable[ProjectSummary], output_path: Path) -> None:
    """Write all captured bids to a CSV file."""

    fieldnames = [
        "project_id",
        "username",
        "rating",
        "review_count",
        "amount",
        "currency_code",
        "delivery_days",
        "status",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for project in projects:
            for bid in _flatten_bids(project.project_id, project.bids):
                writer.writerow(bid)


def _flatten_bids(project_id: str, bids: List[BidRecord]):
    for bid in bids:
        yield {
            "project_id": project_id,
            "username": bid.username,
            "rating": bid.rating,
            "review_count": bid.review_count,
            "amount": bid.amount,
            "currency_code": bid.currency_code,
            "delivery_days": bid.delivery_days,
            "status": bid.status,
        }


__all__ = ["export_projects_to_csv", "export_bids_to_csv"]


def _gao2025_project_type(raw_value: Optional[str]) -> Optional[str]:
    if not raw_value:
        return None

    lowered = raw_value.strip().lower()
    if not lowered:
        return None

    if "sealed" in lowered:
        return "sealed"

    if any(token in lowered for token in ("standard", "open")):
        return "standard"

    return raw_value
