# MinerU Open WebUI Compat

**English** | [简体中文](README.zh-CN.md)

**Open WebUI does not support the MinerU 4 API. This service is the `/file_parse` bridge that makes document parsing work anyway.**

## Why this exists

Open WebUI's MinerU integration in Local mode uploads a document to `POST /file_parse` and reads Markdown from the synchronous response. MinerU 3 served that endpoint from `projects/web_api/app.py`. MinerU 4 removed it and introduced the V1 asynchronous workflow. The two protocols do not line up at any point:

| | Open WebUI sends (Local mode) | MinerU 4 exposes (V1) |
| --- | --- | --- |
| Endpoint | `POST /file_parse` | `POST /v1/uploads`, `POST /v1/parse/jobs` |
| Shape | One synchronous multipart request | Upload → create job → poll → download output |
| Response | `results.<filename>.md_content` | Job status plus output file IDs |
| Model selection | `backend`, `model_version` | `tier` |

Pointing Open WebUI at a MinerU 4 server therefore fails: changing the API URL cannot convert one request into a four-step exchange. Editing Open WebUI's source is the alternative, and it has to be redone on every upgrade.

This bridge closes the gap from the outside. It serves the `/file_parse` interface Open WebUI already knows, performs the V1 exchange against an existing MinerU 4 service, and returns the Markdown in the legacy response shape.

```text
Open WebUI
    │ POST /file_parse (synchronous multipart upload)
    ▼
This bridge (CPU only, no models, no GPU)
    │ V1 upload → parse job → poll → download Markdown
    ▼
Your existing MinerU 4 API → inference models / GPU
```

Neither side is modified. No second model instance is loaded.

### Scope

**Requires an existing, working MinerU 4 API.** This repository does not install, download, or start inference models, and it is not a full reimplementation of the MinerU 3 API — it covers the single Markdown path Open WebUI's Local mode actually uses. Once Open WebUI supports the MinerU 4 V1 API natively, it can connect directly and this bridge becomes unnecessary.

## Tested compatibility

- Official Open WebUI **v0.11.1 `MinerULoader`** → MinerU **4.0.4**, end to end.
- `backend/open_webui/retrieval/loaders/mineru.py` is **byte-identical from v0.11.1 through v0.11.3** (latest release, 2026-08-31), so the same mismatch and the same fix apply across all of them.
- 16 protocol tests, plus offline end-to-end Flash parsing of PDF and HTML.
- Not certified for every Open WebUI/MinerU release, the full browser-to-knowledge-base flow, or GPU Standard/VLM inference. See the [validation record](VALIDATION.md).

## Quick start

```bash
git clone https://github.com/AtongWang/mineru-openwebui-compat.git
cd mineru-openwebui-compat
cp .env.example .env
# Edit .env to point MINERU_API_URL at your existing MinerU 4 API.
docker compose build
docker compose up -d --no-build
docker compose logs -f
```

The bridge publishes port `12001` and needs no GPU allocation. MinerU's own WebUI can keep connecting directly to the native API.

By default it reaches MinerU on the Docker host at port `12000`, matching a MinerU that publishes `12000:8000`. Docker's `host-gateway` resolves to the host, so an upstream bound only to `127.0.0.1` is **not** reachable through it — bind it to a reachable address instead. If both containers share a Docker network, set `MINERU_API_URL=http://mineru-api:8000` and attach this service to that network; separate Compose projects do not share a network automatically.

## Configure Open WebUI

Admin Settings → Documents → content extraction:

| Setting | Value |
| --- | --- |
| Extraction engine | MinerU |
| API mode | Local |
| API URL | `http://SERVER_IP:12001` |
| Timeout | `1860` seconds |
| Parameters | `{"tier":"standard"}` |

- Do **not** append `/v1` or `/file_parse` to the API URL.
- Remove any old `backend` and `model_version` parameters; they now return 422 by design.
- Raise the timeout. Open WebUI defaults to `300` seconds, which is short for real documents; `1860` covers the bridge's 1800-second budget plus upload and response time.
- Inside a container `localhost` means that container — use an address reachable from Open WebUI.
- Save these in the admin interface on an existing deployment: stored database settings can take precedence over environment variables.
- Verify with a PDF first. Other file types need Open WebUI's MinerU file-extension setting widened.

Environment variables for a fresh Open WebUI deployment:

