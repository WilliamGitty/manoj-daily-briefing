#!/usr/bin/env python3
"""
Builds index.html for Manoj's Daily Briefing from live RSS feeds.

No LLM involvement: every headline/summary/link comes verbatim from the
feed's own <title>/<description>/<link>, filtered by the feed's own
published/updated timestamp. This avoids the hallucinated-URL failure mode
of asking an LLM to "search the web" and report back.
"""

import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser

LOOKBACK_HOURS = 24
MAX_ITEMS_PER_SECTION = 4
MIN_ITEMS_PER_SECTION = 2

BBC_TECH = "http://feeds.bbci.co.uk/news/technology/rss.xml"
TECHCRUNCH_AI = "https://techcrunch.com/category/artificial-intelligence/feed/"
BBC_POLITICS = "http://feeds.bbci.co.uk/news/politics/rss.xml"
BBC_WORLD = "http://feeds.bbci.co.uk/news/world/rss.xml"
BBC_HEALTH = "http://feeds.bbci.co.uk/news/health/rss.xml"
ESPN_CRICKET = "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"
BBC_SPORT = "http://feeds.bbci.co.uk/sport/rss.xml"
BBC_INDIA = "http://feeds.bbci.co.uk/news/world/asia/india/rss.xml"

# Order matters for dedupe: earlier sections in this list "claim" a story
# first only where explicitly noted below (BCCI claims from Cricket/India).
SECTIONS = [
    {"key": "tech", "title": "Tech", "feeds": [BBC_TECH]},
    {"key": "ai", "title": "AI", "feeds": [TECHCRUNCH_AI]},
    {"key": "politics_uk", "title": "Politics — UK", "feeds": [BBC_POLITICS]},
    {"key": "politics_global", "title": "Politics — Global", "feeds": [BBC_WORLD]},
    {"key": "health", "title": "Health & Fitness", "feeds": [BBC_HEALTH]},
    {"key": "bcci", "title": "BCCI", "feeds": [ESPN_CRICKET], "keyword": "bcci"},
    {"key": "cricket", "title": "Cricket", "feeds": [ESPN_CRICKET]},
    {"key": "sport", "title": "Sport", "feeds": [BBC_SPORT]},
    {"key": "india", "title": "India News", "feeds": [BBC_INDIA]},
]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(raw):
    if not raw:
        return ""
    text = html.unescape(TAG_RE.sub("", raw))
    text = WS_RE.sub(" ", text).strip()
    return text


def first_two_sentences(text):
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:2]).strip()


def entry_published(entry):
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            return datetime(*val[:6], tzinfo=timezone.utc)
    return None


def source_name(entry, feed_url):
    host = urlparse(entry.get("link", feed_url)).netloc
    host = host.replace("www.", "")
    known = {
        "bbc.co.uk": "BBC News",
        "feeds.bbci.co.uk": "BBC News",
        "techcrunch.com": "TechCrunch",
        "espncricinfo.com": "ESPNcricinfo",
    }
    for k, v in known.items():
        if k in host:
            return v
    return host or "Source"


def fetch_recent_items(feed_url, cutoff, keyword=None):
    parsed = feedparser.parse(feed_url)
    items = []
    seen_links = set()
    for entry in parsed.entries:
        published = entry_published(entry)
        if published is None or published < cutoff:
            continue
        title = clean_text(entry.get("title", ""))
        summary = first_two_sentences(clean_text(entry.get("summary", entry.get("description", ""))))
        link = entry.get("link", "")
        if link in seen_links:
            continue
        seen_links.add(link)
        if keyword:
            haystack = f"{title} {summary}".lower()
            if keyword.lower() not in haystack:
                continue
        items.append(
            {
                "title": title,
                "summary": summary,
                "link": link,
                "published": published,
                "source": source_name(entry, feed_url),
            }
        )
    items.sort(key=lambda i: i["published"], reverse=True)
    return items


def build_sections():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    used_links = set()
    rendered_sections = []

    for section in SECTIONS:
        candidates = []
        for feed_url in section["feeds"]:
            candidates.extend(fetch_recent_items(feed_url, cutoff, keyword=section.get("keyword")))

        # Dedupe: skip stories already claimed by an earlier (more specific) section.
        deduped = [c for c in candidates if c["link"] not in used_links]

        picked = deduped[:MAX_ITEMS_PER_SECTION]
        for item in picked:
            used_links.add(item["link"])

        rendered_sections.append({"title": section["title"], "items": picked})

    return rendered_sections


