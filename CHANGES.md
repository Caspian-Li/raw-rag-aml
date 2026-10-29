# 与原始 RAW-RAG 的差异

原始版本：Litmeb/RAW-RAG @ bce0b2437118226b6e717026b7ae353c20738b4f。

## 未修改的上游文件

731 个文件逐字节保存在 vendor/raw-rag，UPSTREAM.json 保存逐文件 SHA-256。没有把本地复现时被论文 runner 覆盖的配置作为上游原件复制，也没有复制 .git、真实 .env、数据集、结果、请求日志或缓存。

新增服务通过 CompetitionAdapter 继承 RAGAdapter，复用 _build_conversation_index、_format_conversation、_create_token_chunks、_build_turn_context、余弦检索、rerank 请求与响应校验、固定 Top-15 选择、完整轮次展开。它没有复制重写上述算法，也不引入事实抽取、摘要、图或 LLM 答题步骤。v0.1.1 增加下述确定性的查询 Embedding 前缀截断。

## 会影响方法输入、模型或评测边界的差异

1. 按 AML 学术榜要求，将 text-embedding-3-small 换为 text-embedding-v4；固定 1024 维、float 返回。模型变化意味着不能保证原版分数不变。
2. Embedding 每批从 128 降至 10，以符合供应商 Max rows=10 限制；显式指定 float，校验数量、索引、维度、非有限值和零向量，并按 index 恢复响应顺序。
3. 数据由 Add API 分批提供，只使用平台实际传入的 role/content/timestamp/session。说话人以 user/assistant 表示；会话身份改用 user_id 的哈希，不保留原 benchmark 特定命名。
4. 不走 longmemeval_ 前缀触发的临时向量路径；Add 就完成历史块向量化、原子持久化，Search 读取已保存向量。原来在 Search 计时的历史 embedding 成本移到 Add。
5. Search 不接受私有 question_date/filter/gold 字段。无专用问题日期时不额外推测日期或从本地题库补取；原消息日期仍保存在证据中。
6. 不启动原版 reader/judge。AML 固定 Answer/Eval，因此原 answer_prompt_cot 和原版三次 judge 协议均不参与赛事服务。
7. Search 最多返回 min(15, top_k) 项。保留原候选 128 和最终 15；没有为了平台上限 100 而静默将最终证据改成 100。
8. 完整轮次证据直接放入 data[].content；不再依赖客户端读取上游 formatted_context 元数据。保留原文日期和说话人，关闭去重，按 rerank 顺序返回；移除上游 Memory N 的相似度展示标题，不附加任何回答指令。
9. **v0.1.1：查询 Embedding 前缀截断。** 采用公开文档的 8,192 Token 上限作为本地 cl100k_base 参考预算，保留精确 Unicode 前缀；不声称该 tokenizer 与供应商原生计数相同。明确的供应商输入长度错误可在每轮 Embedding 尝试内触发最多 3 次前缀预算减半；其他错误不做此回退。只有查询向量化输入截断，重排使用原始 query，Add 原文和索引不变。该变化可能影响长查询召回和最终成绩，不与原版等价。策略通过 /version 独立披露，存储索引兼容指纹保持不变，避免无必要地重建已写入的记忆。

## 工程差异

v0.1.1 对成功 Embedding 响应的单查询 `usage.total_tokens` 增加校正：超过 8,192 时按比例缩短前缀并重新编码。长度错误与用量校正共用每轮最多 3 次缩短额度；这会增加部分长查询的编码费用和延迟。用量缺失时不伪造供应商 Token 统计。此校正同样只作用于查询，不改变 Add 或重排 query。

- FastAPI 提供同步 Add/Search、health、version，支持三种鉴权；无异步 202/状态查询。
- SQLite WAL/FULL 原子提交消息、块、向量和 request_id 回执；向量采用 float64 little-endian，不做 float32 量化。
- 同用户跨 session 追加，单进程内按用户锁串行；不同用户通过 Add/Search 两个信号量限制在各自并发上限。
- 每次 Add 用上游完整文本分块方法重建当前用户的块和轮次映射，避免按独立消息 tokenization 造成边界偏差。只在相同用户内复用文本完全相同块的已有向量，并在 SQL 中只更新变化的块；不跨样本共享记忆或向量缓存。
- 新增记忆、向量、幂等回执同时提交；失败不部分提交。重复 request_id 且正文不一致返回 409。不得通过更换 request_id 掩盖冲突。
- 明确请求限制 16 MiB、top_k 1–100、单操作 1700 秒；HTTP 正文超限或字段非法返回 4xx。查询 Embedding 的长度截断是 v0.1.1 明确披露的例外，详见上节。
- 并发配置校验支持 Add/Search 各 1–16，默认各 2；这同时公开此前服务器已应用的 16 并发校验补丁。
- 不记录完整模型请求和响应；不会生成原版 rag_requests.jsonl。日志仅包含运行事件和异常类名，关闭 Uvicorn access log。
- 默认闲置 29 天清理用户消息、块、向量和回执，SQLite secure_delete=ON 并尝试截断 WAL；不配置包含赛事数据的外部备份。若操作者另外创建快照、云盘备份或归档，也必须纳入赛后删除流程。
- 运行时验证原始源码清单；配置/模型 endpoint 变化时拒绝复用旧数据库，需采用新的存储目录和声明版本。

## 不作出的声明

本项目不是原作者官方 AML 参赛提交；本地离线测试不是主办方 Smoke；不保证公榜成绩或 Full 规模稳定性。真实部署以 /version 和操作者的运行记录为准。
