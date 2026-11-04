import os
import re
import sys
import time
import uuid
import json
import shutil
import threading
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

import requests
import yt_dlp
from PIL import Image
from io import BytesIO
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from mutagen.id3 import (
    ID3, TIT2, TPE1, TPE2, TALB, TDRC, TYER, TRCK, TCON, COMM, WOAR, APIC, ID3NoHeaderError
)
from mutagen.mp4 import MP4, MP4Cover

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DOWNLOAD_DIR = BASE_DIR / "Stazene"
DEFAULT_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="YouTube & YT Music Downloader")

# Global state for download jobs and metadata enrichment
JOBS_LOCK = threading.Lock()
DOWNLOAD_JOBS: Dict[str, Dict[str, Any]] = {}
ENRICH_CACHE: Dict[str, Dict[str, Any]] = {}

# Thread pools
DOWNLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=3)
ENRICH_EXECUTOR = ThreadPoolExecutor(max_workers=4)

FFMPEG_DIR: Optional[str] = None


def ensure_ffmpeg() -> Optional[str]:
    """Ensure ffmpeg and ffprobe are available and return their directory path."""
    global FFMPEG_DIR
    if FFMPEG_DIR and os.path.exists(FFMPEG_DIR):
        return FFMPEG_DIR

    # 1. Check system PATH
    sys_ffmpeg = shutil.which("ffmpeg")
    sys_ffprobe = shutil.which("ffprobe")
    if sys_ffmpeg and sys_ffprobe:
        FFMPEG_DIR = str(Path(sys_ffmpeg).parent)
        return FFMPEG_DIR

    # 2. Check local bin folder
    local_bin = BASE_DIR / "bin"
    if (local_bin / "ffmpeg.exe").exists() and (local_bin / "ffprobe.exe").exists():
        FFMPEG_DIR = str(local_bin)
        os.environ["PATH"] = FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
        return FFMPEG_DIR

    # 3. Use static-ffmpeg (automatically downloads ffmpeg + ffprobe binaries if needed)
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        sf_ffmpeg = shutil.which("ffmpeg")
        if sf_ffmpeg:
            FFMPEG_DIR = str(Path(sf_ffmpeg).parent)
            return FFMPEG_DIR
    except Exception as e:
        print(f"[WARN] static_ffmpeg initialization issue: {e}")

    return None


def sanitize_filename(name: str) -> str:
    """Clean a string so it is a valid Windows filename or folder name."""
    if not name:
        return "Neznamy"
    # Replace invalid Windows filename characters
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " - ", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(". ")
    return cleaned[:180] if cleaned else "Neznamy"


def clean_artist_name(artist: str) -> str:
    """Remove '- Topic' suffix added by YouTube Music auto-generated channels."""
    if not artist:
        return ""
    artist = re.sub(r"\s*-\s*Topic$", "", str(artist), flags=re.IGNORECASE).strip()
    return artist


def clean_track_title(title: str) -> str:
    """Clean common YouTube video suffixes like (Official Video), [Lyrics], etc."""
    if not title:
        return ""
    patterns = [
        r"\(\s*official\s*(music\s*)?(video|audio|lyric\s*video|visualizer|hd|4k)\s*\)",
        r"\[\s*official\s*(music\s*)?(video|audio|lyric\s*video|visualizer|hd|4k)\s*\]",
        r"\(\s*(lyrics?|lyric\s*video|audio|visualizer|hd|hq|4k|remastered\s*\d*)\s*\)",
        r"\[\s*(lyrics?|lyric\s*video|audio|visualizer|hd|hq|4k|remastered\s*\d*)\s*\]",
        r"\(\s*oficiální\s*(videoklip|video|audio)\s*\)",
        r"\[\s*oficiální\s*(videoklip|video|audio)\s*\]",
    ]
    cleaned = str(title)
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or str(title).strip()


def format_date_str(raw_date: Optional[str], timestamp: Optional[int] = None) -> str:
    """Format YYYYMMDD or unix timestamp into YYYY-MM-DD."""
    if raw_date and len(str(raw_date)) == 8 and str(raw_date).isdigit():
        s = str(raw_date)
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if raw_date and len(str(raw_date)) == 4 and str(raw_date).isdigit():
        return str(raw_date)
    if timestamp:
        try:
            return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def pick_best_thumbnail(entry: Dict[str, Any]) -> str:
    """Choose the best thumbnail URL from a yt-dlp entry."""
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    if thumbs and isinstance(thumbs, list):
        # Pick the last one (usually highest resolution)
        for t in reversed(thumbs):
            if isinstance(t, dict) and t.get("url"):
                return t["url"]
    vid_id = entry.get("id")
    if vid_id and len(str(vid_id)) == 11:
        return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
    return ""


