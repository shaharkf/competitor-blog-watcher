#!/usr/bin/env python3
"""
Competitor blog watcher.

Polls a list of competitor blogs (RSS/Atom preferred, HTML fallback),
remembers which articles it has already seen (state.json), and posts
a Slack message for anything new.

Usage:
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... python watcher.py

State is stored in state.json next to this script. In GitHub Actions the
workflow commits the updated state back to the repo after each run.
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"
FEEDS_FILE = HERE / "feeds.yaml"

UA = {"User-Agent": "Mozilla/5.0 (compatible; BlogWatcher/1.0)"}
TIMEOUT = 20
MAX_ALERTS_PER_SOURCE = 5  # safety valve so a feed glitch doesn't flood Slack


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": {}, "first_run_done": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def entry_id(link: str, title: str) -> str:
    return hashlib.sha256(f"{link}|{title}".encode()).hexdigest()[:16]


# ---------- fetchers ----------

def fetch_rss(url: str) -> list[dict]:
    """Fetch an RSS/Atom feed and return normalized entries."""
    resp = requests.get(url, headers=UA, timeout=TIMEOUT)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    entries = []
    for e in parsed.entries[:25]:
        link = e.get("link", "")
        title = (e.get("title") or "").strip()
        if not link or not title:
            continue
        published = ""
        for key in ("published_parsed", "updated_parsed"):
            if e.get(key):
                published = time.strftime("%Y-%m-%d", e[key])
                break
        summary = BeautifulSoup(e.get("summary", ""), "html.parser").get_text()
        entries.append({
            "link": link,
            "title": title,
            "published": published,
            "summary": summary.strip()[:300],
        })
    return entries


def fetch_html(url: str, link_selector: str | None) -> list[dict]:
    """
    Fallback for blogs without a feed: scrape the index page and treat each
    matched link as an article. Default heuristic grabs links that look like
    blog posts; override per-source with a CSS selector in feeds.yaml.
    """
    resp = requests.get(url, headers=UA, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    if link_selector:
        anchors = soup.select(link_selector)
    else:
        anchors = [
            a for a in soup.find_all("a", href=True)
            if re.search(r"/(blog|post|article|research|news)s?/[^/]+", a["href"])
        ]

    seen_links, entries = set(), []
    for a in anchors:
        link = urljoin(url, a["href"]).split("#")[0].rstrip("/")
        title = a.get_text(strip=True)
        if not title or len(title) < 8 or link in seen_links:
            continue
        seen_links.add(link)
        entries.append({"link": link, "title": title, "published": "", "summary": ""})
        if len(entries) >= 25:
            break
    return entries


def discover_feed(url: str) -> str | None:
    """Try to auto-discover an RSS/Atom feed from a blog index page."""
    try:
        resp = requests.get(url, headers=UA, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        link = soup.find("link", rel="alternate",
                         type=re.compile(r"application/(rss|atom)\+xml"))
        if link and link.get("href"):
            return urljoin(url, link["href"])
    except requests.RequestException:
        pass
    return None


# ---------- slack ----------

def post_to_slack(webhook: str, source_name: str, entry: dict) -> None:
    date_part = f" · {entry['published']}" if entry["published"] else ""
    text = f":rotating_light: *{source_name}* published a new article{date_part}"
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{text}\n*<{entry['link']}|{entry['title']}>*",
            },
        }
    ]
    if entry["summary"]:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": entry["summary"]}],
        })
    resp = requests.post(webhook, json={"text": f"{source_name}: {entry['title']}",
                                        "blocks": blocks}, timeout=TIMEOUT)
    resp.raise_for_status()


# ---------- main ----------

def main() -> int:
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        print("ERROR: SLACK_WEBHOOK_URL env var is not set.", file=sys.stderr)
        return 1

    sources = yaml.safe_load(FEEDS_FILE.read_text())["sources"]
    state = load_state()
    new_count = 0

    for src in sources:
        name = src["name"]
        print(f"[{name}] checking...")
        try:
            if src.get("rss"):
                entries = fetch_rss(src["rss"])
            else:
                feed = discover_feed(src["url"])
                if feed:
                    print(f"[{name}] auto-discovered feed: {feed}")
                    entries = fetch_rss(feed)
                else:
                    entries = fetch_html(src["url"], src.get("link_selector"))
        except Exception as exc:  # noqa: BLE001 — keep one bad source from killing the run
            print(f"[{name}] FAILED: {exc}", file=sys.stderr)
            continue

        seen = set(state["seen"].get(name, []))
        fresh = [e for e in entries if entry_id(e["link"], e["title"]) not in seen]

        # First run for a source: record everything silently, don't spam Slack
        # with the entire back-catalog.
        if not state["first_run_done"].get(name):
            print(f"[{name}] first run — indexing {len(entries)} existing articles silently")
            state["first_run_done"][name] = True
        else:
            for entry in fresh[:MAX_ALERTS_PER_SOURCE]:
                print(f"[{name}] NEW: {entry['title']}")
                post_to_slack(webhook, name, entry)
                new_count += 1

        seen.update(entry_id(e["link"], e["title"]) for e in entries)
        # keep state bounded
        state["seen"][name] = list(seen)[-500:]

    state["last_run"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"Done. {new_count} new article(s) alerted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
