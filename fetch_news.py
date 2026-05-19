"""Fetch Indonesian + Blitar news from many sources, dedupe across runs,
optionally enrich with Gemini, then push every new item to Telegram with photo."""

import html
import json
import os
import re
import sys
import time
import hashlib
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
def _env_int(name, default):
    v = (os.environ.get(name) or "").strip()
    return int(v) if v else default


GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "").strip() or "gemini-2.5-flash"
MAX_PER_RUN = _env_int("MAX_PER_RUN", 15)
STATE_FILE = (os.environ.get("STATE_FILE") or "").strip() or "state/seen.txt"
STATE_MAX = _env_int("STATE_MAX", 5000)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# National Indonesian RSS feeds
NATIONAL_FEEDS = [
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

# Blitar coverage via Google News RSS (covers ratusan portal sekaligus)
BLITAR_QUERIES = [
    "Blitar", "Kota Blitar", "Kabupaten Blitar",
    "Wlingi", "Sutojayan", "Kanigoro", "Srengat", "Garum",
    "Nglegok", "Talun", "Kesamben", "Gandusari", "Ponggok",
    "Sananwetan", "Kepanjenkidul", "Sukorejo", "Kademangan",
    "Selopuro", "Selorejo", "Doko", "Binangun", "Wates",
    "Panggungrejo", "Wonotirto", "Bakung",
    "Sanankulon", "Wonodadi", "Udanawu",
]

BLITAR_NEEDLES = [q.lower() for q in BLITAR_QUERIES]

CRITICAL_KEYWORDS = [
    "presiden", "menteri", "pemerintah", "kabinet", "parlemen", "dpr", "mpr",
    "rupiah", "inflasi", "resesi", "krisis", "apbn", "subsidi", "pajak", "bbm",
    "kpk", "korupsi", "gratifikasi", "suap", "tersangka", "kejaksaan", "mahkamah",
    "bencana", "gempa", "banjir", "longsor", "erupsi", "kebakaran", "tsunami",
    "teroris", "ledakan", "konflik", "kerusuhan", "demo", "unjuk rasa",
]

CATEGORY_EMOJI = {
    "Kriminal": "🚨", "Kecelakaan": "🚑", "Bencana": "🌊",
    "Kesehatan": "🏥", "Politik": "🏛️", "Pemerintahan": "🏛️",
    "Pendidikan": "🎓", "Ekonomi": "💰", "Bisnis": "💼",
    "Olahraga": "⚽", "Budaya": "🎭", "Wisata": "🏞️",
    "Inovasi": "💡", "Teknologi": "🤖", "Hiburan": "🎬",
    "Hukum": "⚖️", "Internasional": "🌐", "Lainnya": "📰",
}

TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"""<img[^>]+src=['"]([^'"]+)['"]""", re.IGNORECASE)
OG_IMG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|og:image:url|twitter:image|twitter:image:src)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\']\s*\d+\s*;\s*url=([^"\']+)["\']',
    re.IGNORECASE,
)
SCRAPE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# --- State (dedupe across runs) ----------------------------------------------

def load_seen():
    seen = set()
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    seen.add(line)
    except FileNotFoundError:
        pass
    return seen


def save_seen(seen):
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    # keep at most STATE_MAX most recent — preserve insertion order by reading file
    items = list(seen)
    if len(items) > STATE_MAX:
        items = items[-STATE_MAX:]
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        for k in items:
            fh.write(k + "\n")


def dedupe_key(article):
    base = article.get("link") or article.get("title") or ""
    return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()


# --- Fetching ----------------------------------------------------------------

def _absolutize(img, base_url):
    if not img:
        return None
    img = img.strip()
    if img.startswith("//"):
        return "https:" + img
    if img.startswith("/"):
        try:
            from urllib.parse import urlsplit
            p = urlsplit(base_url)
            return f"{p.scheme}://{p.netloc}{img}"
        except Exception:
            return None
    if img.startswith(("http://", "https://")):
        return img
    return None


