"""
Argus Intelligence Pipeline
Scrapes SEC and ESMA regulatory sources, passes to Claude for analysis,
outputs feed.json that the live site reads.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
import feedparser
import requests
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── SOURCES ────────────────────────────────────────────────────────────
SOURCES = [
    {
        "jurisdiction": "US",
        "name": "SEC Press Releases",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&dateb=&owner=include&count=20&search_text=&action=getcurrent&output=atom",
        "tag": "ENFORCEMENT",
    },
    {
        "jurisdiction": "US",
        "name": "SEC News",
        "url": "https://www.sec.gov/rss/news/press.xml",
        "tag": "ENFORCEMENT",
    },
    {
        "jurisdiction": "US",
        "name": "CFTC Newsroom",
        "url": "https://www.cftc.gov/rss/pressreleases.xml",
        "tag": "MARKET STRUCTURE",
    },
    {
        "jurisdiction": "EU",
        "name": "ESMA News",
        "url": "https://www.esma.europa.eu/rss.xml",
        "tag": "MICA",
    },
    {
        "jurisdiction": "EU",
        "name": "EBA News",
        "url": "https://www.eba.europa.eu/rss.xml",
        "tag": "STABLECOINS",
    },
]

# ── CLAUDE ANALYSIS ────────────────────────────────────────────────────
def analyze_item(title, summary, source_name, jurisdiction):
    """
    Ask Claude whether this item is relevant to crypto/digital assets.
    If yes, return a structured feed item. If no, return None.
    """
    prompt = f"""You are analyzing regulatory news for Argus Intelligence, a platform serving institutional clients (banks, funds) entering crypto.

SOURCE: {source_name} ({jurisdiction})
TITLE: {title}
CONTENT: {summary[:1500]}

Determine if this is relevant to crypto, digital assets, stablecoins, DeFi, blockchain, or virtual assets.

If NOT relevant: respond with exactly: IRRELEVANT

If relevant: respond with valid JSON only, no markdown, no explanation:
{{
  "relevant": true,
  "urgency": "HIGH" or "MEDIUM" or "LOW",
  "tag": one of ["ENFORCEMENT", "MARKET STRUCTURE", "STABLECOINS", "LICENSING", "CUSTODY", "AML/CFT", "MICA", "SANCTIONS"],
  "title": "concise professional title under 100 chars",
  "summary": "2-3 sentence institutional-grade summary of what changed and why it matters",
  "action": "specific recommended action for a bank or fund entering crypto — 1-2 sentences",
  "affects": [] or list of protocol names from: ["Aave", "Uniswap", "Lido", "MakerDAO", "dYdX", "Tornado Cash"]
}}

Urgency guide:
- HIGH: immediate compliance deadline, enforcement action, new binding rule
- MEDIUM: guidance, consultation, proposed rule with future deadline  
- LOW: general update, speech, non-binding guidance"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()

        if text == "IRRELEVANT" or "IRRELEVANT" in text:
            return None

        # Strip any markdown fences if Claude added them
        text = re.sub(r"```json|```", "", text).strip()
        data = json.loads(text)

        if not data.get("relevant"):
            return None

        return data

    except Exception as e:
        print(f"  Claude error: {e}")
        return None


# ── FETCH RSS ──────────────────────────────────────────────────────────
def fetch_source(source):
    """Fetch and parse an RSS feed, return list of (title, summary, link) tuples."""
    print(f"  Fetching {source['name']}...")
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for entry in feed.entries[:8]:  # Check latest 8 items per source
            title = entry.get("title", "")
            summary = entry.get("summary", entry.get("description", ""))
            link = entry.get("link", "")
            # Clean HTML tags from summary
            summary = re.sub(r"<[^>]+>", " ", summary).strip()
            if title:
                items.append((title, summary, link))
        return items
    except Exception as e:
        print(f"  Error fetching {source['name']}: {e}")
        return []


# ── LOAD EXISTING FEED ─────────────────────────────────────────────────
def load_existing_feed():
    try:
        with open("feed.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def is_duplicate(title, existing_feed):
    """Simple dedup — skip if a very similar title already exists."""
    title_lower = title.lower()
    for item in existing_feed:
        existing_lower = item.get("title", "").lower()
        # Check for 60%+ word overlap
        words_new = set(title_lower.split())
        words_old = set(existing_lower.split())
        if len(words_new) > 0:
            overlap = len(words_new & words_old) / len(words_new)
            if overlap > 0.6:
                return True
    return False


# ── MAIN ───────────────────────────────────────────────────────────────
def run():
    print("Argus Intelligence Pipeline starting...")
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

    existing_feed = load_existing_feed()
    new_items = []
    next_id = max((item.get("id", 0) for item in existing_feed), default=0) + 1

    for source in SOURCES:
        print(f"\n[{source['jurisdiction']}] {source['name']}")
        raw_items = fetch_source(source)

        for title, summary, link in raw_items:
            # Skip duplicates
            if is_duplicate(title, existing_feed + new_items):
                print(f"  SKIP (duplicate): {title[:60]}...")
                continue

            print(f"  Analyzing: {title[:70]}...")
            result = analyze_item(title, summary, source["name"], source["jurisdiction"])

            if result is None:
                print(f"  → IRRELEVANT")
                continue

            print(f"  → RELEVANT [{result['urgency']}] {result['tag']}")

            # Build feed item
            feed_item = {
                "id": next_id,
                "j": source["jurisdiction"],
                "urgency": result["urgency"],
                "tag": result["tag"],
                "time": datetime.now(timezone.utc).strftime("%b %d %Y"),
                "unread": True,
                "title": result["title"],
                "summary": result["summary"],
                "action": result["action"],
                "affects": result.get("affects", []),
                "deadline": result.get("deadline", ""),
                "source": f"{source['name']} · {link[:80]}",
            }

            new_items.append(feed_item)
            next_id += 1

            # Be polite to the API
            time.sleep(1)

    if new_items:
        print(f"\n✓ Found {len(new_items)} new relevant items")
        # New items go to the top, keep last 50 total
        updated_feed = new_items + existing_feed
        updated_feed = updated_feed[:50]

        # Mark old items as read
        for item in updated_feed[len(new_items):]:
            item["unread"] = False

        with open("feed.json", "w") as f:
            json.dump(updated_feed, f, indent=2)
        print("✓ feed.json updated")
    else:
        print("\n— No new relevant items found today")
        # Still write file to confirm pipeline ran
        if not existing_feed:
            with open("feed.json", "w") as f:
                json.dump([], f)

    print("\nPipeline complete.")


if __name__ == "__main__":
    run()
