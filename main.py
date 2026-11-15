import os
import re
import time
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from starlette.background import BackgroundTask

app = FastAPI(title="Social Downloader API", version="1.0.0")

# Replace with your Blogger domain after publishing, e.g. https://yourblog.blogspot.com
BLOGGER_ORIGIN = os.getenv("BLOGGER_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if BLOGGER_ORIGIN == "*" else [BLOGGER_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

ALLOWED_HOSTS = {
    "facebook.com", "fb.watch", "instagram.com",
    "tiktok.com", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "redd.it", "reddit.com",
    "pinterest.com", "pin.it", "threads.net",
}
MAX_FILE_BYTES = 300 * 1024 * 1024
RATE_WINDOW = 3600
RATE_LIMIT = 10
requests_by_ip = {}

class DownloadRequest(BaseModel):
    url: HttpUrl

def host_is_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_HOSTS)

def check_rate_limit(ip: str):
    now = time.time()
    recent = [t for t in requests_by_ip.get(ip, []) if now - t < RATE_WINDOW]
    if len(recent) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit reached. Try again later.")
    recent.append(now)
    requests_by_ip[ip] = recent

def remove_file(path: str):
    try:
        os.remove(path)
    except OSError:
        pass

@app.get("/")
def home():
    return {
        "service": "Social Downloader API",
        "status": "online",
        "usage": "POST /api/download with JSON: {\"url\":\"https://...\"}",
        "note": "Only download content you own or have permission to save."
    }

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/download")
def download_video(payload: DownloadRequest, request: Request):
    url = str(payload.url)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not host_is_allowed(parsed.hostname):
        raise HTTPException(status_code=400, detail="This website is not supported.")

    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    with tempfile.TemporaryDirectory(prefix="socialdl_") as temp_dir:
        output_template = str(Path(temp_dir) / "%(id)s.%(ext)s")
        command = [
            "yt-dlp",
            "--no-warnings",
            "--no-playlist",
            "--max-filesize", str(MAX_FILE_BYTES),
            "--socket-timeout", "20",
            "--retries", "1",
            "--format", "best[ext=mp4]/best",
            "--output", output_template,
            url,
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=180, check=False
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Download timed out. Try another public video link.")
        except Exception:
            raise HTTPException(status_code=500, detail="Downloader could not start.")

        candidates = [p for p in Path(temp_dir).glob("*") if p.is_file()]
        if result.returncode != 0 or not candidates:
            # Do not expose raw server output to visitors.
            raise HTTPException(
                status_code=422,
                detail="Could not fetch this video. It may be private, restricted, unsupported, or require login."
            )

        video_path = max(candidates, key=lambda p: p.stat().st_size)
        if video_path.stat().st_size > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail="Video is too large for this free service.")

        # FileResponse streams the file; the temporary directory is removed after response.
        return FileResponse(
            path=str(video_path),
            media_type="application/octet-stream",
            filename=re.sub(r"[^A-Za-z0-9._-]", "_", video_path.name),
            background=BackgroundTask(lambda: None),
        )
