# MinerU Open WebUI Compat

[English](README.md) | **简体中文**

**Open WebUI 尚未适配 MinerU 4 接口。本服务作为 `/file_parse` 中转层，让文档解析照常可用。**

## 为什么需要它

Open WebUI 的 MinerU 集成在 Local 模式下将文档上传到 `POST /file_parse`，并从同步响应中读取 Markdown。MinerU 3 由 `projects/web_api/app.py` 提供该接口；MinerU 4 移除了它，改用 V1 异步流程。两套协议没有任何一处能对上：

| | Open WebUI 发出（Local 模式） | MinerU 4 提供（V1） |
| --- | --- | --- |
| 接口 | `POST /file_parse` | `POST /v1/uploads`、`POST /v1/parse/jobs` |
| 形式 | 单次同步 multipart 请求 | 上传 → 创建任务 → 轮询 → 下载结果 |
| 响应 | `results.<文件名>.md_content` | 任务状态与输出文件 ID |
| 模型选择 | `backend`、`model_version` | `tier` |

因此把 Open WebUI 直接指向 MinerU 4 一定失败：改 URL 无法把一次请求变成四步交互。另一种做法是改 Open WebUI 源码，但每次升级都要重做。

本项目从外部补齐这段差异：对 Open WebUI 提供它本来就认识的 `/file_parse`，对已有 MinerU 4 服务完成 V1 交互，再把 Markdown 按旧响应结构返回。

```text
Open WebUI
    │ POST /file_parse（同步 multipart 上传）
    ▼
本中转服务（仅 CPU，无模型，不占 GPU）
    │ V1 上传 → 解析任务 → 轮询 → 下载 Markdown
    ▼
已有的 MinerU 4 API → 推理模型 / GPU
```

两端都不需要改动，也不额外加载模型。

### 适用范围

**需已有可用的 MinerU 4 API 服务。** 本仓库不安装、不下载、不启动推理模型，也不是完整的 MinerU 3 API 实现 —— 只覆盖 Open WebUI Local 模式实际使用的那条 Markdown 路径。等 Open WebUI 原生支持 MinerU 4 V1 接口后即可直连，本中转层随之不再需要。

## 已验证版本

- Open WebUI **v0.11.1 官方 `MinerULoader`** → MinerU **4.0.4**，端到端验证。
- `backend/open_webui/retrieval/loaders/mineru.py` 在 **v0.11.1 至 v0.11.3（最新发布，2026-08-31）之间逐字节一致**，因此同一处不兼容与同一套解法适用于这些版本。
- 16 项协议测试，另有断网条件下 PDF/HTML 的 Flash 端到端解析。
- 未覆盖所有 Open WebUI/MinerU 版本，不等同于完整浏览器入库流程，也未验证 GPU Standard/VLM 推理。详见 [验证记录](VALIDATION.md)。

## 快速部署

```bash
git clone https://github.com/AtongWang/mineru-openwebui-compat.git
cd mineru-openwebui-compat
cp .env.example .env
# 编辑 .env，将 MINERU_API_URL 指向已有的 MinerU 4 API。
docker compose build
docker compose up -d --no-build
docker compose logs -f
```

本服务发布在 `12001` 端口，无需分配 GPU。MinerU 自带 WebUI 可继续直连原生 API。

默认连接宿主机 `12000` 端口上的 MinerU，对应其端口映射为 `12000:8000` 的情况。Docker 的 `host-gateway` 指向宿主机，因此仅监听 `127.0.0.1` 的上游**无法**经此网关访问，需改为监听可达地址。若两个容器共享 Docker 网络，可设 `MINERU_API_URL=http://mineru-api:8000` 并把本服务加入该网络；不同 Compose 项目的默认网络不会自动互通。

## Open WebUI 设置

管理员设置 → 文档 → 内容提取：

| 配置项 | 值 |
| --- | --- |
| 提取引擎 | MinerU |
| API 模式 | Local |
| API URL | `http://服务器IP:12001` |
| 超时 | `1860` 秒 |
| 参数 | `{"tier":"standard"}` |

- URL **不要**追加 `/v1` 或 `/file_parse`。
- 清除原有 `backend`、`model_version` 参数，它们现在按设计返回 422。
- 必须调高超时。Open WebUI 默认 `300` 秒，对真实文档偏短；`1860` 覆盖中转层 1800 秒预算并留出上传与响应时间。
- 容器内的 `localhost` 指容器自身，须填写 Open WebUI 可达的地址。
- 已有部署请在管理界面保存配置：数据库中的设置可能优先于环境变量。
- 先用 PDF 验收，其他类型需放宽 Open WebUI 的 MinerU 文件扩展名设置。

