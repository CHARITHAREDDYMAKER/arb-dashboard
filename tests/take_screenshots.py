"""Save one full-page screenshot per tab and commodity, and check the layout at laptop width.

Start the app first, then run this from the project root:

    streamlit run app/app.py --server.headless true --server.port 8765
    python tests/take_screenshots.py

Needs Playwright (`pip install playwright`) and uses the Microsoft Edge already
installed on Windows, so no browser download is required. It is a review tool
and not a dependency of the dashboard.
"""

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

URL = "http://localhost:8765"
OUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "phase4_screens"
COMMODITIES = ["JEERA", "GUARSEED"]
WIDTH, HEIGHT = 1366, 768  # a common laptop screen


def wait_until_drawn(page: Page) -> None:
    """Wait for Streamlit to finish running and for the charts to render."""
    page.wait_for_selector('[role="tab"]', timeout=60_000)
    page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=60_000)
    page.wait_for_timeout(1500)


def content_height(page: Page) -> int:
    """Height of the scrolling main area: Streamlit scrolls an inner container, not the page."""
    return page.evaluate("document.querySelector('[data-testid=\"stMain\"]').scrollHeight")


def has_horizontal_scroll(page: Page) -> bool:
    return page.evaluate(
        """() => [document.documentElement, document.querySelector('[data-testid="stMain"]')]
                  .some(element => element.scrollWidth > element.clientWidth + 1)"""
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    problems = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.on("console", lambda message: problems.append(f"console {message.type}: {message.text}") if message.type == "error" else None)

        for commodity in COMMODITIES:
            page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
            page.goto(f"{URL}/?commodity={commodity}")
            wait_until_drawn(page)
            tabs = page.get_by_role("tab")
            for index in range(tabs.count()):
                page.set_viewport_size({"width": WIDTH, "height": HEIGHT})
                tab = tabs.nth(index)
                name = tab.inner_text().strip()
                tab.click()
                wait_until_drawn(page)
                if has_horizontal_scroll(page):
                    problems.append(f"{commodity} / {name}: horizontal scrolling at {WIDTH}px")
                if page.locator('[data-testid="stException"]').count():
                    problems.append(f"{commodity} / {name}: exception shown on the page")

                # Grow the window to the full content height so one screenshot holds the whole tab.
                page.set_viewport_size({"width": WIDTH, "height": content_height(page) + 40})
                page.wait_for_timeout(1200)
                slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
                path = OUT_DIR / f"{commodity.lower()}_{index + 1}_{slug}.png"
                page.screenshot(path=str(path))
                print(f"saved {path.name}")
        browser.close()

    print("\n".join(problems) if problems else f"No problems: no errors and no horizontal scrolling at {WIDTH}px.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
