#!/usr/bin/env python3
"""Collect source links for the daily US market and Bitcoin report.

The script intentionally keeps extraction simple and auditable: it gathers
titles, canonical links, timestamps, and source names from public RSS feeds.
Codex can then summarize from this source bundle instead of inventing links.
"""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


USER_AGENT = "daily-market-discord-report/1.0"

EQUITY_KEYWORDS = (
    "stock",
    "stocks",
    "market",
    "markets",
    "nasdaq",
    "dow",
    "s&p",
    "sp 500",
    "fed",
    "federal reserve",
    "rate",
    "rates",
    "yield",
    "treasury",
    "inflation",
    "jobs",
    "earnings",
    "nvidia",
    "apple",
    "microsoft",
    "tesla",
    "semiconductor",
    "ai",
)

BITCOIN_KEYWORDS = (
    "bitcoin",
    "btc",
    "crypto",
    "cryptocurrency",
    "ether",
    "ethereum",
    "etf",
    "coinbase",
    "sec",
    "stablecoin",
)

FEEDS = [
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
        "category": "equity",
    },
    {
        "name": "CNBC Markets",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "category": "equity",
    },
    {
        "name": "CNBC Economy",
        "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html",
        "category": "equity",
    },
    {
        "name": "CNN Business",
        "url": "http://rss.cnn.com/rss/money_latest.rss",
        "category": "equity",
    },
    {
        "name": "AP Business",
        "url": "https://feeds.apnews.com/apf-business",
        "category": "equity",
    },
    {
        "name": "Investing.com Stock Market News",
        "url": "https://www.investing.com/rss/news_25.rss",
        "category": "equity",
    },
    {
        "name": "Seeking Alpha Market News",
        "url": "https://seekingalpha.com/market_currents.xml",
        "category": "equity",
    },
    {
        "name": "ZDNet",
        "url": "https://www.zdnet.com/news/rss.xml",
        "category": "equity",
    },
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "category": "bitcoin",
    },
    {
        "name": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "category": "bitcoin",
    },
    {
        "name": "Bitcoin Magazine",
        "url": "https://bitcoinmagazine.com/.rss/full/",
        "category": "bitcoin",
    },
]

REFERENCE_PAGES = [
    {
        "name": "Finviz Market Map",
        "url": "https://finviz.com/map.ashx?t=sec",
        "note": "Use as a visual cross-check for S&P 500 sector breadth and heatmap.",
    },
    {
        "name": "Finviz",
        "url": "https://finviz.com/",
        "note": "Use as a cross-check for Nasdaq, Dow, and major market movement.",
    },
    {
        "name": "CNBC US Markets",
        "url": "https://www.cnbc.com/us-markets/",
        "note": "Use as a cross-check for index levels and top movers.",
    },
    {
        "name": "CNN Fear & Greed",
        "url": "https://edition.cnn.com/markets/fear-and-greed",
        "note": "Use as a cross-check for market sentiment.",
    },
    {
        "name": "Hankyung Global Market",
        "url": "https://www.hankyung.com/globalmarket",
        "note": "Use as a Korean-language cross-check when reachable.",
    },
]


@dataclass(frozen=True)
class SourceItem:
    source: str
    category: str
    title: str
    url: str
    published_at: str | None
    score: int


def fetch_text(url: str, timeout: int) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def child_text(element: ET.Element, names: Iterable[str]) -> str | None:
    for name in names:
        child = element.find(name)
        if child is not None and child.text:
            return html.unescape(child.text.strip())
    return None


def item_link(element: ET.Element) -> str | None:
    direct = child_text(element, ("link", "{http://www.w3.org/2005/Atom}link"))
    if direct:
        return direct.strip()
    for child in element:
        if child.tag.endswith("link") and child.attrib.get("href"):
            return child.attrib["href"].strip()
    return None


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [
        (key, value)
        for key, value in query
        if not key.lower().startswith(("utm_", "guccounter"))
    ]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(filtered)))


def score_title(title: str, category: str) -> int:
    lowered = title.lower()
    keywords = BITCOIN_KEYWORDS if category == "bitcoin" else EQUITY_KEYWORDS
    score = sum(2 for keyword in keywords if keyword in lowered)
    if any(keyword in lowered for keyword in ("breaking", "urgent", "fed", "bitcoin", "nasdaq")):
        score += 2
    return score


