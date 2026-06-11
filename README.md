# Competitor Blog Watcher → Slack

Polls competitor blogs every 30 minutes via GitHub Actions and posts a Slack
alert whenever a new article appears.

How it works: for each source in `feeds.yaml` the script tries, in order,
(1) an explicit RSS feed if you provided one, (2) auto-discovery of an
RSS/Atom feed from the blog page, (3) HTML scraping of the blog index as a
fallback. New articles are detected by diffing against `state.json`, which is
committed back to the repo after each run. The first run for a source indexes
the existing back-catalog silently so Slack isn't flooded.

## Setup (≈5 minutes)

1. **Create a Slack incoming webhook** (you do this yourself in Slack):
   open https://api.slack.com/apps → Create New App → enable *Incoming
   Webhooks* → add a webhook to your alerts channel → copy the URL.

2. **Create a GitHub repo** and push these files to it.

3. **Add the webhook as a secret**: repo → Settings → Secrets and variables →
   Actions → New repository secret, name it `SLACK_WEBHOOK_URL`.

4. **Edit `feeds.yaml`** with your real competitor list. For each blog,
   adding an explicit `rss:` URL is the most reliable option (check
   `/feed`, `/rss.xml`, or `/blog/rss` on their site).

5. **Trigger a first run** from the Actions tab (workflow_dispatch). This
   run indexes existing articles silently; every run after that alerts on
   anything new.

## Tuning

- Polling frequency: edit the cron in `.github/workflows/watch.yml`
  (GitHub schedules can drift a few minutes; that's normal).
- Flood protection: `MAX_ALERTS_PER_SOURCE` in `watcher.py` caps alerts per
  source per run.
- Feed-less blogs: set `link_selector` in `feeds.yaml` with a CSS selector
  targeting the article links on the blog index page.

## Run locally

```bash
pip install -r requirements.txt
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/... python watcher.py
```
