# MinerU Open WebUI Compat

A lightweight CPU-only bridge between Open WebUI's legacy `/file_parse` loader and the self-hosted MinerU 4 V1 API. No GPU, model downloads, or changes to Open WebUI are required for the adapter itself.

将 Open WebUI 的旧版同步接口转换为 MinerU 4 的上传、解析任务、轮询和 Markdown 下载流程。独立运行，不依赖 MinerU 源码仓库。**需已有可用的 MinerU 4 API 服务**；本仓库不启动或安装推理模型。

```text
Open WebUI → compat:12001 → MinerU 4 API:12000 → GPU
MinerU 自带 WebUI ──────────↗
```

## 已验证版本

- Open WebUI **v0.11.1 官方 MinerULoader** → MinerU **4.0.4**。
- 16 项协议测试；PDF/HTML 经过断网 Flash 端到端解析。
- 尚未验证所有 Open WebUI/MinerU 版本，也不等同于完整浏览器入库和 GPU Standard 推理验收。详见 [验证记录](VALIDATION.md)。

## 快速部署

```bash
git clone https://github.com/AtongWang/mineru-openwebui-compat.git
cd mineru-openwebui-compat
cp .env.example .env
# 编辑 .env，填写已有 MinerU 4 API 地址。
docker compose build
docker compose up -d --no-build
docker compose logs -f
```

默认连接同一宿主机发布在 12000 端口上的 MinerU，例如其端口映射为 `12000:8000`。Docker 的 host-gateway 指向宿主机；若上游仅监听 127.0.0.1，容器无法通过该网关访问，请配置容器可达地址。若两个服务共享 Docker 网络，可改用 `http://mineru-api:8000`，并将本服务加入该网络。不同 Compose 网络不会自动互通。

本服务发布在 12001 端口。只需要 Docker Compose，不需要为兼容容器分配 GPU。MinerU 原生 WebUI 继续直接连接 MinerU API。

## Open WebUI 设置

管理员设置 → 文档/内容提取：

| 配置 | 值 |
| --- | --- |
| 提取引擎 | MinerU |
| API 模式 | Local |
| API URL | `http://服务器IP:12001` |
| 超时 | `1860` 秒 |
| 参数 | `{"tier":"standard"}` |

清除原来的 `backend`、`model_version` 参数；URL 不要追加 `/v1` 或 `/file_parse`。初次验收使用 PDF。容器里的 localhost 指容器自身，应填写 Open WebUI 容器可达的服务器地址。已有部署请在管理界面保存配置，数据库设置可能优先于环境变量。

新 Open WebUI 部署可参考以下环境变量：

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

成功返回 `results.<文件名>.md_content`。随后在 Open WebUI 上传真实 PDF，检查提取文本及知识库检索。health 检查验证上游可达，不代表模型推理已通过。

## 参数与边界


- `tier`：flash/basic/standard/advanced，默认 standard；服务端需支持对应档位。
- `parse_method`：auto/txt/ocr；`enable_ocr=true` 强制 ocr。
- `start_page_id/end_page_id`：旧式从 0 开始、包含末页，转为 V1 从 1 开始；未指定末页或末页为 99999 时到最后一页。
- 直接调用兼容层支持 `page_ranges`；Open WebUI 0.11.1 Local loader 自身会移除该参数，需在其参数中使用 start/end_page_id。
- `return_md=true`；公式/表格开关只接受 true，V1 未提供对应关闭选项。
- `language/lang_list`：V1 无对应请求字段，记录警告并使用自动语言识别。
- `backend/model_version` 及未知选项返回 422，避免旧后端名称产生错误映射。
- 仅支持单文件 Markdown。不会把提取图片注册为 Open WebUI 图片附件，不提供旧 ZIP/JSON/批处理协议。
- 默认文件上限 200 MiB、Markdown 上限 16 MiB；通过 MAX_UPLOAD_BYTES/MAX_MARKDOWN_BYTES 修改。上传大小检查发生在 multipart 接收后，文件由框架暂存磁盘；暴露到外部时可在入口代理限制请求体。
- 失败/部分成功/取消返回 502，超时返回 504，并尝试取消已创建的未完成任务。浏览器断开不会保证立即取消服务端任务。
- 仅支持同源自托管文件上传，不支持云端预签名上传或跨域下载跳转。
- 兼容层不保存任务索引；上游文件按 MinerU 自身保留策略管理。进程重启不保证恢复在途请求，不自动重试 POST 避免重复推理。
- 上游启用鉴权时，为兼容容器设置 MINERU_API_KEY。兼容入口本身不加鉴权（Open WebUI 0.11.1 Local loader 不发送该 Key），应部署在可信内网；这不是公网鉴权网关。
- 反向代理如有配置，其超时也需覆盖解析时长。

## 离线迁移

联网机器先构建镜像，再导出：

```bash
docker compose build
mkdir -p offline
docker save -o offline/mineru-compat-1.0.0.tar mineru-compat:1.0.0
cp compose.yaml .env.example offline/
(cd offline && sha256sum mineru-compat-1.0.0.tar > SHA256SUMS)
```

复制 `offline/` 到离线服务器，执行：

```bash
sha256sum -c SHA256SUMS
docker load -i mineru-compat-1.0.0.tar
cp .env.example .env
# 修改 .env 中的 MINERU_API_URL。
docker compose up -d --no-build --pull never
```

依赖已装入镜像，离线端不运行 pip、不重新构建。单独迁移 MinerU 的含模型镜像，以及 Open WebUI 镜像和数据卷。本项目的 `docker save` 不包含它们，也不包含数据卷。

## 开发与测试

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock pytest==8.4.2
.venv/bin/python -m pytest -q
docker compose config --quiet
```

依赖锁定在 `requirements.lock`，Python 基础镜像固定 digest。GitHub Actions 运行协议测试和 Compose 校验，不启动模型服务。

## 协议依据

- [Open WebUI v0.11.1 MinerULoader](https://github.com/open-webui/open-webui/blob/v0.11.1/backend/open_webui/retrieval/loaders/mineru.py)
- [MinerU V1 HTTP API 示例](https://github.com/opendatalab/MinerU/blob/master/scripts/http_api_example.sh)

独立兼容项目，非 MinerU 或 Open WebUI 官方组件。