def normalize_compare(s: str) -> str:
    """Normalize string for fuzzy matching of artist/channel names."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9\u00c0-\u024f]", "", str(s).lower())


def names_match(a: str, b: str) -> bool:
    """Check if two artist/channel strings refer to the same entity."""
    na = normalize_compare(a)
    nb = normalize_compare(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 4 and len(nb) >= 4 and (na in nb or nb in na):
        return True
    return False


NON_ARTIST_SUFFIX_RE = re.compile(
    r"^(original\s+song|official\s*(music\s*)?(video|audio|lyric\s*video|visualizer)|"
    r"music\s+video|lyric\s+video|lyrics?|visualizer|audio|live(\s+performance)?|"
    r"cover|acoustic|instrumental|ost|soundtrack|theme\s+song|full\s+song|hd|4k)$",
    re.IGNORECASE,
)


def split_raw_title(raw_title: str):
    """Split a title by common separators if present."""
    for sep in [" - ", " – ", " — ", " | ", "- ", " -", "– ", " –"]:
        if sep in raw_title:
            parts = raw_title.split(sep, 1)
            left = parts[0].strip()
            right = parts[1].strip()
            if left and right:
                return left, right
    return None, None


def apply_js_and_cookie_opts(
    ydl_opts: Dict[str, Any],
    use_cookies: bool = False,
    browser_name: str = "firefox",
) -> Dict[str, Any]:
    """Attach Node.js runtime for EJS challenge solving and optional browser cookies for 18+ videos."""
    opts = dict(ydl_opts)
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}

    if use_cookies:
        cookie_file = BASE_DIR / "cookies.txt"
        if cookie_file.exists():
            opts["cookiefile"] = str(cookie_file)
        else:
            opts["cookiesfrombrowser"] = (browser_name,)
    return opts


def extract_smart_metadata(
    entry: Dict[str, Any],
    playlist_title: str = "",
    index: int = 1,
    fallback_uploader: str = "",
) -> Dict[str, Any]:
    """Extract and normalize track, artist, album, release date, and thumbnail from yt-dlp info."""
    raw_title = entry.get("title") or "Neznámý název"
    uploader = clean_artist_name(entry.get("uploader") or entry.get("channel") or fallback_uploader or "")
    creator = clean_artist_name(entry.get("creator") or "")

    # 1. Explicit music metadata (primarily from YouTube Music / licensed tracks)
    explicit_artist = entry.get("artist") or ""
    if not explicit_artist and entry.get("artists") and isinstance(entry.get("artists"), list):
        explicit_artist = ", ".join(str(a) for a in entry["artists"] if a)
    if not explicit_artist and creator:
        explicit_artist = creator
    explicit_artist = clean_artist_name(explicit_artist)

    explicit_track = entry.get("track") or ""

    artist = ""
    track = ""

    if explicit_track:
        track = clean_track_title(explicit_track)
        artist = explicit_artist or uploader
    else:
        left, right = split_raw_title(raw_title)
        if left and right:
            left_clean = clean_track_title(left)
            right_clean = clean_track_title(right)

            # Check if description explicitly names the song
            desc = entry.get("description") or ""
            desc_song_match = re.search(r"(?:^|\n)\s*(?:song|track|skladba|title)\s*:\s*(.+)", desc, re.IGNORECASE)
            desc_song = desc_song_match.group(1).strip() if desc_song_match else ""

            # Check if right side is actually the channel/uploader name (e.g. "SONG TITLE - KrimsonTheFox")
            if uploader and names_match(right_clean, uploader) and not names_match(left_clean, uploader):
                artist = uploader
                track = left_clean
            # Check if left side is the channel/uploader name (e.g. "KrimsonTheFox - SONG TITLE")
            elif uploader and names_match(left_clean, uploader) and not names_match(right_clean, uploader):
                artist = uploader
                track = right_clean
            # Check if description SONG: matches left side
            elif desc_song and names_match(desc_song, left_clean):
                track = left_clean
                artist = right_clean if not NON_ARTIST_SUFFIX_RE.match(right_clean) else (uploader or right_clean)
            # Check if description SONG: matches right side
            elif desc_song and names_match(desc_song, right_clean):
                track = right_clean
                artist = left_clean
            # Check if right side is a generic descriptor like "Original Song", "Official Video", etc.
            elif NON_ARTIST_SUFFIX_RE.match(right_clean):
                artist = explicit_artist or uploader
                track = left_clean
            else:
                # Default fallback when neither matches uploader:
                # If explicit_artist is known, match against it; otherwise assume Left = Artist, Right = Track
                if explicit_artist and names_match(right_clean, explicit_artist):
                    artist = explicit_artist
                    track = left_clean
                else:
                    artist = explicit_artist or left_clean
                    track = right_clean
        else:
            artist = explicit_artist or uploader
            track = clean_track_title(raw_title)

    if not artist:
        artist = uploader or "Neznámý interpret"

    if not track:
        track = clean_track_title(raw_title)

    # Remove redundant artist prefix or suffix if still attached to track
    if artist and artist != "Neznámý interpret":
        for sep in [" - ", " – ", " — ", "- ", " -"]:
            if track.lower().startswith(artist.lower() + sep):
                track = track[len(artist) + len(sep):].strip()
            elif track.lower().endswith(sep + artist.lower()):
                track = track[:-len(artist) - len(sep)].strip()

    album = entry.get("album") or playlist_title or ""
    release_raw = (
        entry.get("release_date")
        or entry.get("upload_date")
        or (str(entry.get("release_year")) if entry.get("release_year") else "")
    )
    release_date = format_date_str(release_raw, entry.get("timestamp") or entry.get("release_timestamp"))

    vid_id = entry.get("id") or ""
    webpage_url = entry.get("webpage_url") or entry.get("url") or ""
    if not webpage_url.startswith("http") and vid_id:
        webpage_url = f"https://www.youtube.com/watch?v={vid_id}"

    return {
        "id": vid_id,
        "index": index,
        "url": webpage_url,
        "raw_title": raw_title,
        "title": track or raw_title,
        "artist": artist or "Neznámý interpret",
        "album": album,
        "release_date": release_date,
        "duration": entry.get("duration") or 0,
        "thumbnail": pick_best_thumbnail(entry),
        "channel": uploader,
        "description": (entry.get("description") or "")[:1000],
        "genre": entry.get("genre") or "",
        "has_explicit_track": bool(explicit_track),
        "enriched": bool(release_date and (entry.get("track") or entry.get("upload_date"))),
    }


def refine_playlist_items_metadata(items: List[Dict[str, Any]], playlist_uploader: str = "") -> None:
    """Detect playlist-wide naming patterns (e.g. 'Track Title - Artist' across all videos) and fix order."""
    if not items:
        return

    # Count occurrences of left and right parts across the playlist
    left_counts: Dict[str, int] = {}
    right_counts: Dict[str, int] = {}
    split_pairs = []

    for it in items:
        left, right = split_raw_title(it.get("raw_title") or "")
        if left and right:
            lc = normalize_compare(clean_track_title(left))
            rc = normalize_compare(clean_track_title(right))
            if lc:
                left_counts[lc] = left_counts.get(lc, 0) + 1
            if rc:
                right_counts[rc] = right_counts.get(rc, 0) + 1
            split_pairs.append((it, clean_track_title(left), clean_track_title(right), lc, rc))

    if not split_pairs:
        return

    max_left_repeat = max(left_counts.values()) if left_counts else 0
    max_right_repeat = max(right_counts.values()) if right_counts else 0

    for it, left_clean, right_clean, lc, rc in split_pairs:
        if it.get("has_explicit_track"):
            continue
        ch = it.get("channel") or playlist_uploader or ""
        # 1. Direct match with channel or playlist uploader
        if ch and names_match(right_clean, ch) and not names_match(left_clean, ch):
            it["artist"] = ch
            it["title"] = left_clean
        elif ch and names_match(left_clean, ch) and not names_match(right_clean, ch):
            it["artist"] = ch
            it["title"] = right_clean
        # 2. Playlist-wide pattern: right part is repeated across multiple videos (e.g. "Song - Artist")
        elif right_counts.get(rc, 0) >= 2 and right_counts.get(rc, 0) > left_counts.get(lc, 0):
            it["artist"] = right_clean
            it["title"] = left_clean
        # 3. Playlist-wide pattern: left part is repeated across multiple videos (e.g. "Artist - Song")
        elif left_counts.get(lc, 0) >= 2 and left_counts.get(lc, 0) > right_counts.get(rc, 0):
            it["artist"] = left_clean
            it["title"] = right_clean


def enrich_single_video(video_id: str, url: str, playlist_title: str, index: int) -> Optional[Dict[str, Any]]:
    """Fetch full metadata for a single video when playlist was loaded with extract_flat."""
    if video_id in ENRICH_CACHE:
        cached = dict(ENRICH_CACHE[video_id])
        if playlist_title and not cached.get("album"):
            cached["album"] = playlist_title
        cached["index"] = index
        return cached

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
    }
    target_url = url if url.startswith("http") else f"https://www.youtube.com/watch?v={video_id}"

    for use_cookies in (False, True):
        try:
            ydl_opts = apply_js_and_cookie_opts(base_opts, use_cookies=use_cookies)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if info:
                    parsed = extract_smart_metadata(info, playlist_title=playlist_title, index=index)
                    parsed["enriched"] = True
                    ENRICH_CACHE[video_id] = parsed
                    return parsed
        except Exception as e:
            if not use_cookies:
                continue
            print(f"[WARN] Metadata enrich failed for {video_id}: {e}")
    return None


def fetch_cover_art_jpeg(thumbnail_url: str, video_id: str = "") -> Optional[bytes]:
    """Download thumbnail and convert it to high-quality JPEG bytes for ID3/MP4 cover art."""
    urls_to_try = []
    if video_id and len(video_id) == 11:
        urls_to_try.append(f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg")
        urls_to_try.append(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
    if thumbnail_url and thumbnail_url not in urls_to_try:
        urls_to_try.insert(0, thumbnail_url)

    headers = {"User-Agent": "Mozilla/5.0"}
    for u in urls_to_try:
        try:
            resp = requests.get(u, headers=headers, timeout=10)
            if resp.status_code == 200 and len(resp.content) > 1000:
                img = Image.open(BytesIO(resp.content))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                out = BytesIO()
                img.save(out, format="JPEG", quality=92)
                return out.getvalue()
        except Exception:
            continue
    return None


def apply_mp3_tags(
    file_path: Path,
    meta: Dict[str, Any],
    total_tracks: int = 1,
    embed_cover: bool = True
) -> None:
    """Write rich ID3v2.3 metadata and cover art into an MP3 file."""
    try:
        try:
            tags = ID3(str(file_path))
        except ID3NoHeaderError:
            tags = ID3()

        title = meta.get("title") or meta.get("raw_title") or file_path.stem
        artist = meta.get("artist") or "Neznámý interpret"
        album = meta.get("album") or ""
        release_date = meta.get("release_date") or ""
        year = release_date[:4] if len(release_date) >= 4 else ""
        index = meta.get("index") or 1
        url = meta.get("url") or ""
        description = meta.get("description") or ""
        genre = meta.get("genre") or "Music"

        tags.delall("TIT2")
        tags.add(TIT2(encoding=3, text=title))

        tags.delall("TPE1")
        tags.add(TPE1(encoding=3, text=artist))

        tags.delall("TPE2")
        tags.add(TPE2(encoding=3, text=artist))

        if album:
            tags.delall("TALB")
            tags.add(TALB(encoding=3, text=album))

        if release_date:
            tags.delall("TDRC")
            tags.add(TDRC(encoding=3, text=release_date))
        if year:
            tags.delall("TYER")
            tags.add(TYER(encoding=3, text=year))

        tags.delall("TRCK")
        tags.add(TRCK(encoding=3, text=f"{index}/{total_tracks}" if total_tracks > 1 else str(index)))

        if genre:
            tags.delall("TCON")
            tags.add(TCON(encoding=3, text=genre))

        if url:
            tags.delall("WOAR")
            tags.add(WOAR(url=url))

        comment_text = f"Zdroj: {url}"
        if release_date:
            comment_text += f" | Datum zveřejnění: {release_date}"
        if description:
            comment_text += f"\n{description[:400]}"
        tags.delall("COMM")
        tags.add(COMM(encoding=3, lang="ces", desc="", text=comment_text))

        if embed_cover:
            cover_bytes = fetch_cover_art_jpeg(meta.get("thumbnail", ""), meta.get("id", ""))
            if cover_bytes:
                tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,  # Front cover
                        desc="Cover",
                        data=cover_bytes,
                    )
                )

        # Save as ID3v2.3 for maximum compatibility with Windows Explorer and all players
        tags.save(str(file_path), v2_version=3)
    except Exception as e:
        print(f"[WARN] Failed to write ID3 tags to {file_path}: {e}")


def apply_mp4_tags(
    file_path: Path,
    meta: Dict[str, Any],
    total_tracks: int = 1,
    embed_cover: bool = True
) -> None:
    """Write rich MP4 metadata atoms and cover art into an MP4 video file."""
    try:
        video = MP4(str(file_path))
        if video.tags is None:
            video.add_tags()

        title = meta.get("title") or meta.get("raw_title") or file_path.stem
        artist = meta.get("artist") or "Neznámý interpret"
        album = meta.get("album") or ""
        release_date = meta.get("release_date") or ""
        index = int(meta.get("index") or 1)
        url = meta.get("url") or ""
        description = meta.get("description") or ""
        genre = meta.get("genre") or ""

        video.tags["\xa9nam"] = [title]
        video.tags["\xa9ART"] = [artist]
        video.tags["aART"] = [artist]
        if album:
            video.tags["\xa9alb"] = [album]
        if release_date:
            video.tags["\xa9day"] = [release_date]
        if genre:
            video.tags["\xa9gen"] = [genre]

        video.tags["trkn"] = [(index, max(total_tracks, index))]

        comment_parts = []
        if url:
            comment_parts.append(f"Zdroj: {url}")
        if release_date:
            comment_parts.append(f"Zveřejněno: {release_date}")
        if comment_parts:
            video.tags["\xa9cmt"] = [" | ".join(comment_parts)]
        if description:
            video.tags["desc"] = [description[:250]]
            video.tags["\xa9des"] = [description[:800]]

        if embed_cover:
            cover_bytes = fetch_cover_art_jpeg(meta.get("thumbnail", ""), meta.get("id", ""))
            if cover_bytes:
                video.tags["covr"] = [MP4Cover(cover_bytes, imageformat=MP4Cover.FORMAT_JPEG)]

        video.save()
    except Exception as e:
        print(f"[WARN] Failed to write MP4 tags to {file_path}: {e}")


# ==================== API MODELS ====================

class AnalyzeRequest(BaseModel):
    url: str
    prefer_playlist: bool = True


class EnrichRequest(BaseModel):
    playlist_title: str = ""
    items: List[Dict[str, Any]]


class DownloadItemModel(BaseModel):
    id: str
    index: int = 1
    url: str
    raw_title: str = ""
    title: str
    artist: str
    album: str = ""
    release_date: str = ""
    thumbnail: str = ""
    description: str = ""
    genre: str = ""
    format_choice: str = "mp3"  # "mp3", "mp4", or "both"


class StartDownloadRequest(BaseModel):
    playlist_title: str = ""
    is_playlist: bool = False
    output_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    create_playlist_subfolder: bool = True
    naming_format: str = "artist_title"  # "artist_title", "index_artist_title", "original_title", "title_only"
    embed_cover: bool = True
    video_quality: str = "best"  # "best", "1080", "720"
    items: List[DownloadItemModel]


# ==================== API ENDPOINTS ====================

@app.get("/api/config")
def get_config():
    ffmpeg_dir = ensure_ffmpeg()
    return {
        "default_output_dir": str(DEFAULT_DOWNLOAD_DIR),
        "ffmpeg_ready": bool(ffmpeg_dir),
        "ffmpeg_dir": ffmpeg_dir or "",
    }


@app.post("/api/browse-folder")
def browse_folder(payload: Dict[str, Any] = None):
    """Open native Windows folder selection dialog on the local machine."""
    initial_dir = (payload or {}).get("current_dir") or str(DEFAULT_DOWNLOAD_DIR)
    if not os.path.exists(initial_dir):
        initial_dir = str(DEFAULT_DOWNLOAD_DIR)

    try:
        # Run tkinter in a subprocess so it never blocks or conflicts with asyncio/threads
        script = (
            "import tkinter as tk; from tkinter import filedialog; "
            "root = tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
            f"folder = filedialog.askdirectory(initialdir={repr(initial_dir)}, title='Vyberte cílovou složku pro stahování'); "
            "print(folder); root.destroy()"
        )
        res = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120
        )
        chosen = res.stdout.strip()
        if chosen:
            chosen_path = str(Path(chosen).resolve())
            return {"selected": True, "path": chosen_path}
        return {"selected": False, "path": initial_dir}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nelze otevřít dialog výběru složky: {e}")


@app.post("/api/open-folder")
def open_folder(payload: Dict[str, Any]):
    """Open the specified folder in Windows Explorer."""
    folder = payload.get("path") or str(DEFAULT_DOWNLOAD_DIR)
    folder_path = Path(folder)
    folder_path.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(folder_path))
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze")
def analyze_url(req: AnalyzeRequest):
    """Analyze a YouTube or YouTube Music URL (playlist, album, or single track/video)."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Zadejte prosím platný odkaz.")

    # Normalize URL if user pasted without scheme
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    parsed_url = urlparse(url)
    qs = parse_qs(parsed_url.query)

    # If URL has both v= and list=, and user wants the whole playlist, convert watch?v=...&list=... to playlist?list=...
    # unless it's an auto-mix radio (RD...) where extract_info on the watch URL with noplaylist=False works better
    list_id = qs.get("list", [""])[0]
    vid_id = qs.get("v", [""])[0]

    target_url = url
    is_radio_mix = list_id.startswith("RD")

    if req.prefer_playlist and list_id and vid_id and not is_radio_mix:
        # Constructing the playlist URL ensures yt-dlp fetches the whole playlist cleanly
        domain = parsed_url.netloc or "www.youtube.com"
        target_url = f"https://{domain}/playlist?list={list_id}"

    base_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": not req.prefer_playlist,
        "playlistend": 50 if is_radio_mix else 500,
        "ignoreerrors": True,
    }

    info = None
    last_err = None
    for use_cookies in (False, True):
        try:
            ydl_opts = apply_js_and_cookie_opts(base_opts, use_cookies=use_cookies)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
            if info:
                break
        except Exception as e:
            last_err = e
            if not use_cookies:
                continue

    if not info:
        if last_err:
            raise HTTPException(status_code=400, detail=f"Nepodařilo se načíst odkaz: {str(last_err)}")
        raise HTTPException(status_code=404, detail="Na zadaném odkazu nebyla nalezena žádná videa ani skladby.")

    is_music_yt = "music.youtube.com" in url.lower()

    # Check if it's a playlist / multi-entry result
    if "entries" in info and info["entries"] is not None:
        raw_entries = [e for e in info["entries"] if e]
        playlist_title = info.get("title") or info.get("playlist_title") or "YouTube Playlist"
        # Clean common "Album - " prefix on YT Music album titles
        playlist_title_clean = re.sub(r"^Album\s*-\s*", "", playlist_title, flags=re.IGNORECASE).strip()

        playlist_uploader = clean_artist_name(info.get("uploader") or info.get("channel") or "")

        items = []
        for idx, entry in enumerate(raw_entries, start=1):
            # Ignore deleted/private videos
            title_check = (entry.get("title") or "").lower()
            if title_check in ("[private video]", "[deleted video]", "private video", "deleted video"):
                continue
            meta = extract_smart_metadata(
                entry,
                playlist_title=playlist_title_clean,
                index=len(items) + 1,
                fallback_uploader=playlist_uploader,
            )
            meta["format_choice"] = "mp3" if is_music_yt else "mp3"
            items.append(meta)

        if not items:
            raise HTTPException(status_code=404, detail="Playlist neobsahuje žádné dostupné skladby nebo je soukromý.")

        refine_playlist_items_metadata(items, playlist_uploader=playlist_uploader)

        return {
            "is_playlist": True,
            "playlist_title": playlist_title_clean,
            "playlist_uploader": playlist_uploader,
            "source_type": "YouTube Music" if is_music_yt else "YouTube",
            "total_count": len(items),
            "items": items,
        }
    else:
        # Single video or track
        meta = extract_smart_metadata(info, playlist_title=info.get("album") or "", index=1)
        meta["enriched"] = True
        if meta.get("id"):
            ENRICH_CACHE[meta["id"]] = meta

        return {
            "is_playlist": False,
            "playlist_title": meta.get("album") or meta.get("title") or "",
            "playlist_uploader": meta.get("artist") or "",
            "source_type": "YouTube Music" if is_music_yt else "YouTube",
            "total_count": 1,
            "items": [meta],
        }


