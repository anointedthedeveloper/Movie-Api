import requests

BASE = "https://movie-api-nine-chi.vercel.app"

# Use /debug/url to test the CDN URL FROM Vercel's own IP
print("Getting fresh CDN URL via /links...")
r = requests.get(BASE + "/links", params={
    "subjectId": "2190807691784770592",
    "detailPath": "lucifer-UQASHYbVPB2",
    "se": 1, "ep": 1
}, timeout=30)
url = r.json()["downloads"][0]["url"]
print(f"URL: {url[:90]}...")

print("\nTesting that URL FROM Vercel's IP via /debug/url...")
r2 = requests.get(BASE + "/debug/url", params={"url": url}, timeout=30)
print(r2.json())
