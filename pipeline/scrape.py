"""
Argus Intelligence Pipeline
Scrapes SEC, CFTC, ESMA, EBA regulatory sources, passes to Claude for analysis,
outputs feed.json that the live site reads.
"""

import json, os, re, time
from datetime import datetime, timezone
import feedparser
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SOURCES = [
    {"jurisdiction":"US","name":"SEC Press Releases","url":"https://www.sec.gov/rss/news/press.xml","tag":"ENFORCEMENT"},
    {"jurisdiction":"US","name":"CFTC Newsroom","url":"https://www.cftc.gov/rss/pressreleases.xml","tag":"MARKET STRUCTURE"},
    {"jurisdiction":"EU","name":"ESMA News","url":"https://www.esma.europa.eu/rss.xml","tag":"MICA"},
    {"jurisdiction":"EU","name":"EBA News","url":"https://www.eba.europa.eu/rss.xml","tag":"STABLECOINS"},
]

def analyze_item(title, summary, source_name, jurisdiction):
    prompt = f"""You are analyzing regulatory news for Argus Intelligence, serving institutional clients entering crypto and digital assets.

SOURCE: {source_name} ({jurisdiction})
TITLE: {title}
CONTENT: {summary[:1500]}

Is this relevant to ANY of: crypto, digital assets, stablecoins, DeFi, blockchain, virtual assets, tokenization, CBDCs, crypto custody, crypto AML/KYC, crypto exchanges, crypto securities law, fintech payments, or digital finance regulation?

Be INCLUSIVE — if there is ANY connection to digital assets or how traditional finance intersects with crypto, mark it relevant.

If NOT relevant (e.g. purely about physical commodities, non-digital traditional securities with no crypto angle): respond with exactly: IRRELEVANT

If relevant: respond with valid JSON only, no markdown:
{{
  "relevant": true,
  "urgency": "HIGH" or "MEDIUM" or "LOW",
  "tag": one of ["ENFORCEMENT","MARKET STRUCTURE","STABLECOINS","LICENSING","CUSTODY","AML/CFT","MICA","SANCTIONS"],
  "title": "concise professional title under 100 chars",
  "summary": "2-3 sentence institutional-grade summary of what changed and why it matters for crypto/digital asset firms",
  "action": "specific recommended action for a bank or fund with crypto exposure — 1-2 sentences",
  "affects": [] or list from: ["Aave","Uniswap","Lido","MakerDAO","dYdX","Tornado Cash"]
}}

Urgency: HIGH=enforcement action or binding deadline, MEDIUM=proposed rule or guidance, LOW=speech or non-binding update"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=600,
            messages=[{"role":"user","content":prompt}]
        )
        text = response.content[0].text.strip()
        if "IRRELEVANT" in text:
            return None
        text = re.sub(r"```json|```","",text).strip()
        data = json.loads(text)
        return data if data.get("relevant") else None
    except Exception as e:
        print(f"  Claude error: {e}")
        return None

def fetch_source(source):
    print(f"  Fetching {source['name']}...")
    try:
        feed = feedparser.parse(source["url"])
        items = []
        for entry in feed.entries[:10]:
            title = entry.get("title","")
            summary = entry.get("summary", entry.get("description",""))
            link = entry.get("link","")
            summary = re.sub(r"<[^>]+>"," ",summary).strip()
            if title:
                items.append((title, summary, link))
        return items
    except Exception as e:
        print(f"  Error: {e}")
        return []

def load_existing_feed():
    try:
        with open("feed.json","r") as f:
            return json.load(f)
    except:
        return []

def is_duplicate(title, existing):
    tl = title.lower()
    for item in existing:
        el = item.get("title","").lower()
        wn, wo = set(tl.split()), set(el.split())
        if len(wn) > 0 and len(wn & wo) / len(wn) > 0.6:
            return True
    return False

def run():
    print(f"Argus Pipeline starting — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
    existing = load_existing_feed()
    new_items = []
    next_id = max((i.get("id",0) for i in existing), default=0) + 1

    for source in SOURCES:
        print(f"\n[{source['jurisdiction']}] {source['name']}")
        for title, summary, link in fetch_source(source):
            if is_duplicate(title, existing + new_items):
                print(f"  SKIP: {title[:60]}...")
                continue
            print(f"  Analyzing: {title[:70]}...")
            result = analyze_item(title, summary, source["name"], source["jurisdiction"])
            if result is None:
                print(f"  → IRRELEVANT")
                continue
            print(f"  → RELEVANT [{result['urgency']}] {result['tag']}")
            new_items.append({
                "id": next_id, "j": source["jurisdiction"],
                "urgency": result["urgency"], "tag": result["tag"],
                "time": datetime.now(timezone.utc).strftime("%b %d %Y"),
                "unread": True, "title": result["title"],
                "summary": result["summary"], "action": result["action"],
                "affects": result.get("affects",[]),
                "deadline": result.get("deadline",""),
                "source": f"{source['name']} · {link[:80]}",
            })
            next_id += 1
            time.sleep(0.5)

    if new_items:
        print(f"\n✓ {len(new_items)} new relevant items found")
        updated = new_items + existing
        for item in updated[len(new_items):]:
            item["unread"] = False
        with open("feed.json","w") as f:
            json.dump(updated[:50], f, indent=2)
        print("✓ feed.json updated")
    else:
        print("\n— No new items today")
        # Write fallback so site always has something
        if not existing:
            with open("feed.json","w") as f:
                json.dump([], f)

    print("\nPipeline complete.")

if __name__ == "__main__":
    run()
