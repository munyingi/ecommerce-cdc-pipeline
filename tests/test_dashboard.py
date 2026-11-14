"""
Smoke test: open every dashboard page against the DuckDB snapshot, for each
period option, and fail if any page raises an error. Runs in CI on every push.

Run locally:  pytest -q tests/test_dashboard.py
"""
import os

import pytest
from streamlit.testing.v1 import AppTest

os.environ["DASHBOARD_BACKEND"] = "duckdb"   # CI has no Postgres warehouse

PAGES = ["overview", "customers", "products", "operations", "data_pipeline"]
PERIODS = ["Last 30 days", "Last 90 days", "Last 365 days", "All time"]


@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page, period):
    at = AppTest.from_file(f"dashboard/views/{page}.py", default_timeout=120)
    at.session_state["period_choice"] = period
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title, "page should render a title"


def test_navigation_between_pages():
    at = AppTest.from_file("dashboard/app.py", default_timeout=120)
    at.run()
    assert at.title[0].value == "Executive overview"
    for page in PAGES[1:]:
        at.switch_page(f"views/{page}.py").run()
        assert not at.exception, [e.value for e in at.exception]
