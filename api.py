import os
from flask import Flask, jsonify, request, abort, Response, stream_with_context
from concurrent.futures import ThreadPoolExecutor, as_completed
from scraper import (
    search, get_featured, get_detail, get_download_options,
    download_session, DOWNLOAD_HEADERS,
    netnaija_search, netnaija_detail, _nn_session, NN_HEADERS,
)

app = Flask(__name__)


# ── Netnaija / AltSource proxy ───────────────────────────────────────────────

def _nn_proxy_stream(target: str):
    """Proxy-stream a Netnaija/AltSource URL."""
    upstream = _nn_session.get(
        target, stream=True, timeout=120, allow_redirects=True,
        headers={"User-Agent": NN_HEADERS["User-Agent"], "Referer": "https://thenetnaija.ng/"},
    )
    upstream.raise_for_status()
    content_type   = upstream.headers.get("Content-Type", "application/octet-stream")
    content_length = upstream.headers.get("Content-Length", "")
    final_url      = upstream.url
    filename       = final_url.split("/")[-1].split("?")[0] or "download"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Download-Source":   "netnaija",
    }
    if content_length:
        headers["Content-Length"] = content_length
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
        mimetype=content_type,
        headers=headers,
    )


@app.get("/altsource/proxy")
def api_altsource_proxy():
    """
    Proxy-stream any AltSource download URL.
    ?url=https://www.lulacloud.com/d/...
    """
    target = request.args.get("url", "").strip()
    if not target or not target.startswith("http"):
        abort(400, "Missing or invalid param: url")
    return _nn_proxy_stream(target)


@app.get("/netnaija/download")
def api_netnaija_download():
    """
    In-app download for a Netnaija direct link.
    ?url=https://meetdownload.com/...
    &filename=Gen-V-S01E07.mkv   (optional override)
    """
    target   = request.args.get("url", "").strip()
    override = request.args.get("filename", "").strip()
    if not target or not target.startswith("http"):
        abort(400, "Missing or invalid param: url")
    upstream = _nn_session.get(
        target, stream=True, timeout=120, allow_redirects=True,
        headers={"User-Agent": NN_HEADERS["User-Agent"], "Referer": "https://thenetnaija.ng/"},
    )
    upstream.raise_for_status()
    content_type   = upstream.headers.get("Content-Type", "application/octet-stream")
    content_length = upstream.headers.get("Content-Length", "")
    final_url      = upstream.url
    filename       = override or final_url.split("/")[-1].split("?")[0] or "download"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Download-Source":   "netnaija",
        "Access-Control-Expose-Headers": "Content-Length, Content-Disposition",
    }
    if content_length:
        headers["Content-Length"] = content_length
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
        mimetype=content_type,
        headers=headers,
    )


# ── Netnaija detail ───────────────────────────────────────────────────────────

@app.get("/netnaija/detail")
def api_netnaija_detail():
    """
    Scrape detail + download links from a Netnaija post URL.
    ?url=https://thenetnaija.ng/gen-v-2023-tv-series-download/
    """
    url = request.args.get("url", "").strip()
    if not url or "thenetnaija.ng" not in url:
        abort(400, "Missing or invalid param: url")
    return jsonify(netnaija_detail(url))


# ── Unified search (all sources) ─────────────────────────────────────────────