新部署可参考以下环境变量：

```yaml
CONTENT_EXTRACTION_ENGINE: mineru
MINERU_API_MODE: local
MINERU_API_URL: http://服务器IP:12001
MINERU_API_TIMEOUT: "1860"
MINERU_PARAMS: '{"tier":"standard"}'
```

## 检查

```bash
curl -f http://localhost:12001/health
curl -f http://localhost:12001/file_parse \
  -F 'files=@document.pdf' -F 'return_md=true' -F 'tier=standard'
```

解析成功会返回 `results.<文件名>.md_content`。随后在 Open WebUI 上传真实 PDF，检查提取文本与知识库检索。`/health` 只确认上游可达，不代表模型推理已通过。

## 参数与边界

- `tier`：`flash`/`basic`/`standard`/`advanced`，默认 `standard`；所选档位需服务端确实支持。
- `parse_method`：`auto`/`txt`/`ocr`；`enable_ocr=true` 强制 `ocr`。
- `start_page_id` / `end_page_id`：旧式从 0 开始、包含末页的索引，转为 V1 从 1 开始的范围。未指定末页或末页为 `99999` 时解析到最后一页。
- `page_ranges` 仅在直接调用中转层时有效。Open WebUI 的 Local loader 会自行移除该参数，在其中请改用 `start_page_id` / `end_page_id`。
- 必须 `return_md=true`；公式与表格开关只接受 `true`，V1 请求未提供对应的关闭选项。
- `language` / `lang_list`：V1 无对应字段，记录警告并使用自动语言识别。
- `backend`、`model_version` 及未知选项返回 **422**，而不是把旧后端名称错误映射到新档位。
- 仅支持单文件 Markdown。不会把提取的图片注册为 Open WebUI 图片附件，也不实现旧的 ZIP、JSON、批处理接口。
- 默认上限：单文件 200 MiB、Markdown 16 MiB，可通过 `MAX_UPLOAD_BYTES`、`MAX_MARKDOWN_BYTES` 调整。大小检查发生在 multipart 接收之后，文件由框架暂存磁盘；对外暴露时可在入口代理更早限制请求体。
- 失败、部分成功、已取消的任务返回 **502**；超时返回 **504** 并尽力取消任务。浏览器断开不保证上游立即取消。
- 仅支持同源自托管上传，拒绝云端预签名上传与跨域下载跳转。
- 不持久化任务索引。上游文件遵循 MinerU 自身的保留策略；进程重启不保证恢复在途请求；POST 一律不自动重试 —— 重试意味着重复推理。
- 上游启用鉴权时设置 `MINERU_API_KEY`。中转入口本身不鉴权，因为 Open WebUI 的 Local loader 不发送该 Key，请部署在可信内网。**这不是公网鉴权网关。**
- 任一侧前置反向代理的超时也必须覆盖完整解析时长。

## 离线部署

在联网机器构建并导出：

```bash
docker compose build
mkdir -p offline
docker save -o offline/mineru-compat-1.0.0.tar mineru-compat:1.0.0
cp compose.yaml .env.example offline/
(cd offline && sha256sum mineru-compat-1.0.0.tar > SHA256SUMS)
```

将 `offline/` 复制到目标服务器，在该目录中执行：

```bash
sha256sum -c SHA256SUMS
docker load -i mineru-compat-1.0.0.tar
cp .env.example .env
# 将 MINERU_API_URL 改为可达的上游地址。
docker compose up -d --no-build --pull never
```

依赖已全部打入镜像，离线端不运行 `pip`、不重新构建。含模型的 MinerU 镜像、Open WebUI 镜像及各数据卷需单独迁移，本次导出不包含它们。

## 开发

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock pytest==8.4.2
.venv/bin/python -m pytest -q
docker compose config --quiet
```

依赖锁定在 `requirements.lock`，Python 基础镜像固定 digest。GitHub Actions 运行协议测试与 Compose 校验，不启动任何模型服务。

## 协议依据

- [Open WebUI v0.11.3 `MinerULoader`](https://github.com/open-webui/open-webui/blob/v0.11.3/backend/open_webui/retrieval/loaders/mineru.py) —— 本服务所承接的旧版 `/file_parse` 客户端
- [MinerU V1 HTTP API 示例](https://github.com/opendatalab/MinerU/blob/master/scripts/http_api_example.sh) —— 本服务对上游所使用的协议

独立兼容项目，非 MinerU 或 Open WebUI 官方组件。