@app.post("/api/enrich")
def enrich_items(req: EnrichRequest):
    """Fetch complete metadata (exact release date, track, artist, album) for a batch of playlist items."""
    futures = []
    for item in req.items:
        vid_id = item.get("id") or ""
        url = item.get("url") or ""
        idx = item.get("index") or 1
        if vid_id:
            futures.append((vid_id, ENRICH_EXECUTOR.submit(enrich_single_video, vid_id, url, req.playlist_title, idx)))

    results = {}
    for vid_id, fut in futures:
        try:
            res = fut.result(timeout=20)
            if res:
                results[vid_id] = res
        except Exception:
            pass

    return {"enriched_items": results}


def build_base_filename(item: Dict[str, Any], naming_format: str) -> str:
    """Create sanitized filename stem according to user's chosen naming pattern."""
    artist = sanitize_filename(item.get("artist") or "Neznámý interpret")
    title = sanitize_filename(item.get("title") or item.get("raw_title") or "Skladba")
    raw_title = sanitize_filename(item.get("raw_title") or title)
    idx = int(item.get("index") or 1)

    if naming_format == "artist_title":
        if artist and artist.lower() not in ("neznámý interpret", "neznamy") and not title.lower().startswith(artist.lower()):
            return sanitize_filename(f"{artist} - {title}")
        return title
    elif naming_format == "index_artist_title":
        if artist and artist.lower() not in ("neznámý interpret", "neznamy") and not title.lower().startswith(artist.lower()):
            return sanitize_filename(f"{idx:02d}. {artist} - {title}")
        return sanitize_filename(f"{idx:02d}. {title}")
    elif naming_format == "title_only":
        return title
    else:  # original_title
        return raw_title


