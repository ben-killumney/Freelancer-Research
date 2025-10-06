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
- **Auction format detection** – Flags whether each project is an open or
  sealed auction, matching the variables used by Gao et al. (2025).
- **Status snapshots** – Records the project status (open, closed, frozen, etc.)
  at the scrape timestamp so that single-sweep collections can be aligned with
  longitudinal designs.
- **Flexible exports** – Save results as JSON or CSV files for downstream
  analysis in Python, R, or statistical packages.
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
  --output data/projects.json \
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
  bounds, bid counts, employer reputation snippets, detected skills, auction
  format (open vs sealed), and the observed project status plus timestamped
  history entries. When exported to CSV, skills are pipe-delimited for easier
  parsing and status histories are serialised as JSON for reproducibility.
- **Bid level records** (optional) contain project id, bidder username,
  reputation metrics, offered amount, currency code, stated delivery days, and
  status. These fields are sufficient to construct panels for welfare analyses
  such as bid dispersion, newcomer versus incumbent success, or estimating
  access cliffs.

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

## License

This repository is provided for academic research purposes. Ensure your use of
Freelancer.com data complies with their policies and with your institution's
ethics requirements.
