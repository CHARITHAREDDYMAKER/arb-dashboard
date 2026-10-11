"""Click through every sidebar control in a real browser and check the app responds.

Start the app first, then run this from the project root:

    streamlit run app/app.py --server.headless true --server.port 8765
    python tests/browser_check.py

Each step changes one control and checks that (a) no error appears and (b) the
header changes when it should. Uses Playwright with the installed Microsoft Edge.
"""

import re
import sys

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

URL = "http://localhost:8765"
results: list[tuple[str, bool, str]] = []


def settle(page: Page) -> None:
    """Wait for Streamlit to finish re-running and for the charts to render."""
    page.wait_for_selector('[role="tab"]', timeout=60_000)
    try:  # give a re-run triggered by the last action a moment to start
        page.wait_for_selector('[data-testid="stApp"][data-test-script-state="running"]', timeout=1500)
    except PlaywrightTimeout:
        pass  # nothing re-ran, or it finished before we looked
    page.wait_for_selector('[data-testid="stApp"][data-test-script-state="notRunning"]', timeout=60_000)
    page.wait_for_timeout(1200)


def header_text(page: Page) -> str:
    """The KPI cards and insight sentences: everything the desk inputs should move."""
    cards = " | ".join(page.locator(".kpi").all_inner_texts()[:6])
    insights = " | ".join(page.locator(".insight").all_inner_texts())
    return re.sub(r"\s+", " ", cards + " || " + insights)


def has_error(page: Page) -> bool:
    return page.locator('[data-testid="stException"]').count() > 0


