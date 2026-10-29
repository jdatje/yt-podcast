#!/usr/bin/env python3
"""yt-podcast: turn YouTube playlists into an audio podcast feed.

Usage:
  ytpodcast.py run   [--config config.toml]   download new episodes + rebuild feeds
  ytpodcast.py list  [--config config.toml]   show what would be in each feed (no downloads)
  ytpodcast.py feed  [--config config.toml]   rebuild the feed XML only
"""
from __future__ import annotations

import argparse
import email.utils
import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST = "https://podcastindex.org/namespace/1.0"
ET.register_namespace("itunes", ITUNES)
ET.register_namespace("podcast", PODCAST)

DEFAULT_SPONSORBLOCK = ["sponsor", "selfpromo", "interaction"]


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- config

def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    for key in ("base_url", "public_dir", "feeds"):
        if key not in cfg:
            sys.exit(f"config: missing '{key}'")
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    cfg["public_dir"] = Path(cfg["public_dir"])
    cfg["state_dir"] = Path(cfg.get("state_dir", cfg["public_dir"].parent / "state"))
    cfg.setdefault("yt_dlp", "yt-dlp")
    cfg.setdefault("max_downloads_per_run", 5)
    return cfg


# ---------------------------------------------------------------- state

def state_path(cfg: dict, feed: dict) -> Path:
    return cfg["state_dir"] / f"{feed['slug']}.json"


def load_state(cfg: dict, feed: dict) -> dict:
    p = state_path(cfg, feed)
    if p.exists():
        return json.loads(p.read_text())
    return {"episodes": {}}


def save_state(cfg: dict, feed: dict, state: dict) -> None:
    p = state_path(cfg, feed)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(p)


# ---------------------------------------------------------------- youtube

