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
import requests
from bs4 import BeautifulSoup

LOOKBACK_HOURS = 24
MAX_ITEMS_PER_SECTION = 4
MIN_ITEMS_PER_SECTION = 2
SCRAPE_TIMEOUT_SECONDS = 6
SCRAPE_MIN_CHARS = 80
PROMO_KEYWORDS = [
    "giveaway",
    "sweepstakes",
    "coupon",
    "promo code",
    "discount code",
    "enter to win",
    "win a ",
    "/deals/",
]
USER_AGENT = (
    "Mozilla/5.0 (compatible; ManojDailyBriefingBot/1.0; "
    "+https://github.com/WilliamGitty/manoj-daily-briefing)"
)

BBC_TECH = "http://feeds.bbci.co.uk/news/technology/rss.xml"
VERGE_TECH = "https://www.theverge.com/rss/tech/index.xml"
ARSTECHNICA_TECH = "https://feeds.arstechnica.com/arstechnica/index"

TECHCRUNCH_AI = "https://techcrunch.com/category/artificial-intelligence/feed/"
VERGE_AI = "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"
ARSTECHNICA_AI = "https://arstechnica.com/ai/feed/"

BBC_POLITICS = "http://feeds.bbci.co.uk/news/politics/rss.xml"
GUARDIAN_POLITICS = "https://www.theguardian.com/politics/rss"
FT_POLITICS = "https://www.ft.com/political-fix?format=rss"
ECONOMIST_BRITAIN = "https://www.economist.com/britain/rss.xml"

BBC_WORLD = "http://feeds.bbci.co.uk/news/world/rss.xml"
GUARDIAN_WORLD = "https://www.theguardian.com/world/rss"
AL_JAZEERA = "https://www.aljazeera.com/xml/rss/all.xml"
ECONOMIST_INTERNATIONAL = "https://www.economist.com/international/rss.xml"

BBC_HEALTH = "http://feeds.bbci.co.uk/news/health/rss.xml"
SCIENCEDAILY_HEALTH = "https://www.sciencedaily.com/rss/top/health.xml"
STATNEWS = "https://www.statnews.com/feed/"

ESPN_CRICKET = "https://www.espncricinfo.com/rss/content/story/feeds/0.xml"

BBC_SPORT = "http://feeds.bbci.co.uk/sport/rss.xml"
SKY_SPORTS_NEWS = "https://www.skysports.com/rss/12040"

BBC_INDIA = "http://feeds.bbci.co.uk/news/world/asia/india/rss.xml"
HINDUSTAN_TIMES_INDIA = "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"
TIMES_OF_INDIA = "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms"

# Order matters for dedupe: earlier sections in this list "claim" a story
# first only where explicitly noted below (BCCI claims from Cricket/India).
SECTIONS = [
    {"key": "tech", "title": "Tech", "feeds": [BBC_TECH, VERGE_TECH, ARSTECHNICA_TECH]},
    {"key": "ai", "title": "AI", "feeds": [TECHCRUNCH_AI, VERGE_AI, ARSTECHNICA_AI]},
    {
        "key": "politics_uk",
        "title": "Politics — UK",
        "feeds": [BBC_POLITICS, GUARDIAN_POLITICS, FT_POLITICS, ECONOMIST_BRITAIN],
    },
    {
        "key": "politics_global",
        "title": "Politics — Global",
        "feeds": [BBC_WORLD, GUARDIAN_WORLD, AL_JAZEERA, ECONOMIST_INTERNATIONAL],
    },
    {"key": "health", "title": "Health & Fitness", "feeds": [BBC_HEALTH, SCIENCEDAILY_HEALTH, STATNEWS]},
    {"key": "bcci", "title": "BCCI", "feeds": [ESPN_CRICKET], "keyword": "bcci"},
    {"key": "cricket", "title": "Cricket", "feeds": [ESPN_CRICKET]},
    {"key": "sport", "title": "Sport", "feeds": [BBC_SPORT, SKY_SPORTS_NEWS]},
    {
        "key": "india",
        "title": "India News",
        "feeds": [BBC_INDIA, HINDUSTAN_TIMES_INDIA, TIMES_OF_INDIA],
    },
]

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(raw):
    if not raw:
        return ""
    text = html.unescape(TAG_RE.sub("", raw))
    text = WS_RE.sub(" ", text).strip()
    return text


MAX_SUMMARY_CHARS = 500


def trim_summary(text):
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = []
    length = 0
    for part in parts:
        if out and length + len(part) > MAX_SUMMARY_CHARS:
            break
        out.append(part)
        length += len(part) + 1
    return " ".join(out).strip()


