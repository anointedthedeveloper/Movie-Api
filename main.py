import sys
import os
import re
from scraper import search, get_detail, get_download_options, download_file


def pick(items: list, label: str):
    if not items:
        print(f"  No {label} available.")
        return None
    for i, item in enumerate(items):
        if label == "quality":
            print(f"  [{i}] {item['resolution']}P {item['format']}  —  {item['size_mb']} MB")
        elif label == "subtitle":
            print(f"  [{i}] {item['lang_name']} ({item['lang']})  —  {item['size_kb']} KB")
        elif label == "episode":
            print(f"  [{i}] Episode {item}")
        else:
            print(f"  [{i}] {item}")
    idx = input(f"Pick {label} (Enter to skip): ").strip()
    return items[int(idx)] if idx.isdigit() and int(idx) < len(items) else None


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def fetch_and_maybe_download(subject_id, detail_path, se, ep, title, out_dir):
    print(f"\nFetching links (S{se}E{ep})...")
    options = get_download_options(subject_id, detail_path, se=se, ep=ep)

    if not options["downloads"]:
        print("  No download options returned.")
        return

    print("\nVideo qualities:")
    quality = pick(options["downloads"], "quality")

    print("\nSubtitles:")
    subtitle = pick(options["captions"], "subtitle")

    # Show links first
    if quality:
        print(f"\n  Video URL  : {quality['url']}")
    if subtitle:
        print(f"  Subtitle URL: {subtitle['url']}")

    if not quality and not subtitle:
        print("\nNothing selected.")
        return

    confirm = input("\nDownload? (y/n): ").strip().lower()
    while confirm not in ("y", "n"):
        confirm = input("  Enter 'y' to download or 'n' to skip: ").strip().lower()
    if confirm == "n":
        print("Skipped download.")
        return

    base_name = safe_filename(f"{title}-S{se}E{ep}")
    os.makedirs(out_dir, exist_ok=True)

    if quality:
        filename = os.path.join(out_dir, f"{base_name}-{quality['resolution']}P.mp4")
        print(f"\nDownloading video → {filename}")
        download_file(quality["url"], filename)
        print(f"  Saved: {filename}")

    if subtitle:
        filename = os.path.join(out_dir, f"{base_name}-{subtitle['lang']}.srt")
        print(f"\nDownloading subtitle → {filename}")
        download_file(subtitle["url"], filename)
        print(f"  Saved: {filename}")


def main():
    query = " ".join(sys.argv[1:]) or input("Search query: ").strip()
    if not query:
        sys.exit("No query provided.")

    # ── Step 1: Search ────────────────────────────────────────────────────────
    print(f"\nSearching: {query}")
    results = search(query)
    if not results:
        sys.exit("No results found.")

    for i, r in enumerate(results):
        genres = ", ".join(r["genres"]) if r["genres"] else "—"
        print(f"  [{i}] {r['title']} ({r['date']}) — {r['type']} — {genres}")

    choice = input("\nPick result: ").strip()
    if not choice.isdigit() or int(choice) >= len(results):
        sys.exit("Invalid choice.")
    item = results[int(choice)]

    # ── Step 2: Detail ────────────────────────────────────────────────────────
    print(f"\nFetching detail for: {item['title']}")
    detail = get_detail(item["detail_path"])

    subject_id  = detail["subject_id"]
    detail_path = item["detail_path"]
    out_dir     = os.path.join(os.getcwd(), "downloads", safe_filename(detail["title"]))

    # Print metadata (useful for frontend)
    print(f"  Title      : {detail['title']}")
    if detail["release_date"]: print(f"  Released   : {detail['release_date']}")
    if detail["imdb_rating"]:  print(f"  IMDB       : {detail['imdb_rating']} ({detail['imdb_votes']} votes)")
    if detail["country"]:      print(f"  Country    : {detail['country']}")
    if detail["cover"]:        print(f"  Cover      : {detail['cover']}")
    if detail["description"]:  print(f"  Description: {detail['description'][:120]}...")

    # ── Step 3: Season / episode selection ───────────────────────────────────
    se, ep = 1, 1
    season = None

    # Filter out season 0 with 0 episodes
    valid_seasons = [s for s in detail["seasons"] if s["max_ep"] > 0]

    if valid_seasons:
        if len(valid_seasons) > 1:
            for i, s in enumerate(valid_seasons):
                print(f"  [{i}] Season {s['se']}  ({s['max_ep']} episodes)")
            si = input("Pick season (Enter for first): ").strip()
            season = valid_seasons[int(si)] if si.isdigit() and int(si) < len(valid_seasons) else valid_seasons[0]
        else:
            season = valid_seasons[0]
        se = season["se"]

        # Offer: single episode or entire season
        print(f"\nSeason {se} — {season['max_ep']} episodes")
        mode = input("Download [a]ll episodes or pick [e]pisode? (a/e): ").strip().lower()
        while mode not in ("a", "e"):
            mode = input("  Enter 'a' for all or 'e' for single episode: ").strip().lower()

        if mode == "a":
            # Ask quality/subtitle once, apply to all episodes
            print("\nFetching links for Episode 1 to pick quality/subtitle...")
            opts0 = get_download_options(subject_id, detail_path, se=se, ep=1)

            if not opts0["downloads"]:
                sys.exit("No download options returned.")

            print("\nVideo qualities:")
            quality = pick(opts0["downloads"], "quality")
            print("\nSubtitles:")
            subtitle = pick(opts0["captions"], "subtitle")

            if not quality and not subtitle:
                sys.exit("Nothing selected.")

            if quality:  print(f"\n  Sample video URL : {opts0['downloads'][opts0['downloads'].index(quality)]['url']}")
            if subtitle: print(f"  Sample subtitle URL: {subtitle['url']}")

            confirm = input(f"\nDownload all {season['max_ep']} episodes at {quality['resolution']}P? (y/n): ").strip().lower()
            if confirm != "y":
                sys.exit("Aborted.")

            os.makedirs(out_dir, exist_ok=True)
            for ep_num in range(1, season["max_ep"] + 1):
                ep_opts = get_download_options(subject_id, detail_path, se=se, ep=ep_num)
                base    = safe_filename(f"{detail['title']}-S{se}E{ep_num}")

                # Match same resolution
                vid = next((d for d in ep_opts["downloads"] if d["resolution"] == quality["resolution"]), None)
                if vid:
                    fname = os.path.join(out_dir, f"{base}-{vid['resolution']}P.mp4")
                    print(f"\nEpisode {ep_num} video → {fname}")
                    download_file(vid["url"], fname)
                    print(f"  Saved: {fname}")

                if subtitle:
                    sub = next((c for c in ep_opts["captions"] if c["lang"] == subtitle["lang"]), None)
                    if sub:
                        fname = os.path.join(out_dir, f"{base}-{sub['lang']}.srt")
                        print(f"Episode {ep_num} subtitle → {fname}")
                        download_file(sub["url"], fname)
                        print(f"  Saved: {fname}")
            return

        else:
            episodes = list(range(1, season["max_ep"] + 1))
            if season["max_ep"] > 1:
                ep_pick = pick(episodes, "episode")
                ep = ep_pick if ep_pick else 1
            print(f"\nSelected: Season {se}, Episode {ep}")

    # ── Step 4: Single episode ────────────────────────────────────────────────
    fetch_and_maybe_download(subject_id, detail_path, se, ep, detail["title"], out_dir)


if __name__ == "__main__":
    main()
