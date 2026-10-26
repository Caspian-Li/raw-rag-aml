# 部署指南

GitHub 仅发布源码和固定 commit。GitHub Pages 不运行 Python 服务。下面命令留给实际取得服务器、域名和真实供应商配置之后执行；当前准备代码不等于已完成公网部署。

## 1. 服务器与凭据

使用 Linux x86_64、Python 3.12 或 Docker Engine + Compose 插件。部署单副本；持久化目录使用主机本地磁盘，不放在 NFS/共享网络盘。主机需要访问实际 embedding 和 Cohere 服务。

初期可按 4 vCPU、8–16 GiB 内存、至少 50 GiB 可用本地磁盘规划，再用真实容量测试调整。这是资源规划建议，不是本项目已经验证的 Full 容量。文本规模约 300M tokens 时，仅 1024 维 float64 窗口向量就可能达到约 10 GiB 量级，另外还需原文、SQLite/WAL 和运行内存空间。

复制 .env.example 为 .env，chmod 600 后填写：

- AML_MEMORY_KEY：高熵服务专用密钥，至少 32 字符。
- AML_EMBEDDING_API_KEY/BASE_URL：可调用 text-embedding-v4 的匹配区域密钥和 endpoint。
- AML_RERANK_API_KEY：Cohere key。

不要把模型供应商 Key 填入赛事 Memory System Key 字段。服务不从旧 RAW-RAG 目录自动寻找凭据。

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

上述命令在你自己的终端生成服务密钥；将结果保存到本机 .env，不提交 Git，也不贴到聊天或公开 Issue。

## 2. 构建并启动 Docker

```bash
docker compose -f deployment/compose.yaml config --quiet
AML_BUILD_COMMIT="$(git rev-parse HEAD)" docker compose -f deployment/compose.yaml up -d --build
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/version
```

Compose 只将 8000 绑定到主机回环地址，避免在尚未设置 TLS 时直接暴露后端。数据库在 named volume memory-data 中，容器以 uid=10001 运行。不要加多个 workers 或启动第二副本共享这个卷。不要在已有比赛数据时运行 down -v；这会删除数据卷。

Docker 构建会安装锁定依赖并下载公开的 cl100k_base 编码资源，无需任何供应商 API key。真实 key 只在运行时通过 .env 注入，不进入镜像。

## 3. 配置 HTTPS

让实际域名解析到主机公网地址，开放反向代理使用的 80/443。将 deployment/Caddyfile.example 的域名换成真实域名，由主机 Caddy 代理至 127.0.0.1:8000；保留大请求限制和长请求超时。Caddy 的安装、DNS、证书和防火墙设置依主机环境完成，当前未替用户创建这些资源。

公网应可访问：

```text
https://实际域名/v1/memories/add
https://实际域名/v1/memories/search
https://实际域名/health
```

Health 不鉴权，Add/Search 始终鉴权。默认填表选择 Authorization: Bearer，Key 填 AML_MEMORY_KEY 原值。

## 4. 上线后自检

```bash
# 只检查 health/version，不调用模型。
python scripts/smoke_remote.py --base-url https://实际域名

# 显式 --exercise 才写入合成文本、调用 embedding/rerank，可能产生费用。
# AML_MEMORY_KEY 从本机 .env 读取，不放到命令行参数里。
python scripts/smoke_remote.py --base-url https://实际域名 --exercise
```

该脚本是自己的接入自检，不等于官方 Smoke。服务从外部网络可达、真实模型权限和配额确认、实际并发/长历史/重启验证完成后，才能申报正式容量。

## 5. 固定版本与官方评测

记录部署 commit、requirements.lock.txt 和 /version 的配置指纹。公开仓库需要包含完整封装和上游来源，见 SUBMISSION_NOTES_ZH.md。平台签发 Eval Key 后先运行官方 Smoke，再运行 Full。

正式 Full 受理后不要因为分数不理想替换模型、检索参数或代码。出现异常保留 Job ID、状态码及无正文的时间记录，通过平台流程处理。

## 6. 数据生命周期

本项目每 10 分钟清理闲置超过 29 天的用户数据，保持在官方赛后 30 天期限内；清理涵盖消息、块、向量和写入回执。需要更早清空时，应在确认所有当前任务结束后停止服务，并按云主机/磁盘实际环境删除对应卷和备份。不要在有任务运行时直接删除数据。

本项目不会给操作系统、云盘、快照服务或宿主机备份工具代为清理。若管理员另有备份、日志采集、崩溃转储或复制机制，必须单独纳入数据处理说明和删除流程。不要打开包含请求正文、Authorization 或模型响应的 debug 日志。