@app.get("/search/all")
def api_search_all():
    """
    Search all sources concurrently.
    Returns {primary: [...], netnaija: [...], errors: {}}.
    """
    q    = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    if not q:
        abort(400, "Missing param: q")

    out = {"primary": [], "netnaija": [], "errors": {}}

    def fetch_primary():
        data  = search(q, page)
        items = data if isinstance(data, list) else data.get("list", data.get("items", []))
        items = [{**item, "source": "primary"} for item in (items or [])]
        q_lower = q.lower().strip()
        def sort_key(item):
            t = item.get("title", "").lower()
            if t == q_lower:        return 0
            if t.startswith(q_lower): return 1
            if q_lower in t:         return 2
            return 3
        items.sort(key=sort_key)
        return items

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {
            ex.submit(fetch_primary):      "primary",
            ex.submit(netnaija_search, q): "netnaija",
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                out[key] = fut.result()
            except Exception as e:
                out["errors"][key] = str(e)

    return jsonify(out)


# ── Featured / Home ──────────────────────────────────────────────────────────

@app.get("/featured")
def api_featured():
    page      = int(request.args.get("page", 1))
    page_size = int(request.args.get("pageSize", 18))
    tab_id    = request.args.get("tabId", "").strip()
    return jsonify(get_featured(page, page_size, tab_id))


# ── Search ────────────────────────────────────────────────────────────────────

@app.get("/search")
def api_search():
    q    = request.args.get("q", "").strip()
    page = int(request.args.get("page", 1))
    if not q:
        abort(400, "Missing param: q")
    return jsonify(search(q, page))


# ── Detail ────────────────────────────────────────────────────────────────────

@app.get("/detail")
def api_detail():
    detail_path = request.args.get("detailPath", "").strip()
    if not detail_path:
        abort(400, "Missing param: detailPath")
    return jsonify(get_detail(detail_path))


# ── Raw links ─────────────────────────────────────────────────────────────────

@app.get("/links")
def api_links():
    subject_id  = request.args.get("subjectId", "").strip()
    detail_path = request.args.get("detailPath", "").strip()
    se          = int(request.args.get("se", 1))
    ep          = int(request.args.get("ep", 1))
    if not subject_id or not detail_path:
        abort(400, "Missing params: subjectId, detailPath")
    opts = get_download_options(subject_id, detail_path, se=se, ep=ep)

    # Tell the frontend which headers it needs to pass when fetching CDN URLs
    # (CDN blocks datacenter IPs like Vercel/AWS but allows browser requests
    #  with the correct Origin/Referer)
    cdn_headers = {
        "Origin":  "https://downloader2.com",
        "Referer": "https://downloader2.com/",
    }
    for d in opts["downloads"]:
        d["headers"] = cdn_headers
    for c in opts["captions"]:
        c["headers"] = cdn_headers

    return jsonify(opts)


@app.get("/links/season")
def api_links_season():
    subject_id  = request.args.get("subjectId", "").strip()
    detail_path = request.args.get("detailPath", "").strip()
    se          = int(request.args.get("se", 1))
    if not subject_id or not detail_path:
        abort(400, "Missing params: subjectId, detailPath")
    detail = get_detail(detail_path)
    season = next((s for s in detail["seasons"] if s["se"] == se), None)
    if not season:
        abort(404, f"Season {se} not found")
    return jsonify([
        {"ep": ep, **get_download_options(subject_id, detail_path, se=se, ep=ep)}
        for ep in range(1, season["max_ep"] + 1)
    ])


# ── Bulk season download links ───────────────────────────────────────────────

@app.get("/stream/season")
def api_stream_season():
    """
    Returns all episode stream URLs for a season (no proxying — client downloads directly).
    ?subjectId=...&detailPath=...&se=1&resolution=720
    """
    subject_id  = request.args.get("subjectId", "").strip()
    detail_path = request.args.get("detailPath", "").strip()
    se          = int(request.args.get("se", 1))
    resolution  = request.args.get("resolution", "").strip()
    if not subject_id or not detail_path:
        abort(400, "Missing params: subjectId, detailPath")

    detail = get_detail(detail_path)
    season = next((s for s in detail["seasons"] if s["se"] == se), None)
    if not season:
        abort(404, f"Season {se} not found")

    cdn_headers = {
        "Origin":  "https://downloader2.com",
        "Referer": "https://downloader2.com/",
    }

    episodes = []
    for ep_num in range(1, season["max_ep"] + 1):
        opts = get_download_options(subject_id, detail_path, se=se, ep=ep_num)
        if not opts["downloads"]:
            continue
        if resolution:
            vid = next((d for d in opts["downloads"] if str(d["resolution"]) == resolution), opts["downloads"][0])
        else:
            vid = opts["downloads"][0]
        vid["headers"] = cdn_headers
        episodes.append({
            "ep":       ep_num,
            "video":    vid,
            "captions": [{**c, "headers": cdn_headers} for c in opts["captions"]],
        })

    return jsonify({"season": se, "episodes": episodes})


# ── Single episode stream (fetches fresh URL + proxies server-side) ──────────

@app.get("/stream")
def api_stream():
    """
    Fetches a fresh CDN URL and proxies the bytes through the server.
    Fresh URL = fresh signature = same IP that signed it does the fetch.
    &type=caption&lang=en  → subtitle file only
    &resolution=720        → pick resolution (default: first available)
    """
    subject_id  = request.args.get("subjectId", "").strip()
    detail_path = request.args.get("detailPath", "").strip()
    se          = int(request.args.get("se", 1))
    ep          = int(request.args.get("ep", 1))
    lang        = request.args.get("lang", "en")
    if not subject_id or not detail_path:
        abort(400, "Missing params: subjectId, detailPath")

    # Always fetch fresh options so the signed URL is brand new
    opts = get_download_options(subject_id, detail_path, se=se, ep=ep)
    kind = request.args.get("type", "video")

    # Subtitle file only
    if kind == "caption":
        match = next((c for c in opts["captions"] if c["lang"] == lang), None)
        if not match:
            abort(404, f"No caption for lang: {lang}")
        upstream = download_session.get(
            match["url"], stream=True, timeout=60,
            allow_redirects=True, headers=DOWNLOAD_HEADERS,
        )
        upstream.raise_for_status()
        return Response(
            stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="sub_S{se}E{ep}_{lang}.srt"'},
        )

    # Video — fetch fresh URL and proxy immediately (same function = same IP)
    if not opts["downloads"]:
        abort(404, "No downloads available for this title")
    res   = int(request.args.get("resolution", opts["downloads"][0]["resolution"]))
    match = next((d for d in opts["downloads"] if d["resolution"] == res), None)
    if not match:
        abort(404, f"No download for resolution: {res}")

    cdn_headers = {**DOWNLOAD_HEADERS, "Cache-Control": "no-cache", "Pragma": "no-cache"}
    upstream = download_session.get(
        match["url"], stream=True, timeout=60,
        allow_redirects=True, headers=cdn_headers,
    )
    upstream.raise_for_status()

    content_length = upstream.headers.get("Content-Length", "")
    resp_headers = {
        "Content-Disposition": f'attachment; filename="S{se}E{ep}_{res}P.mp4"',
        "Content-Type": "video/mp4",
        "Cache-Control": "no-store",
        "Access-Control-Expose-Headers": "Content-Length, Content-Disposition",
    }
    if content_length:
        resp_headers["Content-Length"] = content_length

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
        mimetype="video/mp4",
        headers=resp_headers,
    )