def _http_get(url, timeout=8):
    return requests.get(
        url,
        timeout=timeout,
        allow_redirects=True,
        headers={
            "User-Agent": SCRAPE_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
        },
    )


def _find_in_gnews_html(html_text):
    # Cari URL artikel asli di halaman intermediate Google News.
    for pat in (
        r'data-n-au=["\']([^"\']+)["\']',
        r'data-url=["\'](https?://[^"\']+)["\']',
        r'href=["\'](https?://[^"\']+)["\'][^>]*data-articleurl',
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
    ):
        m = re.search(pat, html_text)
        if m:
            u = m.group(1)
            if "news.google.com" not in u and u.startswith("http"):
                return u
    return None


def scrape_og_image(url, timeout=8, hops_left=2):
    """Best-effort: fetch URL, follow meta-refresh & Google News redirects,
    return og:image / twitter:image URL or None."""
    if not url or hops_left <= 0:
        return None
    try:
        r = _http_get(url, timeout=timeout)
    except Exception as e:
        print(f"[warn] scrape_og GET {url[:80]}: {e}", file=sys.stderr)
        return None
    if r.status_code >= 400:
        return None
    text = r.text or ""
    snippet = text[:200000]
    final_url = r.url or url

    # Google News intermediate: hop to real URL
    if "news.google.com" in final_url:
        real = _find_in_gnews_html(snippet)
        if real:
            return scrape_og_image(real, timeout=timeout, hops_left=hops_left - 1)
        # fallback: meta-refresh
        mref = META_REFRESH_RE.search(snippet)
        if mref:
            return scrape_og_image(mref.group(1), timeout=timeout, hops_left=hops_left - 1)
        return None

    # Direct og:image / twitter:image
    m = OG_IMG_RE.search(snippet)
    if m:
        return _absolutize(m.group(1), final_url)

    # Meta-refresh anywhere
    mref = META_REFRESH_RE.search(snippet)
    if mref:
        nxt = mref.group(1)
        if not nxt.startswith("http"):
            nxt = _absolutize(nxt, final_url)
        if nxt:
            return scrape_og_image(nxt, timeout=timeout, hops_left=hops_left - 1)
    return None


def extract_image(entry):
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


def parse_feed(source, url, scope, max_entries=25):
    out = []
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"[warn] {source}: {e}", file=sys.stderr)
        return out
    for entry in feed.entries[:max_entries]:
        pub = None
        if getattr(entry, "published_parsed", None):
            pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        out.append({
            "title": getattr(entry, "title", "") or "",
            "summary": getattr(entry, "summary", "") or "",
            "link": getattr(entry, "link", "") or "",
            "source": source,
            "scope": scope,  # "national" | "blitar"
            "published": pub,
            "image": extract_image(entry),
        })
    return out


def google_news_url(query):
    return (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": query, "hl": "id", "gl": "ID", "ceid": "ID:id"})
    )


def fetch_all():
    articles = []
    for source, url in NATIONAL_FEEDS:
        articles += parse_feed(source, url, scope="national", max_entries=15)
    for q in BLITAR_QUERIES:
        articles += parse_feed(f"Google News · {q}", google_news_url(q),
                               scope="blitar", max_entries=10)
        time.sleep(0.2)  # polite
    return articles


def is_blitar_relevant(article):
    blob = f"{article['title']} {article['summary']}".lower()
    return any(n in blob for n in BLITAR_NEEDLES)


# --- Ranking (national only) -------------------------------------------------

def score(article):
    text = (article["title"] + " " + article["summary"]).lower()
    hits = sum(1 for kw in CRITICAL_KEYWORDS if kw in text)
    recency = 0
    if article["published"]:
        age_h = (datetime.now(timezone.utc) - article["published"]).total_seconds() / 3600
        recency = max(0.0, 6.0 - age_h)
    return hits * 2 + recency