def download_single_item_Format(
    job_id: str,
    item_key: str,
    item_meta: Dict[str, Any],
    fmt: str,  # "mp3" or "mp4"
    target_dir: Path,
    naming_format: str,
    embed_cover: bool,
    video_quality: str,
    total_tracks: int,
    ffmpeg_dir: str,
):
    """Download either MP3 (highest quality audio) or MP4 (video) and embed full metadata."""
    # Update job item status
    def update_state(**kwargs):
        with JOBS_LOCK:
            if job_id in DOWNLOAD_JOBS and item_key in DOWNLOAD_JOBS[job_id]["items"]:
                DOWNLOAD_JOBS[job_id]["items"][item_key].update(kwargs)

    update_state(status="preparing", status_text="Načítání detailních informací o skladbě…", progress=2)

    # Ensure we have full enriched metadata (release date, album, artist, description, etc.)
    vid_id = item_meta.get("id") or ""
    url = item_meta.get("url") or f"https://www.youtube.com/watch?v={vid_id}"

    full_meta = dict(item_meta)
    if not full_meta.get("release_date"):
        enriched = enrich_single_video(vid_id, url, full_meta.get("album", ""), full_meta.get("index", 1))
        if enriched:
            # Keep user-edited title/artist/album if already customized, fill missing fields
            for k, v in enriched.items():
                if not full_meta.get(k) and v:
                    full_meta[k] = v
            if full_meta.get("artist") in ("", "Neznámý interpret") and enriched.get("artist"):
                full_meta["artist"] = enriched["artist"]

    base_stem = build_base_filename(full_meta, naming_format)
    ext = "mp3" if fmt == "mp3" else "mp4"
    final_path = target_dir / f"{base_stem}.{ext}"

    # Temporary template so yt-dlp writes cleanly even with parallel jobs
    temp_prefix = f".tmp_{uuid.uuid4().hex[:8]}_{base_stem}"
    outtmpl = str(target_dir / f"{temp_prefix}.%(ext)s")

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total > 0 else 0.0
            speed = d.get("speed") or 0
            speed_str = f"{speed / (1024 * 1024):.1f} MB/s" if speed else ""
            update_state(
                status="downloading",
                progress=round(min(pct * 0.88, 88.0), 1),
                speed=speed_str,
                status_text=f"Stahování ({fmt.upper()})… {pct:.0f} %",
            )
        elif d.get("status") == "finished":
            update_state(
                status="processing",
                progress=90.0,
                status_text=f"Zpracování {fmt.upper()} a zápis metadat…",
            )

    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": outtmpl,
        "progress_hooks": [progress_hook],
        "ffmpeg_location": ffmpeg_dir,
        "writethumbnail": False,
    }

    if fmt == "mp3":
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ]
    else:
        # Video MP4
        if video_quality == "1080":
            ydl_opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        elif video_quality == "720":
            ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best"
        else:
            ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
        ydl_opts["merge_output_format"] = "mp4"

    info = None
    last_dl_err = None
    for use_cookies in (False, True):
        try:
            if use_cookies:
                update_state(
                    status="preparing",
                    status_text="Ověřování věku 18+ přes prohlížeč a stahování…",
                )
            run_opts = apply_js_and_cookie_opts(ydl_opts, use_cookies=use_cookies)
            with yt_dlp.YoutubeDL(run_opts) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except Exception as e:
            last_dl_err = e
            if not use_cookies:
                continue
            raise e

    # Merge any newly discovered info from download
    if info:
        dl_meta = extract_smart_metadata(info, playlist_title=full_meta.get("album", ""), index=full_meta.get("index", 1))
        if not full_meta.get("release_date") and dl_meta.get("release_date"):
            full_meta["release_date"] = dl_meta["release_date"]
        if not full_meta.get("album") and dl_meta.get("album"):
            full_meta["album"] = dl_meta["album"]
        if not full_meta.get("description") and dl_meta.get("description"):
            full_meta["description"] = dl_meta["description"]

    # Locate the produced temp file
    produced_file = target_dir / f"{temp_prefix}.{ext}"
    if not produced_file.exists():
        # Search by prefix
        candidates = list(target_dir.glob(f"{temp_prefix}.*"))
        if candidates:
            produced_file = candidates[0]
        else:
            raise RuntimeError(f"Výstupní soubor nebyl nalezen pro {base_stem}.{ext}")

    # Move to final destination path
    if final_path.exists():
        try:
            final_path.unlink()
        except Exception:
            final_path = target_dir / f"{base_stem} ({uuid.uuid4().hex[:4]}).{ext}"

    produced_file.replace(final_path)

    # Embed rich metadata & cover art
    update_state(
        status="tagging",
        progress=95.0,
        status_text="Zápis informací o skladbě, interpretovi, datu a přebalu…",
    )

    if fmt == "mp3":
        apply_mp3_tags(final_path, full_meta, total_tracks=total_tracks, embed_cover=embed_cover)
    else:
        apply_mp4_tags(final_path, full_meta, total_tracks=total_tracks, embed_cover=embed_cover)

    return final_path, full_meta


