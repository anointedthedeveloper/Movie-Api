import requests

BASE = "https://movie-api-nine-chi.vercel.app"

# Get fresh links
r = requests.get(BASE + "/links", params={
    "subjectId": "2190807691784770592",
    "detailPath": "lucifer-UQASHYbVPB2",
    "se": 1, "ep": 1
}, timeout=30)
opts = r.json()

for d in opts["downloads"]:
    url = d["url"]
    # Try with NO special headers - exactly like a browser would
    resp = requests.head(url, timeout=15, allow_redirects=True)
    print(f"{d['resolution']}P -> status={resp.status_code} size={resp.headers.get('Content-Length','?')} type={resp.headers.get('Content-Type','?')}")
