import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app


def client_for(monkeypatch, handler):
    monkeypatch.setenv("MINERU_API_URL", "http://mineru-api:8000")
    monkeypatch.setenv("POLL_INTERVAL", "0.001")
    return TestClient(create_app(httpx.MockTransport(handler)))


def test_full_upload_poll_markdown(monkeypatch):
    calls = []

    def handler(r):
        calls.append((r.method, r.url.path))
        path = r.url.path
        if path == "/v1/uploads":
            assert json.loads(r.content)["bytes"] == 3
            return httpx.Response(
                200, json={"id": "u1", "status": "pending", "upload_url": "/v1/uploads/u1/content", "upload_method": "PUT"}
            )
        if path == "/v1/uploads/u1/content":
            assert r.content == b"PDF"
            return httpx.Response(204)
        if path.endswith("/complete"):
            return httpx.Response(200, json={"status": "completed", "file": {"id": "f1"}})
        if path == "/v1/parse/jobs":
            payload = json.loads(r.content)
            assert payload["tier"] == "standard"
            assert payload["ocr_mode"] == "ocr"
            assert payload["files"][0]["page_range"] == "1-3"
            return httpx.Response(200, json={"job_id": "j1", "status": "queued"})
        if path == "/v1/parse/jobs/j1":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "files": [
                        {
                            "status": "completed",
                            "parse": {"parser_version": "4.0.4"},
                            "output_files": {"markdown": {"file_id": "m1"}},
                        }
                    ],
                },
            )
        if path == "/v1/files/m1/content":
            return httpx.Response(200, text="# 测试正文")
        raise AssertionError(path)

    with client_for(monkeypatch, handler) as client:
        response = client.post(
            "/file_parse",
            files={"files": ("test.pdf", b"PDF")},
            data={"return_md": "true", "enable_ocr": "true", "start_page_id": "0", "end_page_id": "2"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["results"]["test"]["md_content"] == "# 测试正文"
    assert len(calls) == 6


@pytest.mark.parametrize("state", ["failed", "partial", "canceled"])
def test_failed_job_not_success(monkeypatch, state):
    def handler(r):
        if r.url.path == "/v1/uploads":
            return httpx.Response(200, json={"id": "u", "status": "completed", "file": {"id": "f"}})
        return httpx.Response(200, json={"job_id": "j", "status": state, "files": [{"error": {"message": "bad PDF"}}]})

    with client_for(monkeypatch, handler) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"x")})
    assert response.status_code == 502
    assert state in response.text


def test_reject_cross_origin_upload(monkeypatch):
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(200, json={"id": "u", "status": "pending", "upload_url": "http://other/steal"})

    with client_for(monkeypatch, handler) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"x")})
    assert response.status_code == 502
    assert len(calls) == 1


@pytest.mark.parametrize(
    "params", [{"enable_table": "false"}, {"unknown": "x"}, {"start_page_id": "-1"}, {"return_md": "false"}]
)
def test_bad_options_fail_before_upload(monkeypatch, params):
    def handler(r):
        raise AssertionError("must not contact upstream")

    with client_for(monkeypatch, handler) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"x")}, data=params)
    assert response.status_code == 422


def test_deadline_cancels_job(monkeypatch):
    monkeypatch.setenv("PARSE_TIMEOUT", "0.05")
    canceled = []

    async def handler(r):
        if r.method == "DELETE":
            canceled.append(r.url.path)
            return httpx.Response(200)
        if r.url.path == "/v1/uploads":
            return httpx.Response(200, json={"id": "u", "status": "completed", "file": {"id": "f"}})
        if r.method == "POST":
            return httpx.Response(200, json={"job_id": "j", "status": "queued"})
        await asyncio.sleep(1)
        return httpx.Response(200, json={"status": "running"})

    with client_for(monkeypatch, handler) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"x")})
    assert response.status_code == 504
    assert canceled == ["/v1/parse/jobs/j"]


def test_upstream_auth_and_http_error(monkeypatch):
    monkeypatch.setenv("MINERU_API_KEY", "test-secret")

    def handler(r):
        assert r.headers["Authorization"] == "Bearer test-secret"
        return httpx.Response(401, json={"error": "unauthorized"})

    with client_for(monkeypatch, handler) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"x")})
    assert response.status_code == 502
    assert "401" in response.text
    assert "test-secret" not in response.text


def test_size_limit(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "2")
    with client_for(monkeypatch, lambda r: pytest.fail("unexpected upload")) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"123")})
    assert response.status_code == 413


@pytest.mark.parametrize("body", ["not json", "{}"])
def test_malformed_upstream(monkeypatch, body):
    with client_for(monkeypatch, lambda r: httpx.Response(200, text=body)) as client:
        response = client.post("/file_parse", files={"files": ("x.pdf", b"x")})
    assert response.status_code == 502


def test_options_mapping():
    from app import parse_options

    assert parse_options({"start_page_id": "3"}, "basic")["page_range"] == "4-r1"
    assert parse_options({"tier": "flash", "parse_method": "txt"}, "standard")["ocr_mode"] == "txt"
    assert parse_options({}, "standard")["page_range"] == "all"
    with pytest.raises(ValueError):
        parse_options({"backend": "pipeline"}, "standard")


def test_non_pdf_omits_page_range(monkeypatch):
    def handler(r):
        if r.url.path == "/v1/uploads":
            return httpx.Response(200, json={"id": "u", "status": "completed", "file": {"id": "f"}})
        payload = json.loads(r.content)
        assert "page_range" not in payload["files"][0]
        return httpx.Response(200, json={"job_id": "j", "status": "failed", "files": []})

    with client_for(monkeypatch, handler) as client:
        response = client.post("/file_parse", files={"files": ("x.html", b"<p>test</p>")})
    assert response.status_code == 502