# ── Generic CDN proxy ────────────────────────────────────────────────────────

@app.get("/proxy")
def api_proxy():
    """
    Proxy any CDN video/subtitle URL through the server.
    Solves IP-locked signed URL rejections when the browser hits CDN directly.
    ?url=https://bcdnxw.hakunaymatata.com/...
    &filename=episode.mp4  (optional)
    """
    target   = request.args.get("url", "").strip()
    filename = request.args.get("filename", "").strip()
    if not target or not target.startswith("http"):
        abort(400, "Missing or invalid param: url")

    upstream = download_session.get(
        target, stream=True, timeout=60,
        allow_redirects=True, headers=DOWNLOAD_HEADERS,
    )
    upstream.raise_for_status()

    content_type   = upstream.headers.get("Content-Type", "application/octet-stream")
    content_length = upstream.headers.get("Content-Length", "")
    final_filename = filename or target.split("/")[-1].split("?")[0] or "download"

    resp_headers = {
        "Content-Disposition": f'attachment; filename="{final_filename}"',
        "Access-Control-Expose-Headers": "Content-Length, Content-Disposition",
    }
    if content_length:
        resp_headers["Content-Length"] = content_length

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
        mimetype=content_type,
        headers=resp_headers,
    )




@app.get("/debug/url")
def api_debug_url():
    """Check what status code a CDN URL returns from this server's IP."""
    url = request.args.get("url", "").strip()
    if not url or not url.startswith("http"):
        abort(400, "Missing or invalid param: url")
    results = {}
    for label, hdrs in [
        ("full",      DOWNLOAD_HEADERS),
        ("no_origin", {k: v for k, v in DOWNLOAD_HEADERS.items() if k != "Origin"}),
        ("ua_only",   {"User-Agent": DOWNLOAD_HEADERS["User-Agent"]}),
    ]:
        try:
            r = download_session.head(url, timeout=15, allow_redirects=True, headers=hdrs)
            results[label] = {"status": r.status_code, "final_url": r.url}
        except Exception as e:
            results[label] = {"error": str(e)}
    return jsonify(results)


@app.get("/debug/search")
def api_debug_search():
    """
    Raw probe of the upstream search API — returns status, headers, and body
    so we can diagnose exactly why search is failing.
    ?q=lucifer
    """
    from scraper import API_BASE, session as api_session
    q = request.args.get("q", "test").strip()
    try:
        resp = api_session.post(
            f"{API_BASE}/subject/search",
            json={"keyword": q, "page": 1, "pageSize": 5},
            timeout=15,
        )
        return jsonify({
            "status":   resp.status_code,
            "body":     resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:2000],
            "headers":  dict(resp.headers),
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(400)
def bad_request(e):
    return jsonify(error=str(e)), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify(error=str(e)), 404

@app.errorhandler(500)
def server_error(e):
    import traceback
    return jsonify(error=str(e), traceback=traceback.format_exc()), 500
