"""Fetch latest Indonesian news from RSS feeds and send top 3 to Telegram with photos."""

import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

FEEDS = [
    ("Antara Politik", "https://www.antaranews.com/rss/politik.xml"),
    ("Antara Ekonomi", "https://www.antaranews.com/rss/ekonomi.xml"),
    ("Antara Hukum", "https://www.antaranews.com/rss/hukum.xml"),
    ("Antara Peristiwa", "https://www.antaranews.com/rss/peristiwa.xml"),
    ("Detik News", "https://news.detik.com/berita/rss"),
    ("BBC Indonesia", "https://feeds.bbci.co.uk/indonesia/rss.xml"),
    ("Tempo Nasional", "https://rss.tempo.co/nasional"),
    ("CNN Indonesia", "https://www.cnnindonesia.com/nasional/rss"),
    ("Kompas Nasional", "https://www.kompas.com/getrss/news"),
]

CRITICAL_KEYWORDS = [
    "presiden", "menteri", "pemerintah", "kabinet", "parlemen", "dpr", "mpr",
    "rupiah", "inflasi", "resesi", "krisis", "apbn", "subsidi", "pajak", "bbm",
    "kpk", "korupsi", "gratifikasi", "suap", "tersangka", "kejaksaan", "mahkamah",
    "bencana", "gempa", "banjir", "longsor", "erupsi", "kebakaran", "tsunami",
    "teroris", "ledakan", "konflik", "kerusuhan", "demo", "unjuk rasa",
]

TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"""<img[^>]+src=['"]([^'"]+)['"]""", re.IGNORECASE)


def extract_image(entry):
    """Return first usable image URL from an RSS entry, or None."""
    for m in getattr(entry, "media_content", []) or []:
        url = m.get("url")
        if url and url.startswith(("http://", "https://")):
            return url
    for m in getattr(entry, "media_thumbnail", []) or []:
        url = m.get("url")
        if url and url.startswith(("http://", "https://")):
            return url
    for link in getattr(entry, "links", []) or []:
        if link.get("rel") == "enclosure" and "image" in link.get("type", ""):
            href = link.get("href")
            if href and href.startswith(("http://", "https://")):
                return href
    summary = getattr(entry, "summary", "") or ""
    m = IMG_RE.search(summary)
    if m:
        url = m.group(1)
        if url.startswith(("http://", "https://")):
            return url
    return None


def fetch_all():
    articles = []
    for source, url in FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                pub = None
                if getattr(entry, "published_parsed", None):
                    pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                articles.append({
                    "title": entry.title,
                    "summary": getattr(entry, "summary", ""),
                    "link": entry.link,
                    "source": source,
                    "published": pub,
                    "image": extract_image(entry),
                })
        except Exception as e:
            print(f"[warn] {source}: {e}", file=sys.stderr)
    return articles


def score(article):
    text = (article["title"] + " " + article["summary"]).lower()
    hits = sum(1 for kw in CRITICAL_KEYWORDS if kw in text)
    recency = 0
    if article["published"]:
        age_h = (datetime.now(timezone.utc) - article["published"]).total_seconds() / 3600
        recency = max(0.0, 6.0 - age_h)
    return hits * 2 + recency


def pick_top(articles, n=3):
    seen, unique = set(), []
    for a in sorted(articles, key=score, reverse=True):
        key = a["title"][:50].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
        if len(unique) >= n:
            break
    return unique


def esc(s):
    return html.escape(s or "", quote=False)


def clean_summary(s, limit=300):
    s = TAG_RE.sub("", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:limit] + ("..." if len(s) > limit else "")


def now_wib():
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).strftime("%d %b %Y, %H:%M WIB")


def caption_for(idx, a):
    title = esc(a["title"])
    summary = esc(clean_summary(a["summary"]))
    link = esc(a["link"])
    source = esc(a["source"])
    body = f"<b>{idx}. {title}</b>\n\n{summary}\n\n🔗 <a href=\"{link}\">{source}</a>"
    # Telegram caption limit: 1024 chars
    return body[:1020] + "..." if len(body) > 1024 else body


def post(endpoint, payload, expect_ok=True):
    r = requests.post(f"{API}/{endpoint}", data=payload, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {}
    if expect_ok and not body.get("ok"):
        print(f"[warn] {endpoint} failed: {r.status_code} {r.text}", file=sys.stderr)
    return r, body


def send_header():
    text = f"📰 <b>Berita Indonesia – {now_wib()}</b>"
    post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def send_article(idx, a):
    caption = caption_for(idx, a)
    if a["image"]:
        r, body = post("sendPhoto", {
            "chat_id": CHAT_ID,
            "photo": a["image"],
            "caption": caption,
            "parse_mode": "HTML",
        }, expect_ok=False)
        if body.get("ok"):
            return
        print(f"[warn] sendPhoto failed for #{idx}, falling back to text", file=sys.stderr)
    # Text fallback (no image, or sendPhoto failed)
    post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": caption,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })


def main():
    articles = fetch_all()
    if not articles:
        sys.exit("no articles fetched from any feed")
    top = pick_top(articles, n=3)
    print(f"selected {len(top)} articles (images: {sum(1 for a in top if a['image'])})")
    for i, a in enumerate(top, 1):
        print(f"  {i}. [{a['source']}] {a['title']}  img={'yes' if a['image'] else 'no'}")
    send_header()
    for i, a in enumerate(top, 1):
        send_article(i, a)
    print("done")


if __name__ == "__main__":
    main()
