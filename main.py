
import os
import time
import shutil
import tempfile
import subprocess
import logging

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel


# -----------------------------
# APP SETTINGS
# -----------------------------

app = FastAPI(
    title="Social Downloader API",
    version="0.3.0"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BLOGGER_ORIGIN = os.getenv("BLOGGER_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if BLOGGER_ORIGIN == "*" else [BLOGGER_ORIGIN],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------
# CONFIGURATION
# -----------------------------

MAX_FILE_BYTES = 300 * 1024 * 1024
DOWNLOAD_TIMEOUT = 180

DENO_PATH = "/opt/render/.deno/bin/deno"

ALLOWED_HOSTS = [
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
    "threads.net",
]

# Simple in-memory rate limiter
RATE_LIMIT = 10
RATE_WINDOW = 3600
request_history = {}


# -----------------------------
# REQUEST MODEL
# -----------------------------

class DownloadRequest(BaseModel):
    url: str


# -----------------------------
# URL VALIDATION
# -----------------------------

def validate_url(url: str):

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if parsed.scheme not in ("http", "https"):
            raise ValueError("Invalid URL scheme")

        if not hostname:
            raise ValueError("Invalid hostname")

        allowed = any(
            hostname == domain or hostname.endswith("." + domain)
            for domain in ALLOWED_HOSTS
        )

        if not allowed:
            raise ValueError("Unsupported website")

        return True

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid or unsupported URL"
        )


# -----------------------------
# RATE LIMIT
# -----------------------------

def check_rate_limit(ip: str):

    now = time.time()

    if ip not in request_history:
        request_history[ip] = []

    request_history[ip] = [
        t for t in request_history[ip]
        if now - t < RATE_WINDOW
    ]

    if len(request_history[ip]) >= RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later."
        )

    request_history[ip].append(now)


# -----------------------------
# CLEANUP
# -----------------------------

def cleanup_directory(directory: str):

    try:
        shutil.rmtree(directory, ignore_errors=True)
    except Exception as error:
        logger.error("Cleanup error: %s", error)


# -----------------------------
# HOME
# -----------------------------

@app.get("/")
def home():

    return {
        "name": "Social Downloader API",
        "version": "0.3.0",
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }


# -----------------------------
# HEALTH CHECK
# -----------------------------

@app.get("/health")
def health():

    return {
        "status": "healthy",
        "deno_exists": os.path.isfile(DENO_PATH)
    }


# -----------------------------
# DOWNLOAD API
# -----------------------------

@app.post("/api/download")
def download_video(
    data: DownloadRequest,
    request: Request,
    background_tasks: BackgroundTasks
):

    url = data.url.strip()

    # Validate URL
    validate_url(url)

    # Rate limit
    client_ip = request.client.host if request.client else "unknown"
    check_rate_limit(client_ip)

    # Check Deno
    if not os.path.isfile(DENO_PATH):
        logger.error("Deno not found: %s", DENO_PATH)

    # Temporary folder
    temp_dir = tempfile.mkdtemp(prefix="social_download_")

    output_template = os.path.join(
        temp_dir,
        "%(id)s.%(ext)s"
    )

    # yt-dlp command
    command = [
        "yt-dlp",

        "--js-runtimes",
        f"deno:{DENO_PATH}",

        "--no-warnings",
        "--no-playlist",
        "--no-part",

        "--max-filesize",
        str(MAX_FILE_BYTES),

        "--socket-timeout",
        "30",

        "--retries",
        "2",

        "--format",
        "best[ext=mp4]/best",

        "--output",
        output_template,

        url
    ]

    try:

        logger.info("Starting download: %s", url)

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT
        )

        # Find downloaded file
        files = [
            f for f in Path(temp_dir).iterdir()
            if f.is_file()
            and not f.name.endswith((".part", ".ytdl"))
        ]

        if result.returncode != 0 or not files:

            error_message = (
                result.stderr or result.stdout or "Download failed"
            )

            logger.error("yt-dlp error: %s", error_message)

            cleanup_directory(temp_dir)

            raise HTTPException(
                status_code=422,
                detail=error_message[-2000:]
            )

        # Select largest output file
        downloaded_file = max(
            files,
            key=lambda f: f.stat().st_size
        )

        file_size = downloaded_file.stat().st_size

        # Check actual file size
        if file_size > MAX_FILE_BYTES:

            cleanup_directory(temp_dir)

            raise HTTPException(
                status_code=413,
                detail="Video is too large. Maximum allowed size is 300 MB."
            )

        if file_size == 0:

            cleanup_directory(temp_dir)

            raise HTTPException(
                status_code=422,
                detail="Downloaded file is empty."
            )

        # Return downloaded video
        background_tasks.add_task(
            cleanup_directory,
            temp_dir
        )

        return FileResponse(
            path=str(downloaded_file),
            filename=downloaded_file.name,
            media_type="application/octet-stream",
            background=background_tasks
        )

    except subprocess.TimeoutExpired:

        cleanup_directory(temp_dir)

        raise HTTPException(
            status_code=504,
            detail="Download timed out. Please try again."
        )

    except HTTPException:
        raise

    except Exception as error:

        cleanup_directory(temp_dir)

        logger.exception("Unexpected download error")

        raise HTTPException(
            status_code=500,
            detail=f"Server error: {str(error)}"
        )
