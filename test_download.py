import requests, time

BASE = "https://movie-api-nine-chi.vercel.app"

# Get links
r = requests.get(BASE + "/links", params={
    "subjectId": "2190807691784770592",
    "detailPath": "lucifer-UQASHYbVPB2",
    "se": 1, "ep": 1
}, timeout=30)
data = r.json()
dl = data["downloads"][0]  # 360P - smallest
url = dl["url"]
hdrs = dl["headers"]
size_mb = dl["size_mb"]

print(f"Downloading Lucifer S1E1 360P ({size_mb} MB)...")
print(f"URL: {url[:80]}...")

start = time.time()
resp = requests.get(url, headers=hdrs, stream=True, timeout=120)
resp.raise_for_status()

total_bytes = int(resp.headers.get("Content-Length", 0))
done = 0
out_path = "C:/Users/Admin/Desktop/Movie-Api/Lucifer_S1E1_360P.mp4"

with open(out_path, "wb") as f:
    for chunk in resp.iter_content(chunk_size=256 * 1024):
        f.write(chunk)
        done += len(chunk)
        if total_bytes:
            pct = done / total_bytes * 100
            bar = "█" * int(pct / 2)
            print(f"\r  [{bar:<50}] {pct:5.1f}%  {done//1024//1024}MB/{total_bytes//1024//1024}MB", end="", flush=True)

elapsed = time.time() - start
speed = (done / 1024 / 1024) / elapsed
print(f"\n\nDone! {done//1024//1024}MB in {elapsed:.1f}s ({speed:.1f} MB/s)")
print(f"Saved: {out_path}")
