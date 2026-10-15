"""Easy Squawk fetcher: pulls headlines from open/official feeds, tags and scores them,
and merges them into data/feed.json. Standard library only. Run by GitHub Actions."""
import hashlib, json, os, re, sys, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# Put a real contact in the User-Agent: SEC requires it, and it is polite for all sources.
UA = "EasySquawk/0.1 (contact: you@example.com)"
OUT = "data/feed.json"
KEEP = 400

# (source name, url, default category). Check each source's terms before you rely on it.
FEEDS = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "Central Banks"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html", "Central Banks"),
    ("Bank of England", "https://www.bankofengland.co.uk/rss/news", "Central Banks"),
    ("SEC", "https://www.sec.gov/news/pressreleases.rss", "Filings"),
    ("SEC 8-K", "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&count=40&output=atom", "Filings"),
    ("BLS", "https://www.bls.gov/feed/bls_latest.rss", "Macro"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", "Equities"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "Equities"),
]
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode({
    "query": '(economy OR inflation OR "central bank" OR oil OR stocks OR tariffs) sourcelang:english',
    "mode": "artlist", "maxrecords": "50", "format": "json", "sort": "datedesc"})

RULES = [
    ("Crypto", r"bitcoin|crypto|ethereum|stablecoin"),
    ("Commodities", r"\boil\b|crude|opec|gold\b|copper|natural gas|wheat"),
    ("FX", r"dollar|euro\b|yen\b|sterling|currency|forex|\bfx\b"),
    ("Central Banks", r"central bank|interest rate|rate decision|fomc|\bfed\b|ecb|boe\b|powell|lagarde"),
    ("Macro", r"inflation|\bcpi\b|\bgdp\b|payrolls?|unemployment|jobs report|retail sales|\bpmi\b"),
    ("World", r"war|sanction|tariff|election|ceasefire|summit|geopolit"),
]
POS = "beat beats raises raised surge surges rally record upgrade strong gain gains growth profit approval wins eases".split()
NEG = "cut cuts miss misses falls fall drop drops plunge slump downgrade weak loss losses probe recall layoffs warning delay tariff default".split()


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def local(tag):
    return tag.split("}")[-1]


def to_ts(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).timestamp()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def parse_feed(raw):
    out = []
    for node in ET.fromstring(raw).iter():
        if local(node.tag) not in ("item", "entry"):
            continue
        d = {local(c.tag): c for c in node}
        title = (d["title"].text or "").strip() if "title" in d else ""
        link = ""
        if "link" in d:
            link = d["link"].get("href") or (d["link"].text or "").strip()
        when = ""
        for k in ("pubDate", "updated", "published", "date"):
            if k in d and d[k].text:
                when = d[k].text.strip()
                break
        if title and link:
            out.append((re.sub(r"\s+", " ", title), link, to_ts(when)))
    return out


def parse_gdelt(raw):
    out = []
    for a in json.loads(raw).get("articles", []):
        ts = None
        try:
            ts = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            pass
        out.append((a.get("title", "").strip(), a.get("url", ""), ts, a.get("domain", "GDELT")))
    return out


def categorize(title, default):
    t = title.lower()
    for cat, rx in RULES:
        if re.search(rx, t):
            return cat
    return default


def score(title):
    w = re.findall(r"[a-z']+", title.lower())
    p, n = sum(x in POS for x in w), sum(x in NEG for x in w)
    return round(max(-1, min(1, (p - n) / (p + n + 1) * 1.2)), 2)


def main():
    old = []
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding="utf-8")).get("items", [])
        except Exception:
            old = []
    items = {i["id"]: i for i in old}
    now, errors = datetime.now(timezone.utc).timestamp(), []

    def add(title, link, ts, source, default):
        if not title or not link:
            return
        i = hashlib.sha1(link.encode()).hexdigest()[:12]
        if i not in items:
            items[i] = {"id": i, "title": title[:220], "url": link, "source": source,
                        "cat": categorize(title, default), "score": score(title), "ts": ts or now}

    for name, url, cat in FEEDS:
        try:
            for title, link, ts in parse_feed(get(url)):
                add(title, link, ts, name, cat)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
    try:
        for title, link, ts, dom in parse_gdelt(get(GDELT)):
            add(title, link, ts, dom, "World")
    except Exception as e:
        errors.append(f"GDELT: {type(e).__name__}")

    ordered = sorted(items.values(), key=lambda x: x["ts"], reverse=True)[:KEEP]
    os.makedirs("data", exist_ok=True)
    json.dump({"updated": now, "errors": errors, "items": ordered}, open(OUT, "w", encoding="utf-8"))
    print(f"{len(ordered)} items, {len(errors)} source errors: {errors}")
    if len(errors) == len(FEEDS) + 1:
        sys.exit(1)


if __name__ == "__main__":
    main()
