# MinerU Open WebUI Compat

**English** | [简体中文](README.zh-CN.md)

**A `/file_parse` bridge for Open WebUI installations that do not yet support the MinerU 4 API.**

Open WebUI v0.11.1's local MinerU integration still uploads documents to the legacy `POST /file_parse` endpoint and expects Markdown in a synchronous response. MinerU 4 removed that endpoint and introduced a V1 workflow: **upload → create a parse job → poll → download results**. The two interfaces are incompatible; changing the API URL alone does not resolve the mismatch.

This project exists to bridge that gap. It exposes the `/file_parse` interface Open WebUI expects, calls an existing MinerU 4 V1 service, and returns the Markdown in the legacy `results.<filename>.md_content` response format.

```text
Open WebUI
    │ POST /file_parse (synchronous file upload)
    ▼
Compatibility bridge (CPU only, no models)
    │ V1 upload → parse job → poll → download Markdown
    ▼
Existing MinerU 4 API → inference models / GPU
```

No Open WebUI source changes or additional model instances are needed. **An existing, working MinerU 4 API is required.** This repository does not install or start inference models and does not implement the entire MinerU 3 API. The compatibility finding applies to the inspected Open WebUI v0.11.1 integration; a future client with native MinerU 4 V1 support can connect directly without this bridge.

## Tested compatibility

- Official Open WebUI **v0.11.1 MinerULoader** → MinerU **4.0.4**.
- 16 protocol tests and offline, end-to-end Flash parsing of PDF and HTML documents.
- This does not certify every Open WebUI/MinerU release, the complete browser-to-knowledge-base flow, or GPU Standard inference. See the [validation record (Chinese)](VALIDATION.md).

## Quick start

```bash
git clone https://github.com/AtongWang/mineru-openwebui-compat.git
cd mineru-openwebui-compat
cp .env.example .env
# Edit .env to point to your existing MinerU 4 API.
docker compose build
docker compose up -d --no-build
docker compose logs -f
```

By default, the bridge connects to MinerU on the Docker host at port `12000`, for example when MinerU publishes `12000:8000`. Docker's `host-gateway` points to the host; an upstream listening only on `127.0.0.1` is not reachable through that gateway. Configure an address reachable from the bridge container.

If both containers share a Docker network, use `http://mineru-api:8000` and attach the bridge to that network. Separate Compose projects do not automatically share a network.

The bridge publishes port `12001`. It needs Docker Compose but no GPU allocation. MinerU's own WebUI can continue connecting directly to the native MinerU API.

## Configure Open WebUI

In the admin settings for documents/content extraction:

| Setting | Value |
| --- | --- |
| Extraction engine | MinerU |
| API mode | Local |
| API URL | `http://SERVER_IP:12001` |
| Timeout | `1860` seconds |
| Parameters | `{"tier":"standard"}` |

Remove old `backend` and `model_version` parameters. Do not append `/v1` or `/file_parse` to the API URL. Start with a PDF to verify the integration.

Inside a container, `localhost` refers to that container. Use a server address reachable from Open WebUI. For existing deployments, save these settings in the admin interface: stored database settings may take precedence over environment variables.

Environment variables for a new Open WebUI deployment:

```yaml
CONTENT_EXTRACTION_ENGINE: mineru
MINERU_API_MODE: local
MINERU_API_URL: http://SERVER_IP:12001
MINERU_API_TIMEOUT: "1860"
MINERU_PARAMS: '{"tier":"standard"}'
```

## Verify the connection

```bash
curl -f http://localhost:12001/health
curl -f http://localhost:12001/file_parse \
  -F 'files=@document.pdf' -F 'return_md=true' -F 'tier=standard'
```

A successful response contains `results.<filename>.md_content`. Next, upload a real PDF in Open WebUI and check the extracted text and knowledge-base retrieval. The health endpoint checks upstream availability, not successful model inference.

## Parameters and limitations

- `tier`: `flash`, `basic`, `standard`, or `advanced`; defaults to `standard`. The upstream server must support the selected tier.
- `parse_method`: `auto`, `txt`, or `ocr`; `enable_ocr=true` forces OCR.
- `start_page_id` / `end_page_id`: converts legacy zero-based, inclusive page indices to V1 one-based ranges. An omitted end index or `99999` means the last page.
- Direct bridge requests support `page_ranges`. Open WebUI v0.11.1's Local loader removes that parameter itself; use `start_page_id` / `end_page_id` in Open WebUI instead.
- `return_md=true`; formula/table switches accept only `true`, because the V1 request does not expose corresponding disable options.
- `language` / `lang_list`: V1 has no corresponding request field. The bridge logs a warning and uses automatic language detection.
- `backend`, `model_version`, and unknown options return HTTP 422 rather than silently mapping old backends to new tiers.
- Single-file Markdown only. Extracted images are not registered as Open WebUI image attachments. Legacy ZIP, JSON, and batch interfaces are not implemented.
- Defaults: 200 MiB per upload and 16 MiB of Markdown. Configure `MAX_UPLOAD_BYTES` / `MAX_MARKDOWN_BYTES` to change these limits. Upload size is checked after multipart reception, with files spooled by the framework; an ingress proxy can enforce an earlier request-body limit.
- Failed, partial, or canceled jobs return HTTP 502. Timeouts return HTTP 504 and trigger a best-effort cancellation of an unfinished job. A browser disconnect does not guarantee immediate upstream cancellation.
- Same-origin, self-hosted uploads only; cloud presigned uploads and cross-origin download redirects are not supported.
- The bridge does not persist a job index. Upstream files follow MinerU's retention behavior. In-flight requests are not guaranteed to survive restarts, and POST requests are not automatically retried to avoid duplicate inference.
- Set the bridge's `MINERU_API_KEY` if the upstream requires authentication. The compatibility endpoint itself has no authentication: Open WebUI v0.11.1's Local loader does not send that key. Deploy on a trusted network; this is not a public authentication gateway.
- If a reverse proxy is present, its timeout must also accommodate parsing time.

## Offline deployment

Build and export on an internet-connected machine:

```bash
docker compose build
mkdir -p offline
docker save -o offline/mineru-compat-1.0.0.tar mineru-compat:1.0.0
cp compose.yaml .env.example offline/
(cd offline && sha256sum mineru-compat-1.0.0.tar > SHA256SUMS)
```

Copy `offline/` to the offline server and run these commands inside it:

```bash
sha256sum -c SHA256SUMS
docker load -i mineru-compat-1.0.0.tar
cp .env.example .env
# Set MINERU_API_URL in .env to the reachable upstream address.
docker compose up -d --no-build --pull never
```

All bridge dependencies are already in the image. The offline server does not run pip or rebuild it. Transfer the MinerU image with its models, the Open WebUI image, and any data volumes separately; this image export does not include them.

## Development and tests

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock pytest==8.4.2
.venv/bin/python -m pytest -q
docker compose config --quiet
```

Dependencies are pinned in `requirements.lock`, and the Python base image is pinned by digest. GitHub Actions runs protocol tests and Compose validation without starting model services.

## Protocol references

- [Open WebUI v0.11.1 MinerULoader](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/retrieval/loaders/mineru.py)
- [MinerU V1 HTTP API example](https://github.com/opendatalab/MinerU/blob/master/scripts/http_api_example.sh)

This is an independent compatibility project, not an official MinerU or Open WebUI component.
