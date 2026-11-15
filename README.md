# Social Downloader API (starter)

A small FastAPI + yt-dlp backend intended to connect to a Blogger front end.

## Important limitations
- Free hosting is limited and may sleep, throttle, or change its free-plan terms.
- This starter only attempts public links from supported extractors. It cannot guarantee every platform/link will work.
- It does not bypass private accounts, DRM, paywalls, login restrictions, or other access controls.
- Use only videos you own or have permission to download, and follow each platform's terms and applicable law.
- This starter has a basic in-memory per-IP rate limit. It is not production-grade abuse protection.
- Temporary files are used; no permanent video library is stored.

## Deploy on Render
1. Create a GitHub repository and upload `main.py`, `requirements.txt`, and `render.yaml`.
2. In Render, choose **New + > Blueprint** and connect the repository.
3. Deploy the free web service.
4. After deployment, open `https://YOUR-SERVICE.onrender.com/health`. It should return `{"ok":true}`.
5. Your API endpoint will be `https://YOUR-SERVICE.onrender.com/api/download`.

## Test
Send a POST request to `/api/download` with JSON:
`{"url":"https://www.facebook.com/..."}`
The response is the video file if the extractor can access it.

## Blogger
The front-end must POST JSON to the API endpoint. Do not put private API keys in Blogger HTML.