def render_html(sections, today_str):
    story_blocks = []
    for section in sections:
        if section["items"]:
            stories_html = "\n".join(
                f'''        <article class="story">
          <a class="headline" href="{html.escape(item['link'])}" target="_blank" rel="noopener">{html.escape(item['title'])}</a>
          <p class="summary">{html.escape(item['summary'])}</p>
          <span class="source"><a href="{html.escape(item['link'])}" target="_blank" rel="noopener">{html.escape(item['source'])}</a></span>
        </article>'''
                for item in section["items"]
            )
        else:
            stories_html = '        <p class="no-story">No qualifying story in the last 24 hours.</p>'

        story_blocks.append(
            f'''      <section class="section">
        <h2>{html.escape(section['title'])}</h2>
{stories_html}
      </section>'''
        )

    sections_html = "\n".join(story_blocks)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Manoj's Daily Briefing — {today_str}</title>
<style>
  :root {{
    --navy: #1a2a4a;
    --cream: #f4f4f2;
    --muted: #767676;
    --source: #999999;
    --rule: #d8d4c8;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--cream);
    font-family: Georgia, 'Times New Roman', serif;
    color: #1a1a1a;
  }}
  .masthead {{
    background: var(--navy);
    padding: 32px 20px;
    text-align: center;
  }}
  .masthead h1 {{
    margin: 0;
    color: #ffffff;
    font-size: clamp(24px, 5vw, 34px);
    font-weight: bold;
    letter-spacing: 0.5px;
  }}
  .masthead .date {{
    display: block;
    margin-top: 8px;
    color: #c9d2e3;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 14px;
    letter-spacing: 0.5px;
  }}
  .intro {{
    max-width: 680px;
    margin: 16px auto 0;
    padding: 0 20px;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 12px;
    color: var(--muted);
    font-style: italic;
    text-align: center;
  }}
  main {{
    max-width: 680px;
    margin: 0 auto;
    padding: 8px 20px 60px;
  }}
  .section {{
    margin-top: 32px;
  }}
  .section h2 {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 15px;
    color: var(--navy);
    text-transform: uppercase;
    letter-spacing: 1px;
    border-bottom: 2px solid var(--navy);
    padding-bottom: 6px;
    margin: 0 0 14px;
  }}
  .story {{
    padding: 14px 0;
    border-bottom: 1px solid var(--rule);
  }}
  .story:last-child {{
    border-bottom: none;
  }}
  .headline {{
    display: block;
    font-family: Georgia, 'Times New Roman', serif;
    font-size: 17px;
    font-weight: bold;
    color: var(--navy);
    text-decoration: none;
    line-height: 1.35;
  }}
  .headline:hover {{
    text-decoration: underline;
  }}
  .summary {{
    margin: 6px 0 8px;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 14px;
    color: var(--muted);
    line-height: 1.5;
  }}
  .source {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11px;
    font-variant: small-caps;
    letter-spacing: 0.5px;
  }}
  .source a {{
    color: var(--source);
    text-decoration: none;
  }}
  .source a:hover {{
    text-decoration: underline;
  }}
  .no-story {{
    padding: 6px 0 0;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
    color: var(--source);
    font-style: italic;
  }}
  footer {{
    max-width: 680px;
    margin: 0 auto 40px;
    padding: 0 20px;
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11px;
    color: var(--source);
    text-align: center;
  }}
</style>
</head>
<body>
  <div class="masthead">
    <h1>Manoj's Daily Briefing</h1>
    <span class="date">{today_str}</span>
  </div>
  <p class="intro">All stories below were published within the last 24 hours, pulled directly from source RSS feeds. Where a section has no qualifying story, that is stated explicitly.</p>
  <main>
{sections_html}
  </main>
  <footer>Built automatically from live RSS feeds &mdash; no AI-generated content.</footer>
</body>
</html>
'''


def main():
    today_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    sections = build_sections()
    output = render_html(sections, today_str)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(output)

    # Console summary for local testing / CI logs.
    for section in sections:
        print(f"\n=== {section['title']} ({len(section['items'])} item(s)) ===")
        if not section["items"]:
            print("  (no qualifying story in the last 24 hours)")
        for item in section["items"]:
            print(f"  - {item['title']}")
            print(f"    published: {item['published'].isoformat()}")
            print(f"    link: {item['link']}")


if __name__ == "__main__":
    main()
