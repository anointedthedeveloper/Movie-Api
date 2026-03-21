import time
import re
import requests
from functools import lru_cache

BASE_SITE = "https://downloader2.com"
API_BASE  = "https://h5-api.aoneroom.com/wefeed-h5api-bff"

API_HEADERS = {
    "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Accept":         "application/json",
    "Content-Type":   "application/json",
    "Origin":         BASE_SITE,
    "Referer":        BASE_SITE + "/",
    "x-request-lang": "en",
    "x-client-info":  '{"timezone":"Africa/Lagos"}',
}

DOWNLOAD_HEADERS = {
    "User-Agent": API_HEADERS["User-Agent"],
    "Referer":    BASE_SITE + "/",
    "Origin":     BASE_SITE,
    "Accept":     "*/*",
}

session = requests.Session()
session.headers.update(API_HEADERS)

# ── Featured / Home ─────────────────────────────────────────────────────────

def get_featured(page: int = 1, page_size: int = 18, tab_id: str = "") -> dict:
    params = {"page": page, "perPage": page_size}
    if tab_id:
        params["tabId"] = tab_id
    resp = session.get(f"{API_BASE}/subject/trending", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("data", {})


# ── Search ────────────────────────────────────────────────────────────────────

_search_cache: dict[str, tuple[dict, float]] = {}
_SEARCH_TTL = 300  # 5 minutes

def search(query: str, page: int = 1) -> dict:
    key = f"{query.lower().strip()}:{page}"
    cached = _search_cache.get(key)
    if cached and time.time() - cached[1] < _SEARCH_TTL:
        return cached[0]
    resp = session.post(f"{API_BASE}/subject/search", json={"keyword": query, "page": page, "pageSize": 24}, timeout=15)
    resp.raise_for_status()
    result = resp.json().get("data", {})
    _search_cache[key] = (result, time.time())
    return result


# ── Detail ────────────────────────────────────────────────────────────────────

def _extract_cover(subject: dict) -> str:
    for key in ("coverUrl", "coverImage", "cover"):
        val = subject.get(key)
        if isinstance(val, str) and val:
            return val
        if isinstance(val, dict):
            return val.get("url", "")
    return ""


@lru_cache(maxsize=256)
def get_detail(detail_path: str) -> dict:
    resp = session.get(f"{API_BASE}/detail",
                       params={"detailPath": detail_path}, timeout=15)
    resp.raise_for_status()
    data    = resp.json()["data"]
    subject = data["subject"]
    seasons = [
        {
            "se":          s["se"],
            "max_ep":      s["maxEp"],
            "resolutions": [r["resolution"] for r in s.get("resolutions", [])],
        }
        for s in data.get("resource", {}).get("seasons", [])
    ]
    trailer = subject.get("trailer") or {}
    stills  = subject.get("stills") or {}
    return {
        "title":        subject["title"],
        "description":  subject.get("description", ""),
        "subject_id":   subject["subjectId"],
        "subject_type": subject.get("subjectType", ""),
        "cover":        _extract_cover(subject),
        "release_date": subject.get("releaseDate", ""),
        "country":      subject.get("countryName", ""),
        "genre":        subject.get("genre", ""),
        "imdb_rating":  subject.get("imdbRatingValue", ""),
        "imdb_votes":   subject.get("imdbRatingCount", 0),
        "subtitles":    subject.get("subtitles", ""),
        "trailer_url":  trailer.get("videoAddress", {}).get("url", ""),
        "backdrop":     stills.get("url", ""),
        "dubs":         [
            {
                "lang":        d["lanCode"],
                "lang_name":   d["lanName"],
                "subject_id":  d["subjectId"],
                "detail_path": d["detailPath"],
            }
            for d in subject.get("dubs", [])
        ],
        "seasons":      seasons,
    }


# ── Download options ──────────────────────────────────────────────────────────

def get_download_options(subject_id: str, detail_path: str,
                         se: int = 1, ep: int = 1) -> dict:
    resp = session.get(
        f"{API_BASE}/subject/download",
        params={"subjectId": subject_id, "detailPath": detail_path,
                "se": se, "ep": ep},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return {
        "downloads": [
            {
                "resolution": d["resolution"],
                "format":     d["format"],
                "size_mb":    round(int(d["size"]) / 1_048_576, 2),
                "url":        d["url"],
            }
            for d in data.get("downloads", [])
        ],
        "captions": [
            {
                "lang":      c["lan"],
                "lang_name": c["lanName"],
                "size_kb":   round(int(c["size"]) / 1024, 1),
                "url":       c["url"],
            }
            for c in data.get("captions", [])
        ],
    }


# ── Netnaija search ──────────────────────────────────────────────────────────

NN_BASE = "https://thenetnaija.ng"
NN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": NN_BASE + "/",
}

_nn_session = requests.Session()
_nn_session.headers.update(NN_HEADERS)


def netnaija_detail(url: str) -> dict:
    resp = _nn_session.get(url, timeout=15)
    resp.raise_for_status()
    html = resp.text

    title = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    cover = re.search(r'<meta property="og:image" content="([^"]+)"', html)

    content_start = html.find('class="entry-content"')
    content_end   = html.find('class="nav-links"', content_start)
    content_chunk = html[content_start:content_end] if content_start != -1 else html

    # Full description: strip all tags from content, split on double-newline/block boundaries
    # Remove script/style blocks first
    text_chunk = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', content_chunk, flags=re.DOTALL)
    # Remove download link anchors (keep their text but skip the actual <a> tags with external hrefs)
    text_chunk = re.sub(r'<a\s[^>]*href="https?://(?!thenetnaija)[^"]+"[^>]*>.*?</a>', '', text_chunk, flags=re.DOTALL)
    # Strip remaining tags
    plain = re.sub(r'<[^>]+>', ' ', text_chunk)
    # Collapse whitespace, split into sentences, filter noise
    sentences = [s.strip() for s in re.split(r'[\n\r]{2,}|(?<=\.)\s{2,}', plain) if len(s.strip()) > 20]
    description = ' '.join(dict.fromkeys(sentences))  # deduplicate while preserving order
    if not description:
        og = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        description = og.group(1) if og else ''

    links = []
    # Pattern 1: <a href="..."><b>LABEL</b></a>
    for m in re.finditer(r'<a\s[^>]*href="(https?://[^"]+)"[^>]*>\s*<b>([^<]+)</b>', content_chunk):
        links.append({"label": m.group(2).strip(), "url": m.group(1)})
    # Pattern 2: <b>LABEL</b> ... <a href="...">Download</a>
    if not links:
        for m in re.finditer(r'<b>([^<]{2,40})</b>[^<]*(?:<[^>]+>)*[^<]*<a\s[^>]*href="(https?://[^"]+)"', content_chunk):
            links.append({"label": m.group(1).strip(), "url": m.group(2)})
    # Pattern 3: any external download link with a text label
    if not links:
        for m in re.finditer(r'<a\s[^>]*href="(https?://(?!thenetnaija)[^"]+)"[^>]*>([^<]{2,60})</a>', content_chunk):
            label = re.sub(r'\s+', ' ', m.group(2)).strip()
            if label and not label.lower().startswith('<'):
                links.append({"label": label, "url": m.group(1)})

    seen_urls: set[str] = set()
    deduped = []
    for lnk in links:
        if lnk["url"] not in seen_urls:
            seen_urls.add(lnk["url"])
            deduped.append(lnk)

    return {
        "title":       title.group(1) if title else "",
        "cover":       cover.group(1) if cover else "",
        "description": description,
        "url":         url,
        "source":      "netnaija",
        "downloads":   deduped,
    }


def netnaija_search(query: str) -> list:
    resp = _nn_session.get(f"{NN_BASE}/", params={"s": query}, timeout=15)
    resp.raise_for_status()
    html = resp.text
    results = []
    for block in re.findall(r'class="magsoul-grid-post-inside">(.*?)</div>\s*</div>\s*</div>', html, re.DOTALL):
        url_m   = re.search(r'href="(https://thenetnaija\.ng/[^"]+)"', block)
        title_m = re.search(r'data-grid-post-title="([^"]+)"', block)
        cover_m = re.search(r'data-src="(https://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', block)
        if not cover_m:
            cover_m = re.search(r'(?<!data-)src="(https://(?!thenetnaija\.ng/wp-content/plugins)[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"', block)
        if url_m and title_m:
            results.append({
                "title":  title_m.group(1),
                "url":    url_m.group(1),
                "cover":  cover_m.group(1) if cover_m else "",
                "source": "netnaija",
            })
    seen, out = set(), []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out


# ── File download ─────────────────────────────────────────────────────────────

def download_file(url: str, dest_path: str) -> None:
    resp = session.get(url, stream=True, timeout=60, headers=DOWNLOAD_HEADERS)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    done  = 0
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done / total * 100
                bar = "#" * int(pct / 2)
                print(f"\r  [{bar:<50}] {pct:5.1f}%  {done//1024}KB/{total//1024}KB",
                      end="", flush=True)
    print()
