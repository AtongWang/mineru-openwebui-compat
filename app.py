"""Open WebUI's single-file legacy contract over a self-hosted MinerU V1 API."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.datastructures import UploadFile


def parse_options(form: dict[str, str], default_tier: str) -> dict:
    allowed = {
        "return_md",
        "tier",
        "backend",
        "model_version",
        "parse_method",
        "enable_ocr",
        "enable_formula",
        "enable_table",
        "formula_enable",
        "table_enable",
        "language",
        "lang_list",
        "start_page_id",
        "end_page_id",
        "page_ranges",
    }
    unknown = set(form) - allowed
    if unknown:
        raise ValueError(f"Unsupported options: {sorted(unknown)}")
    for key in ("return_md", "enable_formula", "enable_table", "formula_enable", "table_enable"):
        if key in form and form[key].lower() not in ("true", "1"):
            raise ValueError(f"{key}=false is not supported by this Markdown adapter")
    if form.get("language") or form.get("lang_list"):
        logging.getLogger(__name__).warning("MinerU V1 has no language hint; using automatic language detection")
    tier = form.get("tier", default_tier)
    # Legacy backend selection is not equivalent to V1 tiers. Require explicit migration.
    for key in ("backend", "model_version"):
        if form.get(key):
            raise ValueError(f"Remove {key}; use tier=basic/standard/advanced instead")
    if tier not in ("flash", "basic", "standard", "advanced"):
        raise ValueError("Invalid tier")
    mode = form.get("parse_method", "auto")
    if "enable_ocr" in form:
        if form["enable_ocr"].lower() not in ("true", "false", "0", "1"):
            raise ValueError("enable_ocr must be boolean")
        if form["enable_ocr"].lower() in ("true", "1"):
            mode = "ocr"
    if mode not in ("auto", "txt", "ocr"):
        raise ValueError("Invalid parse_method")
    pages = form.get("page_ranges", "all") or "all"
    if "start_page_id" in form or "end_page_id" in form:
        if form.get("page_ranges"):
            raise ValueError("Do not combine page_ranges and start/end_page_id")
        start = int(form.get("start_page_id", "0"))
        end = int(form.get("end_page_id", "99999"))
        if start < 0 or end < start:
            raise ValueError("Invalid zero-based inclusive page range")
        pages = f"{start + 1}-r1" if end == 99999 else f"{start + 1}-{end + 1}"
    return {"tier": tier, "ocr_mode": mode, "page_range": pages}


def create_app(transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Configure at startup; the adapter never imports MinerU or GPU libraries."""
    base = os.environ.get("MINERU_API_URL", "http://mineru-api:8000").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("MINERU_API_URL must be an HTTP(S) base URL without credentials/query/fragment")
    timeout = float(os.environ.get("PARSE_TIMEOUT", "1800"))
    interval = float(os.environ.get("POLL_INTERVAL", "1"))
    max_bytes = int(os.environ.get("MAX_UPLOAD_BYTES", "209715200"))
    max_markdown = int(os.environ.get("MAX_MARKDOWN_BYTES", "16777216"))
    default_tier = os.environ.get("MINERU_TIER", "standard")
    if timeout <= 0 or interval <= 0 or max_bytes <= 0 or max_markdown <= 0:
        raise ValueError("Timeouts and size limits must be positive")
    parse_options({}, default_tier)
    key = os.environ.get("MINERU_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    app = FastAPI(title="MinerU 4 compatibility adapter", version="1.0.0")

    def origin(url: str) -> tuple:
        parts = urlsplit(url)
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    @app.get("/health")
    async def health() -> dict:
        try:
            async with httpx.AsyncClient(transport=transport, headers=headers, timeout=5, trust_env=False) as client:
                response = await client.get(f"{base}/v1/health")
                response.raise_for_status()
            return {"status": "healthy"}
        except httpx.HTTPError as exc:
            raise HTTPException(503, "MinerU upstream is unavailable") from exc

    @app.post("/file_parse")
    async def file_parse(request: Request) -> dict:
        async with request.form(max_files=1, max_fields=32) as form:
            files = form.getlist("files")
            if len(files) != 1 or not isinstance(files[0], UploadFile):
                raise HTTPException(422, "Exactly one multipart file named files is required")
            upload = files[0]
            if not upload.size or upload.size > max_bytes:
                raise HTTPException(413, f"File must contain 1..{max_bytes} bytes")
            try:
                options = parse_options({k: str(v) for k, v in form.items() if k != "files"}, default_tier)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            filename = PurePosixPath((upload.filename or "document.pdf").replace("\\", "/")).name
            job_id = None
            terminal = False
            async with httpx.AsyncClient(transport=transport, headers=headers, timeout=60, trust_env=False) as client:

                async def run() -> dict:
                    nonlocal job_id, terminal
                    response = await client.post(
                        f"{base}/v1/uploads",
                        json={
                            "filename": filename,
                            "bytes": upload.size,
                            "mime_type": upload.content_type or "application/octet-stream",
                            "purpose": "parse",
                        },
                    )
                    response.raise_for_status()
                    state = response.json()
                    if state["status"] == "pending":
                        target = urljoin(base + "/", state["upload_url"])
                        if origin(target) != origin(base) or urlsplit(target).username:
                            raise ValueError("Only same-origin self-hosted upload URLs are supported")
                        if state.get("upload_method", "PUT") != "PUT":
                            raise ValueError("Unsupported upload method")

                        async def chunks() -> AsyncIterator[bytes]:
                            while chunk := await upload.read(1024 * 1024):
                                yield chunk

                        upload_headers = dict(state.get("upload_headers") or {})
                        upload_headers["Content-Length"] = str(upload.size)
                        response = await client.put(target, content=chunks(), headers=upload_headers)
                        response.raise_for_status()
                        response = await client.post(f"{base}/v1/uploads/{quote(state['id'], safe='')}/complete")
                        response.raise_for_status()
                        state = response.json()
                    if state["status"] != "completed":
                        raise ValueError("Upload did not complete")
                    response = await client.post(
                        f"{base}/v1/parse/jobs",
                        json={
                            "files": [
                                {
                                    "source": {"type": "file_id", "file_id": state["file"]["id"]},
                                    **({"page_range": options["page_range"]} if options["page_range"] != "all" else {}),
                                }
                            ],
                            "tier": options["tier"],
                            "ocr_mode": options["ocr_mode"],
                            "output_formats": ["markdown"],
                        },
                    )
                    response.raise_for_status()
                    job = response.json()
                    job_id = quote(job["job_id"], safe="")
                    while job["status"] in ("queued", "running"):
                        await asyncio.sleep(interval)
                        response = await client.get(f"{base}/v1/parse/jobs/{job_id}")
                        response.raise_for_status()
                        job = response.json()
                    terminal = job["status"] in ("completed", "partial", "failed", "canceled")
                    if job["status"] != "completed":
                        raise HTTPException(
                            502,
                            {
                                "message": "MinerU job did not complete",
                                "job_id": job_id,
                                "status": job["status"],
                                "files": job.get("files", []),
                            },
                        )
                    results = job["files"]
                    if len(results) != 1 or results[0]["status"] != "completed":
                        raise ValueError("Expected exactly one completed file")
                    result = results[0]
                    output_id = quote(result["output_files"]["markdown"]["file_id"], safe="")
                    data = bytearray()
                    async with client.stream("GET", f"{base}/v1/files/{output_id}/content") as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > max_markdown:
                                raise ValueError("Markdown exceeds MAX_MARKDOWN_BYTES")
                    markdown = data.decode("utf-8")
                    if not markdown.strip():
                        raise ValueError("MinerU returned empty Markdown")
                    return {
                        "backend": options["tier"],
                        "version": (result.get("parse") or {}).get("parser_version", "4"),
                        "results": {PurePosixPath(filename).stem: {"md_content": markdown}},
                    }

                try:
                    return await asyncio.wait_for(run(), timeout=timeout)
                except (TimeoutError, httpx.TimeoutException) as exc:
                    raise HTTPException(504, {"message": "MinerU parse timed out", "job_id": job_id}) from exc
                except httpx.HTTPStatusError as exc:
                    raise HTTPException(
                        502,
                        {
                            "message": "MinerU upstream HTTP error",
                            "status": exc.response.status_code,
                            "body": exc.response.text[:2000],
                        },
                    ) from exc
                except httpx.RequestError as exc:
                    raise HTTPException(502, "Cannot reach MinerU upstream") from exc
                except (ValueError, KeyError, TypeError, IndexError) as exc:
                    raise HTTPException(502, f"Invalid MinerU response: {exc}") from exc
                finally:
                    if job_id and not terminal:
                        try:
                            await client.delete(f"{base}/v1/parse/jobs/{job_id}", timeout=5)
                        except httpx.HTTPError:
                            logging.getLogger(__name__).warning("Could not cancel job %s", job_id)

    return app
