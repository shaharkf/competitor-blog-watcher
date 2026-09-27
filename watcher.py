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

from __future__ import annotations  # keeps `str | None` importable on py3.9 tooling

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


# ---------- relevance + noise filtering ----------
#
# Two different problems produced the noise in #competitor-blogpost-alert:
#   1. Go-to-market posts ("Named a Leader in...", "raises $5M", booth invites).
#   2. Big-corp blogs whose security feed is mostly not about AI at all
#      (Cisco IPsec series, Wi-Fi 7, rail networks, Splunk/Nexus).
# So we gate on *relevance to our lane* first, then strip marketing, and let
# tier-1 direct competitors through more freely — for them a product launch is
# exactly the thing we want to hear about.

# Our lane. Checked against title + summary only (NOT the link: too many vendor
# domains literally end in .ai, which would make the gate always true).
RELEVANCE_PATTERNS = [
    r"\b(ai|llms?|genai|gen-ai)\b",
    r"\bagent(?:ic|s)?\b",
    r"\bmcp\b|model context protocol",
    r"\b(prompt|jailbreak|guardrail|injection)\b",
    r"\b(claude|cursor|copilot|codex|chatgpt|gemini|openai|anthropic|windsurf|cowork)\b",
    r"\b(skills?|plugins?|connectors?|tool[- ]calls?)\b",
    r"\b(rag|embedding|inference|fine[- ]tun\w*|autonomous|shadow ai)\b",
]

# Pure go-to-market. Dropped for every tier, including tier 1.
HARD_MARKETING_PATTERNS = [
    r"\b(raise[sd]?|raising|funding|seed round|series [a-f]\b|valuation|oversubscribed)\b",
    r"\b(acqui(?:re|res|red|sition)s?|merger)\b",
    r"\b(named|recognized|recognised|honou?red|awarded|award|winner|finalist)\b",
    r"\bleader in\b|\btop \d+\b|\bbest \d+\b",
    r"\b(gartner|forrester|magic quadrant|hype cycle|market (?:guide|overview|report))\b",
    r"\b(silver|gold|platinum|bronze)[- ]?(?:tier|partner|status)\b",
    r"\b(webinar|conference|summit|booth|meet us|roadshow|podcast|newsletter|fireside)\b",
    r"\b(rsac?|black ?hat|def ?con|re:invent)\b.{0,20}\b(recap|preview|wrap|join|visit)\b",
    r"\b(hiring|careers?|culture|life at|our team|press release|year in review)\b",
    r"\b(case study|customer story|success story|testimonial)\b",
    # founder/company-origin posts — brand content, not intel
    r"\bwhy we (?:started|built|founded|are building)\b",
    r"\bour (?:journey|story|mission|vision|values)\b|\bfounder'?s? story\b",
    r"\bmeet the team\b|\bfrom the (?:ceo|founders?)\b",
    # "Neo Launches with $100M ..." — money figure in a headline is always GTM
    r"\$\s?\d+(?:\.\d+)?\s*(?:k|m|b|million|billion)\b",
    # exec hires
    r"\b(appoints?|names? .{0,24}\b(?:ceo|cto|ciso|cro|cmo|cfo|vp)\b|welcomes?|joins? as)\b",
    # sales collateral masquerading as posts
    r"\b(data ?sheet|white ?paper|solution brief|product brief|e-?book|buyers? guide)\b",
]

# Ecosystem / product news. Signal from a direct competitor, noise from anyone
# else — we don't need every AI-SPM vendor's feature announcement.
ECOSYSTEM_LAUNCH_PATTERNS = [
    r"\b(announc(?:e|es|ed|ing)|launch(?:es|ed|ing)?|introduc(?:es|ing)|unveil(?:s|ed)?)\b",
    r"\b(now available|general availability|now ga\b|early access|waitlist)\b",
    r"\b(partner(?:s|ship|ships)?|marketplace|joins|integrat(?:es|ion) with|available in)\b",
    r"\b(product (?:update|spotlight)|release notes|changelog|what's new)\b",
]

# Real work. Gets through on every tier (but never beats HARD_MARKETING).
RESEARCH_SIGNAL_PATTERNS = [
    r"\bcve-\d{4}-\d+\b",
    r"\b(vulnerab(?:ility|ilities)|exploit(?:ed|ing|ation)?|zero[- ]?day|0day|poc)\b",
    r"\b(attack|bypass(?:ing|ed)?|escalation|exfiltrat\w*|hijack\w*|poison\w*|tamper\w*)\b",
    r"\b(supply[- ]chain|typosquat\w*|backdoor|persistence|sandbox escape)\b",
    r"\b(new research|research|disclosure|advisory|teardown|reverse[- ]engineer\w*)\b",
    r"\bhow (?:to|we|i)\b|\b(deep[- ]?dive|walkthrough|benchmark|analysis|anatomy of)\b",
    r"\b(owasp|mitre|atlas|nist)\b",
]

