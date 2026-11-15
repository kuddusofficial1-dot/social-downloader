
import os
import re
import time
import shutil
import tempfile
import subprocess
import logging

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, HttpUrl

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("social-downloader")

app = FastAPI(
    title="Social Downloader API",
    version="0.2.0"
)

# CORS
BLOGGER_ORIGIN = os.getenv("BLOGGER_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=(
        ["*"] if BLOGGER_ORIGIN == "*"
        else [BLOGGER_ORIGIN]
    ),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Supported domains
ALLOWED_HOSTS = {
    "facebook.com",
    "fb.watch",
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "reddit.com",
    "redd.it",
    "pinterest.com",
    "pin.it",
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
            detail="Hourly download limit reached. Try later."
        )

    recent.append(now)
    requests_by_ip[ip] = recent


@app.get("/")
def home():
    return {
        "service": "Social Downloader API",
        "status": "online",
        "version": "0.2.0"
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "status": "online"
    }


@app.post("/api/download")
def download_video(
    payload: DownloadRequest,
    request: Request
):
    url = str(payload.url)
    parsed = urlparse(url)

    # URL validation
    if (
        parsed.scheme not in ("http", "https")
        or not host_is_allowed(parsed.hostname)
    ):
        raise HTTPException(
            status_code=400,
            detail="Unsupported website URL."
        )

    # Rate limit
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(ip)

    temp_dir = tempfile.mkdtemp(prefix="socialdl_")
    output = str(
        Path(temp_dir) / "%(id)s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--max-filesize",
        str(MAX_FILE_BYTES),
        "--socket-timeout",
        "30",
        "--retries",
        "2",
        "--format",
        "best[ext=mp4]/best",
        "--output",
        output,
        url
    ]

    try:
        logger.info("Starting download: %s", parsed.hostname)

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

        # Download failed
        if result.returncode != 0 or not candidates:

            error_text = (
                result.stderr
                or result.stdout
                or "Unknown yt-dlp error"
            )

            error_text = error_text[-1500:]

            logger.error(
                "YT-DLP ERROR: %s",
                error_text
            )

            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Download failed",
                    "reason": error_text
                }
            )

        # Select downloaded file
        video = max(
            candidates,
            key=lambda p: p.stat().st_size
        )

        file_size = video.stat().st_size

        if file_size > MAX_FILE_BYTES:
            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=413,
                detail="Video exceeds the 300 MB limit."
            )

        safe_filename = re.sub(
            r"[^A-Za-z0-9._-]",
            "_",
            video.name
        )

        logger.info(
            "Download successful: %s (%s bytes)",
            safe_filename,
            file_size
        )

        return FileResponse(
            path=str(video),
            media_type="application/octet-stream",
            filename=safe_filename,
            background=BackgroundTask(
                shutil.rmtree,
                temp_dir,
                ignore_errors=True
            )
        )

    except subprocess.TimeoutExpired:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        logger.error("Download timed out")

        raise HTTPException(
            status_code=504,
            detail="Download timed out. Try a shorter video."
        )

    except HTTPException:
        raise

    except Exception:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        logger.exception("Unexpected server error")

        raise HTTPException(
            status_code=500,
            detail="Internal server error."
        )
