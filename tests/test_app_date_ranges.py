"""The dashboard must draw without errors for any date range, however narrow.

Runs the whole app headlessly with Streamlit's AppTest. Slow compared with the
unit tests (about 20 seconds), because every case redraws all seven tabs.
"""

from datetime import date
from pathlib import Path

import pytest

streamlit_testing = pytest.importorskip("streamlit.testing.v1")

APP_PATH = str(Path(__file__).resolve().parents[1] / "app" / "app.py")

DATE_RANGES = {
    "full year": (date(2025, 10, 10), date(2026, 10, 9)),
    "one month": (date(2026, 3, 1), date(2026, 3, 31)),
    "one week with no expiry": (date(2026, 9, 28), date(2026, 10, 2)),
    "two days ending on an expiry": (date(2026, 9, 17), date(2026, 9, 18)),
    "first week of data": (date(2025, 10, 10), date(2025, 10, 14)),
    "single trading day": (date(2026, 10, 9), date(2026, 10, 9)),
    "weekend, no trading days": (date(2026, 10, 3), date(2026, 10, 4)),
}


@pytest.mark.parametrize("commodity", ["JEERA", "GUARSEED"])
@pytest.mark.parametrize("label", DATE_RANGES)
def test_app_draws_for_date_range(commodity: str, label: str):
    app = streamlit_testing.AppTest.from_file(APP_PATH, default_timeout=120)
    app.query_params["commodity"] = commodity
    app.run()
    app.date_input[0].set_value(DATE_RANGES[label]).run()
    assert not app.exception, [error.value for error in app.exception]
    assert len(app.tabs) == 7