def parse_feed(feed: dict[str, str], cutoff: datetime, timeout: int) -> list[SourceItem]:
    xml_text = fetch_text(feed["url"], timeout)
    root = ET.fromstring(xml_text)
    raw_items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    items: list[SourceItem] = []

    for raw in raw_items:
        title = child_text(raw, ("title", "{http://www.w3.org/2005/Atom}title"))
        link = item_link(raw)
        published = child_text(
            raw,
            (
                "pubDate",
                "published",
                "updated",
                "{http://www.w3.org/2005/Atom}published",
                "{http://www.w3.org/2005/Atom}updated",
            ),
        )
        published_dt = parse_datetime(published)
        if not title or not link:
            continue
        if published_dt and published_dt < cutoff:
            continue

        score = score_title(title, feed["category"])
        if score == 0:
            continue

        items.append(
            SourceItem(
                source=feed["name"],
                category=feed["category"],
                title=title,
                url=normalize_url(link),
                published_at=published_dt.isoformat() if published_dt else None,
                score=score,
            )
        )

    return items


def dedupe(items: Iterable[SourceItem]) -> list[SourceItem]:
    seen: set[str] = set()
    result: list[SourceItem] = []
    for item in sorted(items, key=lambda row: (row.score, row.published_at or ""), reverse=True):
        key = item.url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def collect(hours: int, timeout: int) -> dict[str, object]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items: list[SourceItem] = []
    errors: list[dict[str, str]] = []

    for feed in FEEDS:
        try:
            items.extend(parse_feed(feed, cutoff, timeout))
        except (ET.ParseError, TimeoutError, urllib.error.URLError, OSError) as exc:
            errors.append({"source": feed["name"], "url": feed["url"], "error": str(exc)})

    deduped = dedupe(items)
    equities = [item for item in deduped if item.category == "equity"]
    bitcoin = [item for item in deduped if item.category == "bitcoin"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": hours,
        "equity_sources": [asdict(item) for item in equities],
        "bitcoin_sources": [asdict(item) for item in bitcoin],
        "reference_pages": REFERENCE_PAGES,
        "errors": errors,
    }


def render_markdown(payload: dict[str, object], equity_limit: int, bitcoin_limit: int) -> str:
    lines = [
        "# Daily Market Source Bundle",
        f"- Generated UTC: {payload['generated_at']}",
        f"- Lookback hours: {payload['lookback_hours']}",
        "",
        "## Equity Sources",
    ]
    for index, item in enumerate(payload["equity_sources"][:equity_limit], start=1):
        lines.extend(
            [
                f"**[{index}] {item['title']}**",
                f"• Source: {item['source']}",
                f"• Published UTC: {item['published_at'] or 'unknown'}",
                f"• URL: {item['url']}",
                "",
            ]
        )

    lines.append("## Bitcoin Sources")
    for index, item in enumerate(payload["bitcoin_sources"][:bitcoin_limit], start=1):
        lines.extend(
            [
                f"**[{index}] {item['title']}**",
                f"• Source: {item['source']}",
                f"• Published UTC: {item['published_at'] or 'unknown'}",
                f"• URL: {item['url']}",
                "",
            ]
        )

    lines.append("## Reference Pages")
    for page in payload["reference_pages"]:
        lines.extend([f"• {page['name']}: {page['url']}", f"  {page['note']}"])

    if payload["errors"]:
        lines.extend(["", "## Fetch Errors"])
        for error in payload["errors"]:
            lines.append(f"• {error['source']}: {error['error']}")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect exact source links for the market report.")
    parser.add_argument("--hours", type=int, default=36, help="Lookback window in hours.")
    parser.add_argument("--timeout", type=int, default=12, help="Per-feed network timeout in seconds.")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--equity-limit", type=int, default=30)
    parser.add_argument("--bitcoin-limit", type=int, default=20)
    args = parser.parse_args()

    payload = collect(hours=args.hours, timeout=args.timeout)
    if args.format == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(payload, args.equity_limit, args.bitcoin_limit))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
