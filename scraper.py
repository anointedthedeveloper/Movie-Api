import queue
import time
import threading
import requests
from functools import lru_cache
from playwright.sync_api import sync_playwright

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

# ── Persistent browser worker (runs in its own thread) ───────────────────────
# Playwright's sync API is greenlet-based and must stay on one thread.
# We send search jobs via a queue and get results back via per-job Events.

_job_queue: queue.Queue = queue.Queue()


def _browser_worker():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        while True:
            job = _job_queue.get()
            if job is None:          # shutdown signal
                break
            query, result_box, event = job
            try:
                page        = browser.new_page()
                api_event   = threading.Event()
                intercepted = []

                def handle_response(response):
                    if "h5-api.aoneroom.com" in response.url and response.status == 200:
                        try:
                            intercepted.append(response.json())
                            api_event.set()
                        except Exception:
                            pass

                page.on("response", handle_response)
                page.goto(f"{BASE_SITE}/?q={query}", wait_until="domcontentloaded", timeout=30000)
                api_event.wait(timeout=10)
                page.close()

                results = []
                for body in intercepted:
                    items = []
                    if isinstance(body, dict):
                        for v in body.values():
                            if isinstance(v, list):
                                items = v
                                break
                            if isinstance(v, dict):
                                for vv in v.values():
                                    if isinstance(vv, list):
                                        items = vv
                                        break
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        dp  = item.get("detailPath", "")
                        sid = item.get("subjectId", "")
                        title = item.get("title", "")
                        if not (dp and sid and title):
                            continue
                        results.append({
                            "title":       title,
                            "date":        item.get("releaseDate", "N/A"),
                            "type":        item.get("subjectType", "N/A"),
                            "genres":      item.get("genres", []),
                            "detail_path": dp,
                            "subject_id":  str(sid),
                        })

                seen = set()
                deduped = []
                for r in results:
                    if r["detail_path"] not in seen:
                        seen.add(r["detail_path"])
                        deduped.append(r)

                result_box.append(deduped)
            except Exception as e:
                result_box.append(e)
            finally:
                event.set()
        browser.close()


_worker_thread = threading.Thread(target=_browser_worker, daemon=True)
_worker_thread.start()


# ── Search ────────────────────────────────────────────────────────────────────

_search_cache: dict[str, tuple[list, float]] = {}
_SEARCH_TTL = 300  # 5 minutes

def search(query: str) -> list[dict]:
    key = query.lower().strip()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[1] < _SEARCH_TTL:
        return cached[0]
    result_box: list = []
    event = threading.Event()
    _job_queue.put((query, result_box, event))
    event.wait(timeout=60)
    result = result_box[0] if result_box else []
    if isinstance(result, Exception):
        raise result
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
