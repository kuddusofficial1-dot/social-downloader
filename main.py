
import os
import re
import time
import shutil
import tempfile
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Social Downloader API")

BLOGGER_ORIGIN = os.getenv("BLOGGER_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if BLOGGER_ORIGIN == "*" else [BLOGGER_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

ALLOWED_HOSTS = {
    "facebook.com", "fb.watch",
    "instagram.com",
    "tiktok.com",
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "reddit.com", "redd.it",
    "pinterest.com", "pin.it",
    "threads.net"
}

MAX_FILE_BYTES = 300 * 1024 * 1024
RATE_LIMIT = 10
RATE_WINDOW = 3600
requests_by_ip = {}


class DownloadRequest(BaseModel):
    url: HttpUrl


def host_is_allowed(host):
    host = (host or "").lower().rstrip(".")
    return any(
        host == domain or host.endswith("." + domain)
        for domain in ALLOWED_HOSTS
    )


def check_rate_limit(ip):
    now = time.time()
    recent = [
        t for t in requests_by_ip.get(ip, [])
        if now - t < RATE_WINDOW
    ]

    if len(recent) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Hourly limit reached. Try later."
        )

    recent.append(now)
    requests_by_ip[ip] = recent


@app.get("/")
def home():
    return {
        "service": "Social Downloader API",
        "status": "online"
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/download")
def download_video(payload: DownloadRequest, request: Request):

    url = str(payload.url)
    parsed = urlparse(url)

    if (
        parsed.scheme not in ("http", "https")
        or not host_is_allowed(parsed.hostname)
    ):
        raise HTTPException(
            status_code=400,
            detail="Unsupported website."
        )

    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)

    temp_dir = tempfile.mkdtemp(prefix="socialdl_")
    output = str(Path(temp_dir) / "%(id)s.%(ext)s")

    command = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--max-filesize", str(MAX_FILE_BYTES),
        "--socket-timeout", "20",
        "--retries", "1",
        "--format", "best[ext=mp4]/best",
        "--output", output,
        url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180
        )

        candidates = [
            p for p in Path(temp_dir).glob("*")
            if p.is_file()
        ]

        if result.returncode != 0 or not candidates:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(
                status_code=422,
                detail=(
                    "Video unavailable. It may be private, "
                    "restricted, unsupported or require login."
                )
            )

        video = max(
            candidates,
            key=lambda p: p.stat().st_size
        )

        if video.stat().st_size > MAX_FILE_BYTES:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(
                status_code=413,
                detail="Video exceeds the size limit."
            )

        return FileResponse(
            path=str(video),
            media_type="application/octet-stream",
            filename=re.sub(
                r"[^A-Za-z0-9._-]",
                "_",
                video.name
            ),
            background=BackgroundTask(
                shutil.rmtree,
                temp_dir,
                ignore_errors=True
            )
        )

    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=504,
            detail="Download timed out."
        )

    except HTTPException:
        raise

    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=500,
            detail="Server error."
        )
