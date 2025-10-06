# Freelancer Welfare Research Scraper

This repository contains a Selenium-based toolkit for collecting public project
and bid information from [Freelancer.com](https://www.freelancer.com/). The
utilities are designed to support labour economics and market design research
questions such as the presence of a winner's curse, access cliffs for new
entrants, or how employer reputation interacts with bidding behaviour.

## Key Features

- **Search automation** – Iterates over keyword queries and paginated result
  sets to gather structured project-level data (budgets, bid counts, skills,
  employer reputation snippets, etc.).
- **Bid harvesting** – Optionally follows each project to capture bidder level
  information (bid amount, promised delivery time, bidder reputation metrics),
  which is essential for studying phenomena like the winner's curse.
- **Flexible exports** – Save results as JSON or CSV files for downstream
  analysis in Python, R, or statistical packages.
- **Longitudinal observations** – Stamp each scrape with UTC timestamps and an
  optional run identifier so repeated captures can be stitched into time-series
  panels.
- **Polite defaults** – Headless browsing with randomised delays, plus hooks to
  authenticate via environment variables when deeper data requires login.

## Installation

1. Ensure you have Python 3.10+ installed.
2. Install the dependencies in a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   The scraper relies on Google Chrome. Install it locally and make sure it is
   available on your `PATH`. `webdriver-manager` will download the matching
   ChromeDriver binary automatically.

## Usage

Run the CLI from the project root to collect project data for one or more
keywords:

```bash
python -m freelancer_research.cli \
  --search "data entry" \
  --search "python scraping" \
  --pages 3 \
  --results-per-page 50 \
  --include-bids \
  --max-bids 40 \
  --run-id "$(date -u +"%Y%m%dT%H%M%SZ")" \
  --append \
  --output data/projects.jsonl \
  --bids-output data/bids.csv
```

### Authentication

Certain project details (especially full bid lists) may require an authenticated
session. Provide credentials via command-line flags or environment variables
before running the scraper:

```bash
export FREELANCER_EMAIL="your-email@example.com"
export FREELANCER_PASSWORD="your-password"
python -m freelancer_research.cli --search "machine learning" --include-bids
```

### Output Structure

- **Project summaries** include the project id, title, description, budget
  bounds, bid counts, employer reputation snippets, and detected skills. When
  exported to CSV, skills are pipe-delimited for easier parsing. Additional
  metadata includes an `observed_at` timestamp, the optional `observation_run_id`
  set via `--run-id`, and a `status_events` array capturing observed statuses
  and bid counts over time.
- **Bid level records** (optional) contain project id, bidder username,
  reputation metrics, offered amount, currency code, stated delivery days, and
  status. They are enriched with `observed_at` timestamps and the originating
  run identifier, making it easier to align bid changes with project level
  observations. These fields are sufficient to construct panels for welfare
  analyses such as bid dispersion, newcomer versus incumbent success, or
  estimating access cliffs.

### Research Considerations

- Respect [Freelancer.com's terms of service](https://www.freelancer.com/info/terms)
  and rate limits. The provided delays are conservative defaults, but you
  should still monitor request volume and adapt if you scale up.
- Store scraped data securely and anonymise user-identifying information when
  necessary to comply with ethical research guidelines.
- Combining project budgets with realised bid amounts allows you to test for
  winner's curse patterns; matching bid histories with bidder tenure helps
  quantify access cliffs for new freelancers.

## Development

- The codebase intentionally avoids storing credentials. Use environment
  variables or a local `.env` file (not committed) if you need to automate
  authentication.
- Run `python -m compileall .` before committing changes to ensure syntax
  correctness.

## Building a Longitudinal Dataset

Recurring executions let you monitor how projects evolve (status changes,
incoming bids, budget adjustments). Two common scheduling approaches are shown
below. Replace the sample search term and output paths as needed.

### Cron

Add an entry with `crontab -e` to run the scraper every hour, appending to JSONL
and CSV outputs while stamping each run with a unique identifier:

```
0 * * * * cd /path/to/Freelancer-Research && \
  /usr/bin/env python -m freelancer_research.cli \
    --search "data entry" \
    --pages 2 \
    --results-per-page 50 \
    --include-bids \
    --run-id "$(date -u +\"%Y%m%dT%H%M%SZ\")" \
    --append \
    --output data/projects.jsonl \
    --bids-output data/bids.csv >> logs/scraper.log 2>&1
```

### systemd timers

Create a service unit (e.g. `/etc/systemd/system/freelancer-scraper.service`):

```
[Unit]
Description=Freelancer.com longitudinal scraper

[Service]
Type=oneshot
WorkingDirectory=/path/to/Freelancer-Research
ExecStart=/usr/bin/env python -m freelancer_research.cli \
  --search "data entry" \
  --pages 2 \
  --include-bids \
  --results-per-page 50 \
  --run-id "$(date -u +\"%Y%m%dT%H%M%SZ\")" \
  --append \
  --output data/projects.jsonl \
  --bids-output data/bids.csv
```

Then define the timer (`/etc/systemd/system/freelancer-scraper.timer`):

```
[Unit]
Description=Run Freelancer scraper hourly

[Timer]
OnBootSec=5m
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start the timer with:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-scraper.timer
```

The `Persistent=true` flag ensures missed runs execute on boot, keeping your
time-series dataset up to date.

## License

This repository is provided for academic research purposes. Ensure your use of
Freelancer.com data complies with their policies and with your institution's
ethics requirements.