```yaml
CONTENT_EXTRACTION_ENGINE: mineru
MINERU_API_MODE: local
MINERU_API_URL: http://SERVER_IP:12001
MINERU_API_TIMEOUT: "1860"
MINERU_PARAMS: '{"tier":"standard"}'
```

## Verify

```bash
curl -f http://localhost:12001/health
curl -f http://localhost:12001/file_parse \
  -F 'files=@document.pdf' -F 'return_md=true' -F 'tier=standard'
```

A successful parse returns `results.<filename>.md_content`. Then upload a real PDF through Open WebUI and check both the extracted text and knowledge-base retrieval. `/health` only confirms the upstream is reachable — it does not exercise model inference.

## Parameters and limitations

- `tier`: `flash`, `basic`, `standard`, `advanced`; defaults to `standard`. The upstream server must actually serve the tier you pick.
- `parse_method`: `auto`, `txt`, `ocr`. `enable_ocr=true` forces `ocr`.
- `start_page_id` / `end_page_id`: legacy zero-based inclusive indices are converted to V1 one-based ranges. An omitted end index, or `99999`, means the last page.
- `page_ranges` works on direct bridge calls only. Open WebUI's Local loader strips that parameter itself, so use `start_page_id` / `end_page_id` there.
- `return_md=true` required. Formula and table switches accept `true` only, because the V1 request exposes no corresponding disable option.
- `language` / `lang_list`: no V1 equivalent. The bridge logs a warning and relies on automatic language detection.
- `backend`, `model_version`, and unknown options return **422** instead of silently mapping legacy backend names onto new tiers.
- Single-file Markdown only. Extracted images are not registered as Open WebUI image attachments, and the legacy ZIP, JSON, and batch interfaces are not implemented.
- Limits default to 200 MiB per upload and 16 MiB of Markdown, tunable via `MAX_UPLOAD_BYTES` and `MAX_MARKDOWN_BYTES`. Upload size is checked after multipart reception, with the file spooled to disk by the framework; an ingress proxy can enforce an earlier request-body limit.
- Failed, partial, and canceled jobs return **502**. Timeouts return **504** and trigger best-effort job cancellation. A browser disconnect does not guarantee immediate upstream cancellation.
- Same-origin self-hosted uploads only. Cloud presigned uploads and cross-origin download redirects are rejected.
- No job index is persisted. Upstream files follow MinerU's own retention behavior, in-flight requests are not guaranteed to survive a restart, and POSTs are never auto-retried — a retry would mean duplicate inference.
- Set `MINERU_API_KEY` if your upstream requires authentication. The bridge endpoint itself is unauthenticated, because Open WebUI's Local loader sends no key: deploy it on a trusted network. **This is not a public authentication gateway.**
- Any reverse proxy in front of either service needs a timeout that covers full parse duration.

## Offline deployment

Build and export on a connected machine:

```bash
docker compose build
mkdir -p offline
docker save -o offline/mineru-compat-1.0.0.tar mineru-compat:1.0.0
cp compose.yaml .env.example offline/
(cd offline && sha256sum mineru-compat-1.0.0.tar > SHA256SUMS)
```

Copy `offline/` to the target server and run inside it:

```bash
sha256sum -c SHA256SUMS
docker load -i mineru-compat-1.0.0.tar
cp .env.example .env
# Set MINERU_API_URL to the reachable upstream address.
docker compose up -d --no-build --pull never
```

Every dependency is baked into the image, so the offline host runs no `pip` and rebuilds nothing. The MinerU image with its models, the Open WebUI image, and all data volumes must be transferred separately — this export contains none of them.

## Development

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock pytest==8.4.2
.venv/bin/python -m pytest -q
docker compose config --quiet
```

Dependencies are pinned in `requirements.lock` and the Python base image is pinned by digest. GitHub Actions runs the protocol tests and Compose validation without starting any model service.

## References

- [Open WebUI v0.11.3 `MinerULoader`](https://github.com/open-webui/open-webui/blob/v0.11.3/backend/open_webui/retrieval/loaders/mineru.py) — the legacy `/file_parse` client this bridge serves
- [MinerU V1 HTTP API example](https://github.com/opendatalab/MinerU/blob/master/scripts/http_api_example.sh) — the upstream protocol it speaks

An independent compatibility project, not an official MinerU or Open WebUI component.
