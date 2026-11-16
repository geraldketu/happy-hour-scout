"""Find and parse happy hour info on a restaurant's own website.

Strategy: fetch the homepage, follow up to a few same-site links that look like
happy hour / specials / menu / drinks pages, then pull out every text block that
mentions "happy hour" and parse days, times and deal lines from it. The raw
snippet and URL are always stored so each result can be checked by a human.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Mozilla/5.0 (compatible; hhscout/0.1; personal happy-hour research)"
LINK_HINTS = re.compile(r"happy|special|drink|bar|menu|deal|promo", re.I)
HH_RE = re.compile(r"happy\s*hour|happy-hour|social\s*hour", re.I)
MAX_SNIPPET = 600

DAY_NAMES = {
    "mon": "Mon", "monday": "Mon", "tue": "Tue", "tues": "Tue", "tuesday": "Tue",
    "wed": "Wed", "wednesday": "Wed", "thu": "Thu", "thur": "Thu", "thurs": "Thu", "thursday": "Thu",
    "fri": "Fri", "friday": "Fri", "sat": "Sat", "saturday": "Sat", "sun": "Sun", "sunday": "Sun",
}
_DAY = r"(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
DAY_RANGE_RE = re.compile(rf"\b{_DAY}\.?\s*(?:-|–|—|to|thru|through)\s*{_DAY}\b\.?", re.I)
DAY_WORD_RE = re.compile(r"\b(daily|every\s*day|seven days|7 days|weekdays|weekends)\b", re.I)
SINGLE_DAY_RE = re.compile(rf"\b{_DAY}s?\b", re.I)

_T = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
TIME_RANGE_RE = re.compile(
    rf"\b{_T}\s*(?:-|–|—|to|until|till|til)\s*(?:{_T}|(close|closing))", re.I
)
DEAL_RE = re.compile(r"\$\s?\d|half[\s-]?(?:off|price)|\d+\s?%\s?off|\b\d+\s?off\b|bogo|buy one", re.I)


# --------------------------------------------------------------------------- parsing


def _hhmm(hour: str, minute: str | None, ampm: str | None, default_pm: bool) -> str | None:
    h = int(hour)
    if h > 24 or (minute and int(minute) > 59):
        return None
    pm = ampm.lower().startswith("p") if ampm else default_pm
    if pm and h < 12:
        h += 12
    if ampm and ampm.lower().startswith("a") and h == 12:
        h = 0
    return f"{h:02d}:{minute or '00'}"


def parse_times(text: str) -> tuple[str | None, str | None]:
    """Return (start, end) as HH:MM ('close' allowed for end). Bare numbers are assumed PM."""
    for m in TIME_RANGE_RE.finditer(text):
        h1, m1, ap1, h2, m2, ap2, close = m.groups()
        if not ap1 and not ap2 and not close and int(h1) > 12:
            continue
        # "4-7pm": the start inherits the end's am/pm
        ap_start = ap1 or ap2
        start = _hhmm(h1, m1, ap_start, default_pm=True)
        end = "close" if close else _hhmm(h2, m2, ap2 or ap1, default_pm=True)
        if start and end:
            return start, end
    return None, None


def parse_days(text: str) -> str | None:
    m = DAY_RANGE_RE.search(text)
    if m:
        return f"{DAY_NAMES[m.group(1).lower().rstrip('.')]}-{DAY_NAMES[m.group(2).lower().rstrip('.')]}"
    m = DAY_WORD_RE.search(text)
    if m:
        word = m.group(1).lower()
        return {"weekdays": "Mon-Fri", "weekends": "Sat-Sun"}.get(word, "Daily")
    days = []
    for m in SINGLE_DAY_RE.finditer(text):
        d = DAY_NAMES.get(m.group(1).lower())
        if d and d not in days:
            days.append(d)
    return ", ".join(days) if days else None


PRICE_ONLY_RE = re.compile(r"^\s*\$\s?\d+(?:\.\d{2})?\s*$")


def parse_deals(text: str) -> str | None:
    # Sites often split "$3" and "Domestic Bottles" across lines: join a bare price to its neighbour
    # (the item before it on menus like "Papadum Board / $8", otherwise the item after it).
    lines = [ln.strip() for ln in re.split(r"\n", text)]
    merged: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if PRICE_ONLY_RE.match(ln):
            prev = merged[-1] if merged else ""
            prev_is_label = (not prev or prev.endswith(":") or DEAL_RE.search(prev)
                             or re.search(r"\d\s*(?:a\.?m|p\.?m)\b|^(?:to|until)$", prev, re.I) or HH_RE.search(prev))
            if not prev_is_label:
                merged[-1] = f"{merged[-1]} {ln}"
            elif i + 1 < len(lines):
                merged.append(f"{ln} {lines[i + 1]}")
                i += 1
            else:
                merged.append(ln)
        else:
            merged.append(ln)
        i += 1
    parts = re.split(r"[\n•|·;]+|(?<=\.)\s+", "\n".join(merged))
    deals = [p.strip(" -–*") for p in parts if DEAL_RE.search(p) and len(p.strip()) < 160]
    return "\n".join(dict.fromkeys(deals)) or None


OTHER_MEAL_RE = re.compile(r"\b(lunch|brunch|breakfast|prix\s*fixe|dinner|kitchen hours|hours of operation)\b", re.I)


def _near_mention(text: str, before: int = 80, after: int = 250) -> tuple[str, str]:
    """(text before, text after) the first happy hour mention.

    The details usually follow the mention; text before it is often the regular
    opening hours or another menu, so it's only a fallback. Both sides are cut at
    other meal labels so "LUNCH SPECIAL & HAPPY HOUR / LUNCH (MON-FRI 11-3)"
    doesn't borrow the lunch times.
    """
    m = HH_RE.search(text)
    if not m:
        return "", text
    pre = text[max(0, m.start() - before): m.start()]
    post = text[m.end(): m.end() + after]
    if OTHER_MEAL_RE.search(pre):
        pre = ""
    later = OTHER_MEAL_RE.search(post)
    if later:
        post = post[: later.start()]
    return pre, post


def parse_snippet(snippet: str, url: str) -> dict:
    pre, post = _near_mention(snippet)
    days = parse_days(post) or parse_days(pre)
    start, end = parse_times(post)
    if start is None:
        start, end = parse_times(pre)
    deals = parse_deals(snippet)
    score = sum(x is not None for x in (days, start, deals))
    return {
        "days": days,
        "start_time": start,
        "end_time": end,
        "deals": deals,
        "snippet": snippet,
        "source_url": url,
        "confidence": {3: "high", 2: "medium"}.get(score, "low"),
    }


def happy_hour_snippets(html: str) -> list[str]:
    """Text blocks around every mention of happy hour, de-duplicated."""
    soup = BeautifulSoup(html, "html.parser")
    # Headers/footers are kept on purpose: restaurants often list hours there.
    for tag in soup(["script", "style", "noscript", "svg", "template", "nav"]):
        tag.decompose()
    snippets = []
    for node in soup.find_all(string=HH_RE):
        block = node.parent
        # climb until the block has some substance (days/times/deals usually sit in a parent section)
        while (block.parent is not None and block.parent.name not in ("body", "html", "[document]")
               and len(block.get_text(" ", strip=True)) < 80):
            block = block.parent
        text = block.get_text("\n", strip=True)
        # a bare heading ("HAPPY HOUR!"): the details are usually in the following blocks
        sib = block
        while len(text) < 200 and (sib := sib.find_next_sibling()) is not None:
            sib_text = sib.get_text("\n", strip=True)
            if HH_RE.search(sib_text):  # its own mention gets its own snippet
                break
            text += "\n" + sib_text
        text = re.sub(r"\n{2,}", "\n", text)[:MAX_SNIPPET]
        if text and not any(text in s or s in text for s in snippets):
            snippets.append(text)
    return snippets


# --------------------------------------------------------------------------- fetching


class SiteFetcher:
    def __init__(self, delay: float = 1.0, max_pages: int = 4):
        self.delay = delay
        self.max_pages = max_pages
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._robots: dict[str, RobotFileParser | None] = {}
        self.last_error: str | None = None

    def _allowed(self, url: str) -> bool:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if origin not in self._robots:
            rp = RobotFileParser(origin + "/robots.txt")
            try:
                resp = self.session.get(origin + "/robots.txt", timeout=10)
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            self._robots[origin] = rp
        return self._robots[origin].can_fetch(USER_AGENT, url)

    def get(self, url: str) -> str | None:
        if not self._allowed(url):
            self.last_error = "blocked by robots.txt"
            return None
        try:
            resp = self.session.get(url, timeout=20)
        except requests.RequestException as exc:
            self.last_error = f"connection failed: {type(exc).__name__}"
            return None
        finally:
            time.sleep(self.delay)
        if resp.status_code != 200:
            self.last_error = f"HTTP {resp.status_code}"
            return None
        if "html" not in resp.headers.get("content-type", ""):
            self.last_error = f"not HTML ({resp.headers.get('content-type')})"
            return None
        if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":  # requests' default when undeclared
            resp.encoding = resp.apparent_encoding
        return resp.text

    def candidate_pages(self, home_url: str, html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        host = urlparse(home_url).netloc.removeprefix("www.")
        scored = []
        for a in soup.find_all("a", href=True):
            url = urljoin(home_url, a["href"]).split("#")[0]
            if urlparse(url).netloc.removeprefix("www.") != host or url.rstrip("/") == home_url.rstrip("/"):
                continue
            if url.lower().endswith((".pdf", ".jpg", ".png")):
                continue
            label = f"{a.get_text(' ', strip=True)} {urlparse(url).path}"  # not the host: "bar.com" isn't a hint
            if LINK_HINTS.search(label):
                score = 2 if HH_RE.search(label) or "special" in label.lower() else 1
                scored.append((score, url))
        seen, ordered = set(), []
        for _, url in sorted(scored, key=lambda x: -x[0]):
            key = url.split("?")[0].rstrip("/").lower()
            if key not in seen:
                seen.add(key)
                ordered.append(url)
        return ordered[: self.max_pages - 1]

    def scrape(self, website: str) -> tuple[str, list[dict]]:
        """Return (status, happy_hour_results) for a restaurant website."""
        self.last_error = None
        home = self.get(website)
        if home is None:
            return "error", []
        results = []
        pages = [(website, home)] + [(u, self.get(u)) for u in self.candidate_pages(website, home)]
        for url, html in pages:
            if html:
                results.extend(parse_snippet(s, url) for s in happy_hour_snippets(html))
        # best first: confidence, then results that found actual times
        order = {"high": 0, "medium": 1, "low": 2}
        results.sort(key=lambda r: (order[r["confidence"]], r["start_time"] is None))
        return ("found" if results else "not_found"), results[:3]
