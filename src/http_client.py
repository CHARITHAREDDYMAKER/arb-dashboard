"""Shared HTTP helpers: a browser-like session and a polite GET with retries."""

import time

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# NCDEX drops connections after a burst of requests, so we wait between calls.
DEFAULT_DELAY_SECONDS = 5.0


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    return session


def polite_get(
    session: requests.Session,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    delay: float = DEFAULT_DELAY_SECONDS,
    retries: int = 3,
    timeout: float = 60.0,
) -> requests.Response:
    """GET `url`, sleeping `delay` seconds first and backing off on failures."""
    last_error: Exception | None = None
    for attempt in range(retries):
        time.sleep(delay * (attempt * 4 + 1))  # 5s, 25s, 45s with the default delay
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
    raise RuntimeError(f"GET {url} failed after {retries} attempts: {last_error}")