def scrape_article_paragraph(url):
    """Fetch the linked article and pull its opening real body text.

    Returns None on any failure so callers can fall back to the feed's own
    teaser summary. Never fabricates text - only extracts what's actually
    on the page.
    """
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=SCRAPE_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "aside", "footer", "header", "figure"]):
            tag.decompose()
        container = soup.find("article") or soup.body
        if container is None:
            return None
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) > 40]
        paragraphs = list(dict.fromkeys(paragraphs))
        if not paragraphs:
            return None
        return trim_summary(" ".join(paragraphs))
    except requests.RequestException:
        return None


def is_live_blog(link):
    return "/live/" in link.lower()


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
        "cricinfo.com": "ESPNcricinfo",
        "theverge.com": "The Verge",
        "arstechnica.com": "Ars Technica",
        "theguardian.com": "The Guardian",
        "ft.com": "Financial Times",
        "economist.com": "The Economist",
        "aljazeera.com": "Al Jazeera",
        "sciencedaily.com": "ScienceDaily",
        "statnews.com": "STAT News",
        "skysports.com": "Sky Sports",
        "hindustantimes.com": "Hindustan Times",
        "timesofindia.indiatimes.com": "Times of India",
        "indiatimes.com": "Times of India",
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
        summary = trim_summary(clean_text(entry.get("summary", entry.get("description", ""))))
        link = entry.get("link", "")
        if not link.lower().startswith(("http://", "https://")):
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        promo_haystack = f"{title} {link}".lower()
        if any(kw in promo_haystack for kw in PROMO_KEYWORDS):
            continue
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


def select_diverse(candidates):
    """Pick up to MAX_ITEMS_PER_SECTION items, round-robin across sources.

    Candidates must already be sorted by recency (most recent first). This
    stops any single prolific outlet from filling a whole section just
    because it happened to publish the most today - each source gets one
    slot per round before any source gets a second. Rolling live-blogs are
    capped at one slot total, since their timestamp keeps refreshing all
    day. Falls back to a single source's own stories if nothing else in
    the lookback window qualifies - no padding with stale content either way.
    """
    queues = {}
    order = []
    for item in candidates:
        if item["source"] not in queues:
            queues[item["source"]] = []
            order.append(item["source"])
        queues[item["source"]].append(item)

    picked = []
    live_blog_count = 0
    made_progress = True
    while len(picked) < MAX_ITEMS_PER_SECTION and made_progress:
        made_progress = False
        for source in order:
            if len(picked) >= MAX_ITEMS_PER_SECTION:
                break
            queue = queues[source]
            while queue:
                candidate = queue.pop(0)
                if is_live_blog(candidate["link"]):
                    if live_blog_count >= 1:
                        continue
                    live_blog_count += 1
                picked.append(candidate)
                made_progress = True
                break
    return picked


def build_sections():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    used_links = set()
    rendered_sections = []

    for section in SECTIONS:
        candidates = []
        for feed_url in section["feeds"]:
            candidates.extend(fetch_recent_items(feed_url, cutoff, keyword=section.get("keyword")))

        # Merge multiple sources by recency, not by feed order.
        candidates.sort(key=lambda i: i["published"], reverse=True)

        # Dedupe: skip stories already claimed by an earlier (more specific) section.
        deduped = [c for c in candidates if c["link"] not in used_links]

        picked = select_diverse(deduped)

        for item in picked:
            used_links.add(item["link"])
            scraped = scrape_article_paragraph(item["link"])
            if scraped and len(scraped) > max(len(item["summary"]), SCRAPE_MIN_CHARS):
                item["summary"] = scraped

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


MIN_SECTIONS_WITH_CONTENT = len(SECTIONS) // 2


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

    # Sanity check: a single feed going quiet is normal and expected (that
    # section just says so honestly). Most sections going empty at once is
    # not normal - it means something broke pipeline-wide (feedparser,
    # network, a shared bug), not that the news dried up. Fail loudly so
    # GitHub's automatic failure email fires and this deploy is blocked,
    # leaving yesterday's good page live instead of publishing a near-empty
    # briefing.
    sections_with_content = sum(1 for s in sections if s["items"])
    if sections_with_content < MIN_SECTIONS_WITH_CONTENT:
        raise SystemExit(
            f"Only {sections_with_content}/{len(sections)} sections had any "
            f"qualifying stories (need at least {MIN_SECTIONS_WITH_CONTENT}) - "
            "this looks like a pipeline-wide failure, not a quiet news day. "
            "Failing the build so the last good deploy stays live."
        )


if __name__ == "__main__":
    main()
