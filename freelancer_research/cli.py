"""Command line interface for the Freelancer.com welfare data scraper."""

from __future__ import annotations

import argparse
import json
import os
import random
from pathlib import Path
from typing import Iterable, List, Sequence

from .exporters import export_bids_to_csv, export_projects_to_csv
from .scraper import FreelancerScraper, ProjectSummary


def _load_terms(search_terms: Sequence[str], search_file: Path | None) -> List[str]:
    terms: List[str] = list(search_terms)
    if search_file and search_file.exists():
        with search_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                cleaned = line.strip()
                if cleaned and not cleaned.startswith("#"):
                    terms.append(cleaned)
    unique_terms = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    return unique_terms


def _export_data(projects: Iterable[ProjectSummary], args: argparse.Namespace) -> None:
    output_path: Path = args.output
    if output_path.suffix.lower() == ".json":
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump([project.to_dict() for project in projects], fh, indent=2)
    elif output_path.suffix.lower() == ".csv":
        export_projects_to_csv(projects, output_path)
    else:
        raise ValueError("Unsupported output format. Use .json or .csv")

    if args.bids_output:
        export_bids_to_csv(projects, args.bids_output)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Collect project and bid data from Freelancer.com for welfare "
            "economics research questions such as access cliffs or winner's curse."
        )
    )
    parser.add_argument(
        "--search",
        action="append",
        dest="search_terms",
        default=[],
        help="Keyword to search for. Provide multiple times for multiple queries.",
    )
    parser.add_argument(
        "--search-file",
        type=Path,
        help="Optional file containing one search term per line.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=1,
        help="Number of result pages to fetch per search term.",
    )
    parser.add_argument(
        "--results-per-page",
        type=int,
        default=20,
        help="How many projects to request per page (Freelancer supports up to ~100).",
    )
    parser.add_argument(
        "--include-bids",
        action="store_true",
        help="Follow each project link and capture individual bid information.",
    )
    parser.add_argument(
        "--max-bids",
        type=int,
        help="Maximum number of bids to retain per project.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the browser in headless mode (default: True).",
    )
    parser.add_argument(
        "--email",
        help=(
            "Freelancer account email. Optional, but helpful for scraping "
            "bidder details that are behind authentication."
        ),
    )
    parser.add_argument(
        "--password",
        help="Freelancer account password. If omitted the FREELANCER_PASSWORD env var is used.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/freelancer_projects.json"),
        help="Destination file (.json or .csv) for the project summaries.",
    )
    parser.add_argument(
        "--bids-output",
        type=Path,
        help="Optional CSV file to receive bid level data when --include-bids is used.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Seed for the random delay generator to ensure reproducible pacing.",
    )

    args = parser.parse_args(argv)
    search_terms = _load_terms(args.search_terms, args.search_file)
    if not search_terms:
        parser.error("Provide at least one --search term or a --search-file.")

    if args.seed is not None:
        random.seed(args.seed)

    email = args.email or os.getenv("FREELANCER_EMAIL")
    password = args.password or os.getenv("FREELANCER_PASSWORD")

    with FreelancerScraper(headless=args.headless) as scraper:
        if email and password:
            scraper.login(email, password)
        projects = scraper.collect_projects(
            search_terms,
            pages_per_term=args.pages,
            results_per_page=args.results_per_page,
            include_bids=args.include_bids,
            max_bids=args.max_bids,
        )
    _export_data(projects, args)


if __name__ == "__main__":
    main()