# --- Gemini enrichment -------------------------------------------------------

def clean_summary(s, limit=600):
    s = TAG_RE.sub("", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:limit]


def gemini_enrich_batch(articles):
    """Enrich many articles in ONE Gemini call to stay under free-tier RPM.
    Returns list aligned with input: [{category, summary}, ...]."""
    empty = [{"category": "", "summary": ""} for _ in articles]
    if not GEMINI_API_KEY or not articles:
        return empty

    items = [{
        "id": i,
        "title": a["title"],
        "source": a.get("source", ""),
        "summary": clean_summary(a["summary"], 500),
    } for i, a in enumerate(articles)]

    prompt = (
        "Kamu adalah editor berita Indonesia. Berikut daftar artikel dalam JSON:\n\n"
        f"{json.dumps(items, ensure_ascii=False)}\n\n"
        "Tugas: untuk SETIAP artikel di list, klasifikasikan ke SATU kategori "
        "berikut: Kriminal, Kecelakaan, Bencana, Kesehatan, Politik, "
        "Pemerintahan, Pendidikan, Ekonomi, Bisnis, Olahraga, Budaya, "
        "Wisata, Inovasi, Teknologi, Hiburan, Hukum, Internasional, Lainnya. "
        "Lalu buat ringkasan 2-3 kalimat Bahasa Indonesia yang factual, "
        "menonjolkan lokasi spesifik bila berita Blitar "
        "(kota/kabupaten/kecamatan/desa).\n\n"
        "Balas JSON array dengan tepat satu elemen per artikel input, "
        "struktur: {id, category, summary}. Urutkan sesuai id input."
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 350 * len(articles),
            "responseMimeType": "application/json",
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    try:
        r = requests.post(url, json=body, timeout=90)
        data = r.json()
    except Exception as e:
        print(f"[warn] gemini http: {e}", file=sys.stderr)
        return empty
    cands = data.get("candidates") or []
    if not cands:
        err = data.get("error") or {}
        if err:
            print(f"[warn] gemini api: {err.get('message','')}", file=sys.stderr)
        return empty
    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            print(f"[warn] gemini: tidak bisa parse JSON", file=sys.stderr)
            return empty
        try:
            parsed = json.loads(m.group(0))
        except Exception as e:
            print(f"[warn] gemini parse: {e}", file=sys.stderr)
            return empty

    if not isinstance(parsed, list):
        return empty

    out = [{"category": "", "summary": ""} for _ in articles]
    for elem in parsed:
        if not isinstance(elem, dict):
            continue
        try:
            idx = int(elem.get("id"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(out):
            out[idx] = {
                "category": (elem.get("category") or "").strip() or "Lainnya",
                "summary": (elem.get("summary") or "").strip(),
            }
    return out


# --- Telegram ----------------------------------------------------------------

def esc(s):
    return html.escape(s or "", quote=True)


def now_wib():
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).strftime("%d %b %Y, %H:%M WIB")


def build_caption(article):
    scope_tag = "📍 BLITAR" if article["scope"] == "blitar" else "🇮🇩 NASIONAL"
    cat = article.get("ai_category") or ""
    emoji = CATEGORY_EMOJI.get(cat, "📰")
    title = esc(article["title"])
    source = esc(article["source"])
    link = esc(article["link"])
    summary = esc(article.get("ai_summary") or clean_summary(article["summary"], 300))

    header = f"{emoji} <b>{title}</b>"
    meta = f"<i>{scope_tag}</i>"
    if cat:
        meta += f" · <i>{esc(cat)}</i>"
    meta += f" · {source}"

    body = f"{header}\n{meta}\n\n{summary}\n\n🔗 <a href=\"{link}\">Baca selengkapnya</a>"
    if len(body) > 1024:
        body = body[:1020] + "..."
    return body


def tg_post(endpoint, payload, expect_ok=True):
    r = requests.post(f"{API}/{endpoint}", data=payload, timeout=30)
    try:
        body = r.json()
    except Exception:
        body = {}
    if expect_ok and not body.get("ok"):
        print(f"[warn] {endpoint} failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
    return r, body


def _strip_html_tags(s):
    return TAG_RE.sub("", html.unescape(s or ""))


def send_article(article):
    caption = build_caption(article)
    if article["image"]:
        r, body = tg_post("sendPhoto", {
            "chat_id": CHAT_ID,
            "photo": article["image"],
            "caption": caption,
            "parse_mode": "HTML",
        }, expect_ok=False)
        if body.get("ok"):
            return True
        print(f"[warn] sendPhoto HTML failed, retrying as plain", file=sys.stderr)
        r, body = tg_post("sendPhoto", {
            "chat_id": CHAT_ID,
            "photo": article["image"],
            "caption": _strip_html_tags(caption)[:1020],
        }, expect_ok=False)
        if body.get("ok"):
            return True
        print(f"[warn] sendPhoto plain also failed, fallback to text", file=sys.stderr)
    r, body = tg_post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": caption,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }, expect_ok=False)
    if body.get("ok"):
        return True
    # Last resort: plain text (no parse_mode) so Telegram can't reject parsing
    r, body = tg_post("sendMessage", {
        "chat_id": CHAT_ID,
        "text": _strip_html_tags(caption),
        "disable_web_page_preview": False,
    })
    return bool(body.get("ok"))


# --- Main --------------------------------------------------------------------

def main():
    seen = load_seen()
    print(f"loaded {len(seen)} seen keys from {STATE_FILE}")

    articles = fetch_all()
    if not articles:
        sys.exit("no articles fetched from any source")

    # Dedupe within this run AND against state
    new_articles = []
    keys_in_run = set()
    for a in articles:
        if not a.get("link") or not a.get("title"):
            continue
        # Drop national items that aren't critical enough — too noisy otherwise
        if a["scope"] == "national" and score(a) < 2:
            continue
        # Blitar items: keep only if actually mentions Blitar (Google News
        # query may return weak matches)
        if a["scope"] == "blitar" and not is_blitar_relevant(a):
            continue
        k = dedupe_key(a)
        if k in seen or k in keys_in_run:
            continue
        keys_in_run.add(k)
        a["_key"] = k
        new_articles.append(a)

    # Sort: most recent first
    new_articles.sort(
        key=lambda a: a["published"] or datetime.fromtimestamp(0, tz=timezone.utc),
        reverse=True,
    )

    # Cap to avoid flooding Telegram
    capped = new_articles[:MAX_PER_RUN]
    print(f"new={len(new_articles)} sending={len(capped)} "
          f"(national={sum(1 for a in capped if a['scope']=='national')} "
          f"blitar={sum(1 for a in capped if a['scope']=='blitar')})")

    if not capped:
        save_seen(seen | keys_in_run)  # still persist anything dropped via cap? no, only sent
        return

    # Cari gambar untuk yang belum punya (Google News RSS tidak menyertakan)
    no_img = sum(1 for a in capped if not a["image"])
    if no_img:
        print(f"scraping og:image for {no_img} articles without RSS image")
    for a in capped:
        if a["image"]:
            continue
        img = scrape_og_image(a["link"])
        if img:
            a["image"] = img

    # Enrich with Gemini (single batched call, hemat kuota free tier)
    enriched = gemini_enrich_batch(capped)
    for a, e in zip(capped, enriched):
        a["ai_category"] = e["category"]
        a["ai_summary"] = e["summary"]

    # Send
    sent_keys = set()
    for a in capped:
        if send_article(a):
            sent_keys.add(a["_key"])
            time.sleep(1.2)  # respect Telegram rate

    # Persist only what we successfully sent (so failed sends retry next run)
    save_seen(seen | sent_keys)
    print(f"sent {len(sent_keys)} articles; state now {len(seen | sent_keys)} keys")


if __name__ == "__main__":
    main()
