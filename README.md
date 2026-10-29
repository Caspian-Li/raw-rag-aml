# RAW-RAG AML Textual API

基于 [Litmeb/RAW-RAG](https://github.com/Litmeb/RAW-RAG) 的 **AML 开源方法榜适配版本**。完整上游源码固定在 `vendor/raw-rag/`；731 个跟踪文件未修改，校验清单为 `UPSTREAM.json`。新增服务直接调用上游分块、完整消息映射、余弦召回及 Cohere 重排实现，不将方法重新写成另一套 RAG。

公开仓库为 `Caspian-Li/raw-rag-aml`，API 在独立服务器上运行。实际部署版本和查询处理策略以 `/version` 为准；GitHub 托管代码，不能替代运行 API 的服务器。

## 方法与署名

原作者：Zhengye Bao、Shengtao Zhang、Haokai Zhang、Shiqi Lou、Muning Wen、Minnan Luo。

原技术报告：*Is Complex Agentic Memory Always Needed? Naive RAG Is Stronger Than We Thought*，arXiv:2610.30755。上游固定 commit：`bce0b2437118226b6e717026b7ae353c20738b4f`。本项目是复现和协议适配，不声称原创提出 RAW-RAG。上游完整说明与许可证保留在 `vendor/raw-rag/`；所有变化见 [CHANGES.md](CHANGES.md)。

## 固定配置

| 项目 | 本服务配置 |
|---|---|
| 赛道/组别 | Textual / Open-source Methods |
| 分块 | 上游 token 窗口 256，重叠 32，cl100k_base |
| Embedding | **text-embedding-v4**，1024 维，float 编码；因赛事要求替换原版模型 |
| Embedding HTTP 批量 | 10，符合官方接口每批最多 10 条的限制 |
| 初步召回 | 同一 user_id 内，余弦 Top-128 |
| Reranker | Cohere rerank-v3.5，/v2/rerank |
| 返回 | 重排后最多 min(15, 请求 top_k) 条；top_k 支持 1–100 |
| 完整消息展开/去重 | 开启展开，关闭去重；不按时间重新排序 |
| Add | 同步，成功前已落盘并完成向量索引；原子追加、幂等 |
| Search | 只返回原文证据，不调用 reader，不接收 gold/question_date |
| 查询 Embedding 长度保护（v0.1.1） | 仅长度拒绝后截取最多 8,192 UTF-8 字节前缀并重试一次；详情见下节 |
| Answer / Eval | 平台负责；本服务主动禁用 answer() |
| 存储 | SQLite WAL，单服务进程，向量无损 float64-le 保存 |
| 并发 | 默认 Add 2、Search 2，各可配置 1–16；当前参赛服务器各为 16，不代表 Full 吞吐保证 |
| 单请求上限 | 16 MiB 请求正文，1700 秒服务操作超时 |
| 数据过期 | 用户记忆闲置 29 天后删除，每 10 分钟检查；无自动外部备份 |

本地原版 LongMemEval-S 的 89.67% 属于不同 embedding、reader、提示词及评分协议，**不是本项目 AML 成绩**，不作为本服务准确率承诺。

## 超长 Search 查询处理（v0.1.1）

Search 的 query 可能包含长表格或上下文。本版本**先原样请求查询 Embedding**，不预先截断供应商本来能够接受的查询。只有百炼明确返回 `HTTP 400`、`InvalidParameter`，且 message 包含 `Range of input length should be [1, ...]` 时，才保留原文前缀并重试一次。

- 截断前缀最多 **8,192 个 UTF-8 字节**，不是 8,192 个字符或本地 Token。边界处不完整的 UTF-8 字符被移除，不插入替换字符。这是为避免 tokenizer 不匹配而采用的保守安全边界，不声称恰好填满模型的最大上下文。
- [百炼公开文档](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)列出 text-embedding-v4 单条上限为 8,192 Token，但线上长度错误中的 33,000 未标明单位，不能直接当作本地 Token 上限。本实现不依赖 cl100k_base 估算查询的模型 Token 数，也不根据 usage 做反复校正。
- 每轮原有 Embedding 尝试中，至多增加一次截断后请求；其他错误不触发截断。原上游外层重试保留。只有原请求被长度限制拒绝的查询会增加调用与延迟。
- 仅查询向量化使用截断前缀；Cohere 重排仍使用原始完整 query。Add 的原文、分块、已保存向量、返回证据不变，候选 128、最终最多 15、轮次展开及并发设置保持原值。不做摘要、LLM 改写、查询分块合并，不拼接 options 或历史。
- 已成功编码的查询保持原输入；已提交记忆和已完成的平台结果不会被服务改写。超长查询尾部不参与向量召回，可能影响这部分查询的检索结果和分数，需要随版本向平台披露。
- `/version.query_embedding_policy` 公布触发条件、安全字节边界、截断方向和重试次数。日志只记录截断事件，不包含原始问题；公开仓库不包含正式评测文本。

旧索引无需重建：查询预处理不改变索引格式或构建参数，索引配置指纹保持兼容；API 版本、部署 commit 和查询策略独立标识方法变化。正式恢复/重跑以平台的版本规则为准。

## 快速运行（Python 3.12 / Linux）

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
cp .env.example .env
chmod 600 .env
# 在本机编辑 .env，填写三个真实凭据，不要提交 .env。
python scripts/preflight.py
python -m rawrag_aml --host 127.0.0.1 --port 8000
```

- `AML_MEMORY_KEY`：自行生成的服务专用密钥，至少 32 字符。
- `AML_EMBEDDING_API_KEY`：可实际调用 text-embedding-v4 的供应商密钥。
- `AML_EMBEDDING_BASE_URL`：与密钥区域匹配的 OpenAI-compatible endpoint。
- `AML_RERANK_API_KEY`：Cohere rerank-v3.5 密钥。
- 不需要本服务的 GPT 回答/评分密钥。上游构造器分配了一个不会被调用的 reader client，使用 embedding endpoint/credential 初始化；所有 answer 调用均被拒绝。

不得将原 `.env` 中仅支持 text-embedding-3-small 的配置当作已验证的 v4 配置。本项目启动时拒绝通过 `AML_EMBEDDING_MODEL` 切换到其他模型。`preflight.py` 仅验证本地配置，不发模型请求，不表示真实服务连通已通过。

## 接口

`GET /health`、`GET /version` 无需鉴权。Add/Search 按 `AML_AUTH_SCHEME` 使用 `Bearer`（默认）、`Token` 或 `X-Api-Key`。未经认证的写入/检索请求返回 401。

```http
Authorization: Bearer <AML_MEMORY_KEY>
```

```json
POST /v1/memories/add
{
  "request_id": "unique-write-1",
  "user_id": "isolated-user",
  "session_id": "source-session",
  "messages": [
    {"role": "user", "content": "raw history text", "timestamp": 1704067200000}
  ]
}
```

成功返回 HTTP 200：

```json
{"success": true, "request_id": "unique-write-1", "user_id": "isolated-user", "session_id": "source-session"}
```

```json
POST /v1/memories/search
{"query": "original question", "user_id": "isolated-user", "top_k": 100}
```

成功响应：

```json
{"data": [{"id": "stable-memory-id", "content": "timestamped raw evidence", "score": 0.9, "created_at": "2026-10-26T00:00:00+00:00"}]}
```

上面的 HTTP 方法/路径是说明标签，不是 JSON 的一部分。完整可执行客户端见 `scripts/smoke_remote.py`。

`timestamp` 是可选 Unix 毫秒，保留传入消息顺序；没有时间时用上游的 unknown-date 展示，不伪造业务日期。选择题可接收顶层 `options`，但不将选项追加进检索 query。这里只实现文本赛道，不接受图片内容数组或 system 消息；不支持的字段/类型返回 422。查询 Embedding 的长度保护按上节执行，不改变原始请求对象。

未知 user_id 返回 `{"data":[]}`。同一 request_id、相同请求重试返回原成功响应且不重复调用模型；相同 request_id 改变正文或用户返回 409。上游失败不提交新快照，返回脱敏的 503 与 Retry-After。原始供应商错误、正文、向量、密钥不写入服务日志。

## 测试和审核

```bash
python -m pip install -r requirements-test.lock.txt
python -m pytest -q
python scripts/verify_release.py  # 已初始化/暂存 Git 后执行
```

测试只使用合成文本和模拟 embedding/rerank，不接触本地 benchmark、金标或供应商计费 API。包括完整会话/增量结果等价、用户隔离、幂等、并发、重启、失败原子性、Top-k、轮次展开、输入校验和过期清理。`VALIDATION.md` 记录实际本地检查结果。GitHub CI 还配置 Docker 构建及不调用上游的容器健康检查；在远端 CI 实际完成前，不宣称镜像已验证。

## 部署与报名

参见 [DEPLOYMENT.md](DEPLOYMENT.md)。推荐一台 Linux 主机、单副本 API、持久化卷及 HTTPS 反向代理。**只运行一个 Uvicorn worker**；同数据库的第二进程会被文件锁拒绝。不支持多副本共享数据库和网络文件系统上的生产 SQLite。

参赛前须验证真实供应商连通、网络访问、配额、Full 规模容量及官方 Smoke。冻结部署 commit、环境与配置指纹，按 [SUBMISSION_NOTES_ZH.md](SUBMISSION_NOTES_ZH.md) 填表，不将默认并发或测试数据量写成已验证容量。

## 官方参考

- [AML 赛事与模型规则](https://agentmemoryleaderboard.ai/competition/)
- [AML Add/Search 契约](https://agentmemoryleaderboard.ai/#doc-contract)
- [text-embedding-v4 OpenAI-compatible 文档](https://www.alibabacloud.com/help/en/model-studio/embedding-interfaces-compatible-with-openai)

规则核查日期：2026-10-26；正式版本以主办方当期冻结契约为准。