def record(step: str, passed: bool, detail: str = "") -> None:
    results.append((step, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {step}" + (f"  ({detail})" if detail else ""))


def expect_change(page: Page, step: str, before: str) -> str:
    settle(page)
    after = header_text(page)
    record(step, after != before and not has_error(page), "header changed" if after != before else "header did NOT change")
    return after


def set_number(page: Page, label: str, value: str) -> None:
    field = page.get_by_label(label, exact=True)
    field.fill(value)
    field.press("Enter")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.goto(f"{URL}/?commodity=JEERA")
        settle(page)
        defaults = header_text(page)
        record("App loads on jeera without errors", "Jeera" in page.locator(".legend-note b").first.inner_text() and not has_error(page))

        # Commodity switch
        page.get_by_test_id("stRadioGroup").get_by_text("Guar seed").click()
        settle(page)
        record("Switch to guar seed", "Guar seed" in page.locator(".legend-note b").first.inner_text() and not has_error(page))
        record("Cold-storage toggle is hidden for guar seed", page.get_by_text("Jeera in cold storage").count() == 0)
        page.get_by_test_id("stRadioGroup").get_by_text("Jeera", exact=True).click()
        settle(page)
        record("Switch back to jeera", header_text(page) == defaults and not has_error(page))

        # Date range: the charts change, the header must not
        page.get_by_role("tab", name="Cash vs Futures").click()
        settle(page)
        days_before = page.get_by_text(re.compile(r"\d+ trading days")).first.inner_text()
        # The date range is a segmented field: click the first segment and type; focus advances by itself.
        page.get_by_label("day, Date range start date").click()
        page.keyboard.type("01012026", delay=60)
        page.get_by_label("day, Date range end date").click()
        page.keyboard.type("30062026", delay=60)
        page.keyboard.press("Enter")
        page.get_by_role("tab", name="Cash vs Futures").click()  # moves focus away so the range is committed
        settle(page)
        days_after = page.get_by_text(re.compile(r"\d+ trading days")).first.inner_text()
        record("Date range filters the charts", days_after != days_before and not has_error(page), f"{days_before} -> {days_after}")
        record("Date range leaves the header on the latest day", header_text(page) == defaults)

        # Desk inputs: each should move the header
        state = defaults
        set_number(page, "Brokerage (Rs per lakh)", "40")
        state = expect_change(page, "Brokerage 10 -> 40", state)
        set_number(page, "Funding spread over T-bill (% p.a.)", "3")
        state = expect_change(page, "Funding spread 1 -> 3", state)
        set_number(page, "Handling † (Rs per quintal)", "30")
        state = expect_change(page, "Handling 10 -> 30", state)
        page.get_by_test_id("stSidebar").get_by_text("18% GST on storage and assaying").click()
        state = expect_change(page, "GST on storage switched off", state)
        page.get_by_test_id("stSidebar").get_by_text("Jeera in cold storage").click()
        state = expect_change(page, "Cold storage switched on", state)
        # The cold-storage setting must survive a visit to guar seed, where its toggle is hidden.
        page.get_by_test_id("stRadioGroup").get_by_text("Guar seed").click()
        settle(page)
        page.get_by_test_id("stRadioGroup").get_by_text("Jeera", exact=True).click()
        settle(page)
        record("Cold storage stays on after switching commodity and back", header_text(page) == state and not has_error(page))

        set_number(page, "Signal threshold (% of spot)", "3")
        state = expect_change(page, "Signal threshold 0.25 -> 3", state)
        set_number(page, "Roll when near month has fewer than (days)", "12")
        state = expect_change(page, "Roll threshold 5 -> 12", state)

        # Reset
        page.get_by_role("button", name="Reset to defaults").click()
        settle(page)
        record("Reset to defaults restores the header", header_text(page) == defaults and not has_error(page))

        # Signal Finder: date picker, timeline click and ranking switch
        page.get_by_role("tab", name="Signal Finder").click()
        settle(page)
        calculator = page.locator(".calc").first
        before = calculator.inner_text()
        page.get_by_label("Trade date (or click a point on the timeline below)").click()
        page.keyboard.type("27 Apr 2026")  # inside the Jan to Jun range chosen earlier
        page.keyboard.press("Enter")
        settle(page)
        after_pick = calculator.inner_text()
        record("Signal Finder: picking a date refills the calculator", after_pick != before and "JEERAUNJHA20MAY2026" in page.locator("body").inner_text() and not has_error(page))

        # Plotly listens for clicks on a layer above the markers, so click by screen position.
        marker = page.locator(".js-plotly-plot:visible .scatterlayer .trace").nth(2).locator("path.point").nth(3)
        marker.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        box = marker.bounding_box()
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        settle(page)
        record("Signal Finder: clicking a timeline marker refills the calculator", calculator.inner_text() != after_pick and not has_error(page))

        table = page.locator('[data-testid="stDataFrame"]:visible').first
        table.scroll_into_view_if_needed()
        page.wait_for_timeout(800)
        shot_before = table.screenshot()
        page.get_by_text("Most Rs per quintal (any days to expiry)").first.click()
        settle(page)
        record("Signal Finder: switching the ranking changes the table", table.screenshot() != shot_before and not has_error(page))

        # Historical Backtest: strategy, threshold and exit mode each change the results
        page.get_by_role("tab", name="Historical Backtest").click()
        settle(page)

        def backtest_cards() -> str:
            return " | ".join(page.locator(".kpi:visible").all_inner_texts())

        cards = backtest_cards()
        record("Backtest tab shows results", "Trades executed".upper() in cards.upper() and not has_error(page))
        page.get_by_role("radiogroup", name="Strategy").get_by_text("Reverse arb (inventory)").click()
        settle(page)
        reverse_cards = backtest_cards()
        record("Backtest: switching strategy changes the results", reverse_cards != cards and not has_error(page))
        page.get_by_label("Entry threshold (% a year, before funding)").click()
        page.keyboard.type("20%")
        page.keyboard.press("Enter")
        settle(page)
        strict_cards = backtest_cards()
        record("Backtest: raising the threshold changes the results", strict_cards != reverse_cards and not has_error(page))
        page.get_by_role("radiogroup", name="Exit").get_by_text("Close before expiry").click()
        settle(page)
        record("Backtest: changing the exit mode changes the results", backtest_cards() != strict_cards and not has_error(page))
        record("Backtest: trade history can be downloaded", page.get_by_role("button", name="Download trade history (CSV)").count() == 1)

        # Theme toggle (a server-wide setting, so it is always switched back)
        background = "getComputedStyle(document.querySelector('.stApp')).backgroundColor"
        dark = page.evaluate(background)
        page.get_by_test_id("stSidebar").get_by_text("Light theme").click()
        page.wait_for_timeout(1500)
        settle(page)
        light = page.evaluate(background)
        record("Light theme toggle changes the page", light != dark and not has_error(page), f"{dark} -> {light}")
        page.get_by_test_id("stSidebar").get_by_text("Light theme").click()
        page.wait_for_timeout(1500)
        settle(page)
        record("Theme switches back to dark", page.evaluate(background) == dark and not has_error(page))

        # Every tab still draws after all that
        for tab in page.get_by_role("tab").all():
            tab.click()
            settle(page)
            name = tab.inner_text().strip().split("\n")[-1]
            record(f"Tab '{name}' draws", not has_error(page) and page.locator(".js-plotly-plot:visible, [data-testid='stDataFrame']:visible, .calc:visible").count() > 0)
        browser.close()

    failed = [step for step, passed, _ in results if not passed]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
