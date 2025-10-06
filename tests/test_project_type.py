import csv
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from freelancer_research.exporters import export_projects_to_csv
from freelancer_research.scraper import FreelancerScraper, ProjectSummary


@pytest.fixture
def scraper() -> FreelancerScraper:
    # Driver is lazily created so leaving it as None is fine for unit testing.
    return FreelancerScraper(headless=True, driver=None)


def _base_payload(**overrides):
    payload = {
        # NOTE: The scraper's regex looks for a literal "\\d" prefix in the URL when
        # extracting project identifiers, so the fixture URL mirrors that format.
        "url": "https://www.freelancer.com/projects/python/test-project-\\dddddd",
        "title": "Example Project",
        "skills": [],
    }
    payload.update(overrides)
    return payload


def test_payload_to_summary_sets_sealed_project_type(scraper: FreelancerScraper):
    payload = _base_payload(projectType="Sealed")
    summary = scraper._payload_to_summary(payload)
    assert summary is not None
    assert summary.project_type == "sealed"


def test_payload_to_summary_sets_standard_project_type(scraper: FreelancerScraper):
    payload = _base_payload(projectType="Standard Project")
    summary = scraper._payload_to_summary(payload)
    assert summary is not None
    assert summary.project_type == "standard"


def test_exporter_translates_project_type(tmp_path: Path):
    project = ProjectSummary(
        project_id="123456",
        title="Example",
        url="https://www.freelancer.com/projects/python/example-\\dddddd",
        project_type="Sealed Project",
    )
    csv_path = tmp_path / "projects.csv"
    export_projects_to_csv([project], csv_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    assert rows[0]["project_type"] == "sealed"
