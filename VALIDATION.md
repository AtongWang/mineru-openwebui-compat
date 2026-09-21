# 验证记录（2026-09-20）

- 单元/协议测试：16 passed。覆盖上传、排队轮询、结果转换、OCR/页码参数、非 PDF 默认不发送页码、失败/partial/canceled、畸形响应、鉴权转发、超时取消、跨源 URL 拒绝和文件大小限制。
- Ruff E/F 检查、格式化、git diff --check：通过。
- 最初的 MinerU 集成 Compose 和离线包 Compose 均校验通过；独立仓库 Compose 在发布前另行验证。
- Docker 镜像 mineru-compat:1.0.0：构建成功，依赖锁定，Python 基础镜像固定 digest；已导出镜像并通过 SHA256 复核。
- 真实 API 集成：本机 mineru:4，安装版本 4.0.4，以 --tier flash 启动。
- 真实客户端：从 open-webui/open-webui 的 v0.11.1 tag 下载原始 retrieval/loaders/mineru.py，不修改代码，在本机已有 Open WebUI 镜像的 Python 依赖环境中加载。
- 测试内容：生成的带文本层 PDF 和 HTML，分别经官方 MinerULoader → 兼容容器 → MinerU V1 API 返回非空、包含预期文本的 Document；返回 metadata 的 version 为 4.0.4。
- 网络：API 使用 --network none，兼容容器和客户端共享该容器网络命名空间，只通过 loopback 通信。运行时没有外网、没有 GPU。

边界：不是完整 Open WebUI 0.11.1 浏览器/知识库入库测试；没有在目标 A100/R535 上验证 Standard/VLM 推理。部署后需上传真实 PDF 完成该验收。测试容器已清理，不替用户启动正式服务。

独立仓库提取日期：2026-09-21。服务代码和协议测试保持原实现；Compose 改为连接已有上游，端到端记录来自提取前的同一实现。

## 协议差异复核（2026-09-21）

- 客户端侧：下载 Open WebUI v0.11.1 与 v0.11.3（当时最新发布，2026-08-31）的 `backend/open_webui/retrieval/loaders/mineru.py`，`diff` 结果逐字节一致。Local 模式仍为 `POST {api_url}/file_parse`，仍读取 `results.<文件名>.md_content`，仍在 `params` 中使用 `backend`/`model_version`，且自行 `pop` 掉 `page_ranges`。`MINERU_API_KEY` 只在 Cloud 分支作为 Authorization 头发送，Local 分支不发送。
- 客户端默认超时：v0.11.3 `config.py` 中 `MINERU_API_TIMEOUT` 默认 `300` 秒，故 README 明确要求调高。
- 服务端侧：本机 MinerU 4.0.4 的 `mineru/parser/api_server.py` 全文无 `file_parse`；V1 路由前缀为 `/v1`，含 `/uploads`、`/parse/jobs`、`/health`、`/tiers`。旧 `/file_parse` 出自 MinerU 3 的 `projects/web_api/app.py`（git 历史确认）。
- 档位名称：`mineru/parser/tier.py` 确认 flash/basic/standard/advanced 四档，与 README 所列一致。
- 本仓库协议测试：16 passed（10 个测试函数，含参数化用例）。

结论：不兼容存在于 Open WebUI 所有已发布版本（截至 v0.11.3），非 v0.11.1 个别版本问题；README 所述参数映射与边界与代码一致。