def run_download_job(job_id: str, req: StartDownloadRequest):
    """Background worker that processes all items in a download job."""
    ffmpeg_dir = ensure_ffmpeg()
    if not ffmpeg_dir:
        with JOBS_LOCK:
            DOWNLOAD_JOBS[job_id]["status"] = "error"
            DOWNLOAD_JOBS[job_id]["error"] = "Nebyl nalezen nástroj FFmpeg potřebný pro převod do MP3 a spojení videa."
        return

    base_dir = Path(req.output_dir.strip() if req.output_dir else str(DEFAULT_DOWNLOAD_DIR))
    if req.create_playlist_subfolder and req.playlist_title and (req.is_playlist or len(req.items) > 1):
        target_dir = base_dir / sanitize_filename(req.playlist_title)
    else:
        target_dir = base_dir

    target_dir.mkdir(parents=True, exist_ok=True)
    with JOBS_LOCK:
        DOWNLOAD_JOBS[job_id]["target_dir"] = str(target_dir)

    total_tracks = len(req.items)

    def process_item(item_model: DownloadItemModel):
        item_key = f"{item_model.index}_{item_model.id}"
        item_dict = item_model.model_dump()
        fmt_choice = item_model.format_choice.lower()
        formats_to_run = ["mp3", "mp4"] if fmt_choice == "both" else [fmt_choice]

        saved_files = []
        try:
            for f in formats_to_run:
                out_path, updated_meta = download_single_item_Format(
                    job_id=job_id,
                    item_key=item_key,
                    item_meta=item_dict,
                    fmt=f,
                    target_dir=target_dir,
                    naming_format=req.naming_format,
                    embed_cover=req.embed_cover,
                    video_quality=req.video_quality,
                    total_tracks=total_tracks,
                    ffmpeg_dir=ffmpeg_dir,
                )
                saved_files.append(out_path.name)
                item_dict.update(updated_meta)

            with JOBS_LOCK:
                DOWNLOAD_JOBS[job_id]["items"][item_key].update({
                    "status": "completed",
                    "progress": 100.0,
                    "speed": "",
                    "status_text": f"Hotovo ({', '.join(saved_files)})",
                    "saved_files": saved_files,
                    "release_date": item_dict.get("release_date", ""),
                })
                DOWNLOAD_JOBS[job_id]["completed_count"] += 1
        except Exception as e:
            with JOBS_LOCK:
                DOWNLOAD_JOBS[job_id]["items"][item_key].update({
                    "status": "error",
                    "status_text": f"Chyba: {str(e)}",
                    "speed": "",
                })
                DOWNLOAD_JOBS[job_id]["failed_count"] += 1

    # Process up to 3 tracks in parallel inside this job
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(process_item, item) for item in req.items]
        for fut in futures:
            try:
                fut.result()
            except Exception:
                pass

    with JOBS_LOCK:
        DOWNLOAD_JOBS[job_id]["status"] = "completed"


