import requests
import json

session = requests.Session()
session.headers.update({
    "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Accept":         "application/json",
    "Origin":         "https://downloader2.com",
    "Referer":        "https://downloader2.com/",
    "x-request-lang": "en",
    "x-client-info":  '{"timezone":"Africa/Lagos"}',
})

BUILD_ID = "jHj9bKQRqUTW0VGHIKnAb"
BASE_API = "https://h5-api.aoneroom.com/wefeed-h5api-bff"

candidates = [
    # Next.js static data route
    f"https://downloader2.com/_next/data/{BUILD_ID}/en.json?q=nesting",
    f"https://downloader2.com/_next/data/{BUILD_ID}/en/index.json?q=nesting",
    # API variations
    f"{BASE_API}/subject/search?keyword=nesting&page=1&pageSize=20",
    f"{BASE_API}/subject?keyword=nesting",
    f"{BASE_API}/subjects?keyword=nesting",
    f"{BASE_API}/feed?keyword=nesting",
    f"{BASE_API}/home/search?keyword=nesting",
    f"{BASE_API}/search/subject?keyword=nesting",
    f"{BASE_API}/v1/search?keyword=nesting",
    f"{BASE_API}/subject/recommend?keyword=nesting",
]

for url in candidates:
    try:
        r = session.get(url, timeout=10)
        print(f"[{r.status_code}] {url}")
        if r.status_code == 200:
            try:
                d = r.json()
                print(f"  KEYS: {list(d.keys())}")
                print(f"  SNIPPET: {json.dumps(d)[:400]}")
            except Exception:
                print(f"  (not JSON) {r.text[:200]}")
    except Exception as e:
        print(f"[ERR] {url} — {e}")