# Trailing junk that HTML card-scraping drags into the title.
_TITLE_JUNK = re.compile(
    r"(read more\s*[→>»]*|\d+\s*min(?:ute)?s?\s*read|share this|learn more)\s*$",
    re.IGNORECASE,
)
# "<title> 6 min | August 25, 2026 Omer Singer" -> "<title>"
_TITLE_CARD_META = re.compile(r"\s*\d+\s*min(?:ute)?s?\b.*$", re.IGNORECASE)
# taxonomy pages are not articles
_NON_ARTICLE_PATH = re.compile(r"/(author|authors|category|categories|tag|tags|page)/", re.I)
_JUNK_ANCHOR = re.compile(r"^(read more|learn more|continue reading|view post)\b", re.I)


def clean_title(raw: str) -> str:
    """
    Normalize a title. HTML-scraped sources hand us the whole card as anchor
    text ("Product SpotlightsAcme Named a...September 15, 20265 minRead more"),
    which is unreadable in Slack and defeats title-based matching.
    """
    title = re.sub(r"\s+", " ", (raw or "")).strip()
    title = _TITLE_CARD_META.sub("", title).strip(" -–—|·,")
    for _ in range(3):
        new = _TITLE_JUNK.sub("", title).strip(" -–—|·,")
        if new == title:
            break
        title = new
    if len(title) > 180:
        cut = title[:180]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        title = cut + "…"
    return title


def _matches(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def should_alert(entry: dict, tier: int) -> tuple[bool, str]:
    """
    Decide whether an entry is worth a Slack ping.
    Returns (keep, reason) — reason is logged so tuning is auditable.
    """
    title = (entry.get("title") or "").strip()
    summary = (entry.get("summary") or "").strip()
    title_l = title.lower()
    body_l = f"{title} {summary}".lower()

    if len(title) < 12:
        return False, "title-too-short"
    if _matches(HARD_MARKETING_PATTERNS, title_l):
        return False, "marketing"

    is_research = _matches(RESEARCH_SIGNAL_PATTERNS, body_l)
    is_launch = _matches(ECOSYSTEM_LAUNCH_PATTERNS, title_l)

    if tier == 1:
        # Direct competitor: in our lane by definition. Launches are signal.
        return True, "tier1-research" if is_research else "tier1"

    if not _matches(RELEVANCE_PATTERNS, body_l):
        return False, "off-topic"
    if is_research:
        return True, "research"
    if is_launch:
        return False, "launch-non-tier1"
    if tier >= 3:
        return False, "tier3-no-research"
    return True, "relevant"


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
        title = clean_title(e.get("title") or "")
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

    # A card usually exposes several anchors for the same post (image, title,
    # "Read More", author). Group by URL and keep the most title-looking text.
    best: dict[str, str] = {}
    order: list[str] = []
    for a in anchors:
        href = a.get("href") or ""
        if _NON_ARTICLE_PATH.search(href):
            continue
        link = urljoin(url, href).split("#")[0].rstrip("/")
        if link == url.rstrip("/"):
            continue
        text = clean_title(a.get_text(" ", strip=True))
        if _JUNK_ANCHOR.match(text):
            text = ""
        if link not in best:
            order.append(link)
            best[link] = text
        elif len(text) > len(best[link]):
            best[link] = text

    entries = []
    for link in order:
        title = best[link] or slug_to_title(link)
        if not title or len(title) < 8:
            continue
        entries.append({"link": link, "title": title, "published": "", "summary": ""})
        if len(entries) >= 25:
            break
    return entries


def slug_to_title(link: str) -> str:
    """Last resort when a card's only anchor text is 'Read More'."""
    slug = link.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"\.(html?|php)$", "", slug)
    words = [w for w in re.split(r"[-_]+", slug) if w]
    if len(words) < 2:
        return ""
    acronyms = {"ai", "llm", "llms", "mcp", "mcps", "cve", "edr", "xdr", "rce",
                "api", "apis", "ciso", "cwe", "sdk", "cli", "pii", "dlp", "a2a",
                "iam", "nhi", "saas", "ssrf", "xss", "poc", "owasp", "nist"}
    return " ".join(
        w.upper() if w.lower() in acronyms else (w if w.isupper() else w.capitalize())
        for w in words
    )


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
        tier = int(src.get("tier", 2))
        print(f"[{name}] checking (tier {tier})...")
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

        kept, dropped = [], {}
        for e in entries:
            keep, reason = should_alert(e, tier)
            if keep:
                kept.append(e)
            else:
                dropped[reason] = dropped.get(reason, 0) + 1
        if dropped:
            detail = ", ".join(f"{n}x {r}" for r, n in sorted(dropped.items()))
            print(f"[{name}] filtered {sum(dropped.values())} ({detail})")

        seen = set(state["seen"].get(name, []))
        fresh = [e for e in kept if entry_id(e["link"], e["title"]) not in seen]

        # First run for a source: record everything silently, don't spam Slack
        # with the entire back-catalog.
        if not state["first_run_done"].get(name):
            print(f"[{name}] first run — indexing {len(kept)} article(s) silently")
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