@app.post("/api/download")
def start_download(req: StartDownloadRequest):
    """Create and launch a download batch job."""
    if not req.items:
        raise HTTPException(status_code=400, detail="Nebyly vybrány žádné položky ke stažení.")

    job_id = uuid.uuid4().hex[:12]
    items_state = {}
    for item in req.items:
        item_key = f"{item.index}_{item.id}"
        items_state[item_key] = {
            "id": item.id,
            "index": item.index,
            "title": item.title,
            "artist": item.artist,
            "format_choice": item.format_choice,
            "status": "queued",
            "status_text": "Ve frontě…",
            "progress": 0.0,
            "speed": "",
            "saved_files": [],
            "release_date": item.release_date,
        }

    with JOBS_LOCK:
        DOWNLOAD_JOBS[job_id] = {
            "job_id": job_id,
            "status": "running",
            "total_count": len(req.items),
            "completed_count": 0,
            "failed_count": 0,
            "target_dir": req.output_dir,
            "items": items_state,
            "error": "",
        }

    DOWNLOAD_EXECUTOR.submit(run_download_job, job_id, req)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    with JOBS_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Úloha nebyla nalezena.")
        return job


# Serve frontend static files
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    import webbrowser
    ensure_ffmpeg()
    port = 8765
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"============================================================")
    print(f" YouTube & YT Music Downloader bezi na: http://127.0.0.1:{port}")
    print(f"============================================================")
    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
