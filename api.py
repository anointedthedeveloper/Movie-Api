import os
import subprocess
import tempfile
import zipstream
from flask import Flask, jsonify, request, abort, Response, stream_with_context
from concurrent.futures import ThreadPoolExecutor, as_completed
from scraper import search, get_detail, get_download_options, session, DOWNLOAD_HEADERS, netnaija_search, netnaija_detail

app = Flask(__name__)

FFMPEG = "ffmpeg"


def show_name_from_path(detail_path: str) -> str:
    return "-".join(detail_path.split("-")[:-1]).replace("-", " ").title()


def fetch_to_temp(url: str, suffix: str) -> str:
    """Download a URL to a temp file, return the file path."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    resp = session.get(url, stream=True, timeout=120, headers=DOWNLOAD_HEADERS)
    resp.raise_for_status()
    for chunk in resp.iter_content(chunk_size=256 * 1024):
        tmp.write(chunk)
    tmp.close()
    return tmp.name


def mux_video_subs(video_url: str, sub_urls: list[dict], out_path: str):
    """
    Mux video + one or more subtitles into MP4.
    sub_urls: list of {url, lang} dicts.
    """
    vid_tmp  = fetch_to_temp(video_url, ".mp4")
    sub_tmps = [fetch_to_temp(s["url"], ".srt") for s in sub_urls]
    try:
        cmd = [FFMPEG, "-y", "-i", vid_tmp]
        for st in sub_tmps:
            cmd += ["-i", st]
        cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]
        for i, s in enumerate(sub_urls):
            cmd += [f"-metadata:s:s:{i}", f"language={s['lang']}"]
            cmd += [f"-disposition:s:{i}", "default" if i == 0 else "0"]
        cmd += ["-map", "0:v", "-map", "0:a"]
        for i in range(len(sub_tmps)):
            cmd += ["-map", f"{i+1}:0"]
        cmd.append(out_path)
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.unlink(vid_tmp)
        for st in sub_tmps:
            os.unlink(st)


def get_best_sub(captions: list, lang: str = "en") -> dict | None:
    """Pick requested lang, fall back to first available."""
    return (
        next((c for c in captions if c["lang"] == lang), None)
        or (captions[0] if captions else None)
    )


# ── AltSource proxy stream ───────────────────────────────────────────────────

@app.get("/altsource/proxy")
def api_altsource_proxy():
    """
    Proxy-stream any AltSource download URL so the frontend can do
    in-app downloads with progress.
    ?url=https://www.lulacloud.com/d/...
    """
    target = request.args.get("url", "").strip()
    if not target or not target.startswith("http"):
        abort(400, "Missing or invalid param: url")
    upstream = _nn_session.get(target, stream=True, timeout=60, headers={
        "User-Agent": NN_HEADERS["User-Agent"],
        "Referer": "https://thenetnaija.ng/",
    })
    upstream.raise_for_status()
    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    content_length = upstream.headers.get("Content-Length", "")
    filename = target.split("/")[-1].split("?")[0] or "download"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
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
    Each netnaija result has: title, url, source="netnaija".
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
        # Sort: exact title match first, then starts-with, then rest
        q_lower = q.lower().strip()
        def sort_key(item):
            t = item.get("title", "").lower()
            if t == q_lower:
                return 0
            if t.startswith(q_lower):
                return 1
            if q_lower in t:
                return 2
            return 3
        items.sort(key=sort_key)
        return items

    with ThreadPoolExecutor(max_workers=2) as ex:
        futures = {
            ex.submit(fetch_primary):       "primary",
            ex.submit(netnaija_search, q):  "netnaija",
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                out[key] = fut.result()
            except Exception as e:
                out["errors"][key] = str(e)

    return jsonify(out)


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
    return jsonify(get_download_options(subject_id, detail_path, se=se, ep=ep))


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


# ── Single episode stream ─────────────────────────────────────────────────────

@app.get("/stream")
def api_stream():
    """
    Stream a single episode with subtitles muxed in by default.
    &lang=en        → English only (default)
    &lang=all       → all available subtitle languages as tracks
    &lang=fr,es     → specific languages
    &lang=none      → no subtitles
    &type=caption&lang=en → subtitle file only
    """
    subject_id  = request.args.get("subjectId", "").strip()
    detail_path = request.args.get("detailPath", "").strip()
    se          = int(request.args.get("se", 1))
    ep          = int(request.args.get("ep", 1))
    lang        = request.args.get("lang", "en")
    if not subject_id or not detail_path:
        abort(400, "Missing params: subjectId, detailPath")

    opts  = get_download_options(subject_id, detail_path, se=se, ep=ep)
    show  = show_name_from_path(detail_path)
    kind  = request.args.get("type", "video")

    # ── Subtitle file only ──
    if kind == "caption":
        match = next((c for c in opts["captions"] if c["lang"] == lang), None)
        if not match:
            abort(404, f"No caption for lang: {lang}")
        upstream = session.get(match["url"], stream=True, timeout=60, headers=DOWNLOAD_HEADERS)
        upstream.raise_for_status()
        return Response(
            stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="Downloaderino_{show}_S{se}E{ep}_{lang}.srt"'},
        )

    # ── Video ──
    if not opts["downloads"]:
        abort(404, "No downloads available for this title")
    res   = int(request.args.get("resolution", opts["downloads"][0]["resolution"]))
    match = next((d for d in opts["downloads"] if d["resolution"] == res), None)
    if not match:
        abort(404, f"No download for resolution: {res}")

    # Resolve which subtitle tracks to embed
    subs_to_mux = []
    if opts["captions"] and lang != "none":
        if lang == "all":
            subs_to_mux = [{"url": c["url"], "lang": c["lang"]} for c in opts["captions"]]
        else:
            langs = [l.strip() for l in lang.split(",")]
            subs_to_mux = [{"url": c["url"], "lang": c["lang"]} for c in opts["captions"] if c["lang"] in langs]
            # fallback to english if none matched
            if not subs_to_mux:
                en = next((c for c in opts["captions"] if c["lang"] == "en"), None)
                if en:
                    subs_to_mux = [{"url": en["url"], "lang": "en"}]

    filename = f"Downloaderino_{show}_S{se}E{ep}_{res}P.mp4"

    if subs_to_mux:
        out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        out_tmp.close()
        mux_video_subs(match["url"], subs_to_mux, out_tmp.name)

        def stream_and_cleanup(path):
            try:
                with open(path, "rb") as f:
                    while chunk := f.read(256 * 1024):
                        yield chunk
            finally:
                os.unlink(path)

        return Response(
            stream_with_context(stream_and_cleanup(out_tmp.name)),
            mimetype="video/mp4",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(os.path.getsize(out_tmp.name)),
            },
        )

    # No subs available or lang=none — plain stream
    upstream = session.get(match["url"], stream=True, timeout=60, headers=DOWNLOAD_HEADERS)
    upstream.raise_for_status()
    return Response(
        stream_with_context(upstream.iter_content(chunk_size=256 * 1024)),
        mimetype="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": upstream.headers.get("Content-Length", ""),
        },
    )


# ── Season bulk stream ────────────────────────────────────────────────────────

@app.get("/stream/season")
def api_stream_season():
    subject_id  = request.args.get("subjectId", "").strip()
    detail_path = request.args.get("detailPath", "").strip()
    se          = int(request.args.get("se", 1))
    res         = int(request.args.get("resolution", 360))
    fmt         = request.args.get("format", "folder")
    lang        = request.args.get("lang", "en")
    if not subject_id or not detail_path:
        abort(400, "Missing params: subjectId, detailPath")

    detail  = get_detail(detail_path)
    season  = next((s for s in detail["seasons"] if s["se"] == se), None)
    if not season:
        abort(404, f"Season {se} not found")
    max_ep  = season["max_ep"]

    ep_from = int(request.args.get("epFrom", 1))
    ep_to   = int(request.args.get("epTo", max_ep))
    if ep_from < 1 or ep_to > max_ep or ep_from > ep_to:
        abort(400, f"Invalid epFrom/epTo range (season has {max_ep} episodes)")

    show        = show_name_from_path(detail_path)
    folder_name = f"Downloaderino_{show}_S{se}E{ep_from}-E{ep_to}_{res}P"

    def episode_chunks(ep_num):
        opts  = get_download_options(subject_id, detail_path, se=se, ep=ep_num)
        match = next((d for d in opts["downloads"] if d["resolution"] == res),
                     opts["downloads"][0] if opts["downloads"] else None)
        if not match:
            return

        subs_to_mux = []
        if opts["captions"] and lang != "none":
            if lang == "all":
                subs_to_mux = [{"url": c["url"], "lang": c["lang"]} for c in opts["captions"]]
            else:
                langs = [l.strip() for l in lang.split(",")]
                subs_to_mux = [{"url": c["url"], "lang": c["lang"]} for c in opts["captions"] if c["lang"] in langs]
                if not subs_to_mux:
                    en = next((c for c in opts["captions"] if c["lang"] == "en"), None)
                    if en:
                        subs_to_mux = [{"url": en["url"], "lang": "en"}]

        if subs_to_mux:
            out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
            out_tmp.close()
            mux_video_subs(match["url"], subs_to_mux, out_tmp.name)
            try:
                with open(out_tmp.name, "rb") as f:
                    while chunk := f.read(256 * 1024):
                        yield chunk
            finally:
                os.unlink(out_tmp.name)
        else:
            upstream = session.get(match["url"], stream=True, timeout=120, headers=DOWNLOAD_HEADERS)
            upstream.raise_for_status()
            yield from upstream.iter_content(chunk_size=256 * 1024)

    zs = zipstream.ZipStream(compress_type=zipstream.ZIP_STORED)
    for ep_num in range(ep_from, ep_to + 1):
        fname   = f"Downloaderino_{show}_S{se}E{ep_num}_{res}P.mp4"
        arcname = f"{folder_name}/{fname}" if fmt == "folder" else fname
        zs.add(episode_chunks(ep_num), arcname)

    zip_filename = f"{folder_name}.zip"
    return Response(
        stream_with_context(zs),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(400)
def bad_request(e):
    return jsonify(error=str(e)), 400

@app.errorhandler(500)
def server_error(e):
    import traceback
    return jsonify(error=str(e), traceback=traceback.format_exc()), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000, use_reloader=False, threaded=True)
