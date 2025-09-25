# Freelancer-Research

This repository provides tooling to collect project- and bid-level micro data
from [Freelancer.com](https://www.freelancer.com) using Selenium. The resulting
datasets can be used to study freelancer welfare questions such as the presence
of winner's curse dynamics, access cliffs for new users, or differences in
employer behaviour across countries and project types.

## Features

- **Project harvesting** – capture project metadata including budget range,
  bid statistics, employer reputation, skills, and posted time.
- **Bid harvesting** – optionally collect the individual bids submitted to each
  project along with bidder reputation, bid amounts, milestone requests, and
  whether a bid won or was withdrawn.
- **Derived metrics** – compute summary statistics (e.g., share of bids within
  budget, mean bid relative to budget midpoint) that are useful starting points
  for welfare analysis.

## Requirements

- Python 3.9+
- Google Chrome or Chromium installed locally.
- A matching `chromedriver` binary accessible on your `PATH` or via the
  [`webdriver-manager`](https://github.com/SergeyPirogov/webdriver_manager)
  package.
- Python dependencies listed in `requirements.txt`:

  ```bash
  pip install -r requirements.txt
  ```

> ⚠️ **Legal note:** Make sure that your data collection complies with the
> Freelancer.com Terms of Service and that you respect rate limits. The provided
> script throttles API calls by default, but you should further adapt it to your
> research ethics guidelines and local regulations.

## Usage

Run the scraper via the module entry point:

```bash
python src/freelancer_scraper.py --query "data analysis" --limit 75 --include-bids
```

This command saves two JSON files in the current directory:

- `freelancer_projects.json` – project-level observations.
- `freelancer_bids.json` – bid-level observations (when `--include-bids` is set).

### Common options

| Flag | Description |
| ---- | ----------- |
| `--query` | Keyword search applied to project listings. |
| `--limit` | Maximum number of projects to download. |
| `--offset` | Offset into the listings result set (useful for pagination). |
| `--include-bids` | Fetch bid-level records for each project. |
| `--bids-limit` | Cap on the number of bids requested per project. |
| `--throttle` | Seconds to wait between API calls (default: 1s). |
| `--no-headless` | Run Chrome with a visible window for debugging. |
| `--driver-path` | Explicit path to a `chromedriver` binary. |

The scraper can also be imported as a module for custom workflows:

```python
from src.freelancer_scraper import FreelancerScraper

with FreelancerScraper(headless=True) as scraper:
    projects = scraper.search_projects(query="machine learning", limit=20)
    for project in projects:
        bids = scraper.fetch_project_bids(project.project_id)
        project.research_metrics.update(
            compute_bid_metrics(project, bids)
        )
```

## Output schema

Each project record contains (among others) the following fields:

- `project_id`, `title`, `seo_url`, `status`, `type`
- `budget_min`, `budget_max`, `currency_code`
- `average_bid`, `bid_count`
- `owner_country`, `owner_rating`, `owner_reviews`
- `skills` (list of skill tags)
- `research_metrics` (derived statistics like `budget_midpoint` and
  `avg_bid_to_mid_budget`)

Bid records include:

- `bid_id`, `bidder_id`, `bidder_username`
- `amount`, `currency_code`, `period_days`, `milestone_percent`
- `status`, `is_awarded`, `is_withdrawn`
- `bidder_country`, `bidder_rating`, `bidder_reviews`

User records (accessible via `fetch_user_details`) provide:

- `user_id`, `username`, `display_name`
- `country`, `city`, `registration_date`
- `rating`, `reviews`, `earnings`, `spent`

These datasets can be combined to study topics such as bidder competition,
winner's curse indicators (compare winning bids to budget midpoints), or access
cliffs (e.g., distribution of bidder reputation across early bids).

## Ethics and research guidance

- Respect the platform's robots.txt and rate-limiting expectations.
- Store collected data securely and anonymise sensitive attributes where
  appropriate.
- Combine platform-sourced data with your own survey or experimental data to
  answer richer welfare questions.

## License

This project is provided for research purposes without warranty. Always verify
that your use complies with applicable laws and platform policies.