def yt(cfg: dict, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [cfg["yt_dlp"], "--no-warnings", "--ignore-config", *cfg.get("yt_dlp_args", []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def list_playlist(cfg: dict, url: str) -> list[dict]:
    res = yt(cfg, "--flat-playlist", "--dump-single-json", url)
    data = json.loads(res.stdout)
    out = []
    for i, e in enumerate(data.get("entries") or [], start=1):
        if not e or not e.get("id"):
            continue
        title = e.get("title") or ""
        if title in ("[Private video]", "[Deleted video]"):
            continue
        if e.get("live_status") in ("is_upcoming", "is_live"):
            continue  # premiere not out yet; picked up on a later run
        out.append({"id": e["id"], "title": title, "index": i})
    return out


def first_match(rules: list[dict], title: str) -> dict | None:
    for r in rules:
        if re.search(r["match"], title, re.IGNORECASE):
            return r
    return None


def plan_feed(cfg: dict, feed: dict) -> list[dict]:
    """Return the ordered list of episodes the feed should contain."""
    exclude = [re.compile(x, re.IGNORECASE) for x in feed.get("exclude", [])]
    ep_re = re.compile(feed.get("episode_regex", r"Episode\s*(\d+)"), re.IGNORECASE)
    seen: set[str] = set()
    planned: list[dict] = []
    for pl in feed["playlists"]:
        for e in list_playlist(cfg, pl["url"]):
            if e["id"] in seen or any(x.search(e["title"]) for x in exclude):
                continue
            rule = first_match(pl.get("seasons", []), e["title"])
            season = rule["season"] if rule else pl.get("season")
            if season is None:
                log(f"  skip (no season rule matches): {e['title']}")
                continue  # not marked seen: a later playlist may still claim it
            seen.add(e["id"])
            m = ep_re.search(e["title"])
            planned.append({**e, "season": int(season), "title_episode": int(m.group(1)) if m else None})
    # Episode numbers: from the titles when every episode of a season has a unique one,
    # otherwise playlist order within the season (avoids duplicate numbers).
    by_season: dict[int, list[dict]] = {}
    for p in planned:
        by_season.setdefault(p["season"], []).append(p)
    for eps in by_season.values():
        nums = [p["title_episode"] for p in eps]
        use_titles = None not in nums and len(set(nums)) == len(nums)
        for i, p in enumerate(eps, start=1):
            p["episode"] = p["title_episode"] if use_titles else i
    return planned


def probe_duration(path: Path) -> int:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(float(res.stdout.strip()))
    except ValueError:
        return 0


def download(cfg: dict, feed: dict, vid: str) -> dict:
    media = cfg["public_dir"] / feed["slug"]
    media.mkdir(parents=True, exist_ok=True)
    args = [
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "--no-playlist", "--retries", "5",
        "--write-info-json", "--write-thumbnail", "--convert-thumbnails", "jpg",
        "-o", str(media / f"{vid}.%(ext)s"),
        "-o", f"thumbnail:{media / vid}.%(ext)s",
        "-o", f"infojson:{cfg['state_dir'] / 'info' / vid}.%(ext)s",
    ]
    cats = feed.get("sponsorblock_remove", DEFAULT_SPONSORBLOCK)
    if cats:
        args += ["--sponsorblock-remove", ",".join(cats)]
    yt(cfg, *args, f"https://www.youtube.com/watch?v={vid}")

    audio = next((p for p in media.glob(f"{vid}.*") if p.suffix not in (".jpg", ".json", ".part")), None)
    if not audio:
        raise RuntimeError("download finished but no audio file found")
    info_file = cfg["state_dir"] / "info" / f"{vid}.info.json"
    info = json.loads(info_file.read_text()) if info_file.exists() else {}
    thumb = media / f"{vid}.jpg"
    return {
        "file": audio.name,
        "size": audio.stat().st_size,
        "duration": probe_duration(audio) or int(info.get("duration") or 0),
        "upload_date": info.get("upload_date"),
        "description": info.get("description") or "",
        "thumb": thumb.name if thumb.exists() else None,
    }


# ---------------------------------------------------------------- feed

MIME = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".webm": "audio/webm", ".opus": "audio/ogg"}


def clean_title(feed: dict, title: str) -> str:
    for pattern in feed.get("title_cleanup", []):
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", title).strip(" |-–—")


def sub(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k.replace("_", "-") if k != "type_" else "type": v for k, v in attrs.items()})
    if text is not None:
        el.text = text
    return el


def build_feed(cfg: dict, feed: dict, state: dict) -> Path:
    base = f"{cfg['base_url']}/{feed['slug']}"
    eps = [e for e in state["episodes"].values() if e.get("status") == "done"]
    eps.sort(key=lambda e: (e["season"], e["episode"]))

    rss = ET.Element("rss", {"version": "2.0"})
    ch = sub(rss, "channel")
    sub(ch, "title", feed["title"])
    sub(ch, "link", feed.get("link", "https://www.youtube.com"))
    sub(ch, "description", feed.get("description", feed["title"]))
    sub(ch, "language", feed.get("language", "en"))
    sub(ch, f"{{{ITUNES}}}author", feed.get("author", ""))
    sub(ch, f"{{{ITUNES}}}type", "serial")
    sub(ch, f"{{{ITUNES}}}block", "Yes")  # keep it out of public directories
    cover = feed.get("cover")
    if not cover and eps and eps[0].get("thumb"):
        cover = f"{base}/{eps[0]['thumb']}"
    if cover:
        sub(ch, f"{{{ITUNES}}}image", href=cover)
        img = sub(ch, "image")
        sub(img, "url", cover)
        sub(img, "title", feed["title"])
        sub(img, "link", feed.get("link", "https://www.youtube.com"))

    season_names = {int(k): v for k, v in feed.get("season_names", {}).items()}
    for e in eps:
        it = sub(ch, "item")
        sub(it, "title", e["title"])
        sub(it, "description", e.get("description", ""))
        sub(it, "guid", e["id"], isPermaLink="false")
        sub(it, "link", f"https://www.youtube.com/watch?v={e['id']}")
        # Same-day uploads (double-episode premieres) get a few minutes apart so apps sort them correctly.
        day = datetime.strptime(e["upload_date"], "%Y%m%d") if e.get("upload_date") else datetime(2000, 1, 1)
        pub = day.replace(hour=12, tzinfo=timezone.utc) + timedelta(minutes=e["episode"])
        sub(it, "pubDate", email.utils.format_datetime(pub))
        url = f"{base}/{e['file']}"
        sub(it, "enclosure", url=url, length=str(e["size"]), type_=MIME.get(Path(e["file"]).suffix, "audio/mp4"))
        sub(it, f"{{{ITUNES}}}duration", str(e.get("duration", 0)))
        sub(it, f"{{{ITUNES}}}season", str(e["season"]))
        sub(it, f"{{{ITUNES}}}episode", str(e["episode"]))
        sub(it, f"{{{ITUNES}}}episodeType", "full")
        if e["season"] in season_names:
            sub(it, f"{{{PODCAST}}}season", str(e["season"]), name=season_names[e["season"]])
        if e.get("thumb"):
            sub(it, f"{{{ITUNES}}}image", href=f"{base}/{e['thumb']}")

    ET.indent(rss)
    out = cfg["public_dir"] / f"{feed['slug']}.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    ET.ElementTree(rss).write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(out)
    return out


# ---------------------------------------------------------------- commands

def cmd_list(cfg: dict) -> None:
    for feed in cfg["feeds"]:
        state = load_state(cfg, feed)
        print(f"\n== {feed['title']} ==")
        for p in plan_feed(cfg, feed):
            have = "done" if state["episodes"].get(p["id"], {}).get("status") == "done" else "new "
            print(f"S{p['season']:02d}E{p['episode']:02d} {have} {clean_title(feed, p['title'])}")


def cmd_run(cfg: dict) -> int:
    budget = int(cfg["max_downloads_per_run"])
    failures = 0
    for feed in cfg["feeds"]:
        log(f"feed: {feed['title']}")
        state = load_state(cfg, feed)
        try:
            planned = plan_feed(cfg, feed)
        except subprocess.CalledProcessError as exc:
            log(f"  could not read playlists: {exc.stderr.strip()[-500:]}")
            failures += 1
            continue
        for p in planned:
            ep = state["episodes"].setdefault(p["id"], {"id": p["id"], "status": "new"})
            ep.update(title=clean_title(feed, p["title"]), season=p["season"], episode=p["episode"])
            if ep["status"] == "done" or budget <= 0:
                continue
            log(f"  downloading S{p['season']:02d}E{p['episode']:02d} {ep['title']}")
            try:
                ep.update(download(cfg, feed, p["id"]), status="done")
                ep.pop("error", None)
                budget -= 1
                log(f"  ok ({ep['size'] // 1_000_000} MB, {ep['duration'] // 60} min)")
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                err = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
                ep["error"] = (err or "").strip()[-500:]
                failures += 1
                log(f"  failed: {ep['error']}")
            save_state(cfg, feed, state)
            time.sleep(cfg.get("sleep_between_downloads", 20))
        save_state(cfg, feed, state)
        out = build_feed(cfg, feed, state)
        done = sum(1 for e in state["episodes"].values() if e.get("status") == "done")
        log(f"  feed written: {out} ({done} episodes)")
    if budget <= 0:
        log("download limit for this run reached; the rest follows on the next run")
    return 1 if failures else 0


def cmd_feed(cfg: dict) -> None:
    for feed in cfg["feeds"]:
        out = build_feed(cfg, feed, load_state(cfg, feed))
        log(f"feed written: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["run", "list", "feed"])
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.toml")))
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    if not shutil.which("ffprobe"):
        sys.exit("ffmpeg/ffprobe not found")
    if args.command == "list":
        cmd_list(cfg)
    elif args.command == "feed":
        cmd_feed(cfg)
    else:
        sys.exit(cmd_run(cfg))


if __name__ == "__main__":
    main()
