"""Network calls for news: the GDELT DOC API and NSE's corporate announcements.

Kept apart from `swingbot.news` (pure parsing, tested offline) so the fetch
scripts and the daily refresh share one copy. Nothing here runs in the cloud
workspace: its network blocks both hosts, so these run on your own machine.

NSE: its terms of use (https://www.nseindia.com/static/nse-terms-of-use)
prohibit "systematic or automated data collection" (clause 9) and storing site
content for "virtual trading or simulation activities" (clause 8). `NSESession`
breaks those terms. It exists for one person's private research; what it
collects stays under the git-ignored data/raw/ and must not be shared.
"""

from __future__ import annotations

import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_UA = "swingbot-research/0.1 (personal backtest)"
GDELT_MAX = 250  # articles per request; a full answer means some were cut

NSE_HOME = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
NSE_API = "https://www.nseindia.com/api/corporate-announcements"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NSE_HOME,
}


def gdelt_articles(query: str, start: date, end: date, retries: int = 3) -> list[dict]:
    """GDELT DOC 2.0 `artlist` for whole days `start`..`end` (UTC), newest first."""
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(GDELT_MAX),
        "sort": "datedesc",
        "startdatetime": f"{start:%Y%m%d}000000",
        "enddatetime": f"{end:%Y%m%d}235959",
    }
    url = f"{GDELT_API}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": GDELT_UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", "replace").strip()
            if not text.startswith("{"):
                # GDELT answers bad queries and rate limits with a plain-text message
                raise ValueError(text[:200])
            return json.loads(text).get("articles", [])
        except (urllib.error.URLError, ValueError, json.JSONDecodeError) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry after: {e}", file=sys.stderr)
            time.sleep(10 * (attempt + 1))
    return []


class NSESession:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.warm()

    def warm(self) -> None:
        """The API only answers with the cookies the HTML page sets."""
        self.opener.open(urllib.request.Request(NSE_HOME, headers=NSE_HEADERS), timeout=30).read()

    def announcements(self, symbol: str, start: date, end: date) -> list[dict]:
        params = {
            "index": "equities",
            "symbol": symbol,
            "from_date": f"{start:%d-%m-%Y}",
            "to_date": f"{end:%d-%m-%Y}",
        }
        req = urllib.request.Request(f"{NSE_API}?{urllib.parse.urlencode(params)}", headers=NSE_HEADERS)
        for attempt in range(2):
            try:
                with self.opener.open(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                return data if isinstance(data, list) else data.get("data", [])
            except (urllib.error.HTTPError, json.JSONDecodeError) as e:
                if attempt:
                    raise
                print(f"  {symbol}: {e}; refreshing cookies", file=sys.stderr)
                time.sleep(10)
                self.warm()
        return []
