# 初始报名表准备稿与后续版本披露

下表保留初始部署前的准备稿，历史占位值不代表当前部署。现有系统为 RAW-RAG-AML，公开仓库为 https://github.com/Caspian-Li/raw-rag-aml；实际运行版本以 /version 为准。v0.1.1 的查询截断披露见文末。

| 字段 | 准备值 |
|---|---|
| 赛道 / 榜单 | 文本记忆 / 开源方法榜 |
| 系统名称 | RAW-RAG-Reproduction-Caspian |
| 版本名称 | aml-textual-v0.1.0；正式部署时绑定实际 commit |
| 公开仓库 | 目标 Caspian-Li/raw-rag-aml；未实际创建/上传前不当作已存在 |
| Add | https://<实际域名>/v1/memories/add |
| Search | https://<实际域名>/v1/memories/search |
| 认证方式 | 默认 Authorization: Bearer；与实际 AML_AUTH_SCHEME 一致 |
| Memory System Key | AML_MEMORY_KEY 原值，仅填受控密钥字段 |
| Add status URL | 留空，采用同步 HTTP 200 |
| Health | https://<实际域名>/health |
| 邮箱 / 姓名 / 团队 | 由参赛者填写真实信息 |

## 可在部署、真实连通和容量自检后修改使用的说明

```text
本系统为 RAW-RAG 的复现及 AML 文本赛道适配，不声称原创提出 RAW-RAG。
原作者：Zhengye Bao、Shengtao Zhang、Haokai Zhang、Shiqi Lou、Muning Wen、Minnan Luo。
技术报告：Is Complex Agentic Memory Always Needed? Naive RAG Is Stronger Than We Thought，arXiv:2610.30755。
上游：https://github.com/Litmeb/RAW-RAG
上游 commit：bce0b2437118226b6e717026b7ae353c20738b4f。
本版本源码：<实际公开仓库>；部署 commit：<完整 SHA>；版本：aml-textual-v0.1.0。

保留原文、256-token 窗口、32-token 重叠、余弦召回 128、Cohere rerank-v3.5 重排后最多 15 项；完整消息展开，关闭去重，按重排顺序返回。请求 top_k 小于 15 时按其上限返回。
依学术榜规则将 embedding 从 text-embedding-3-small 换为 text-embedding-v4，1024 维、float 编码，每批 10 条；不使用 LLM 进行记忆提取或生成最终答案。Answer/Eval 由平台执行。

提供同步 Add/Search；Add 在消息、原文块、向量和幂等回执原子落盘后才成功。按 user_id 隔离，同用户跨 session 追加，request_id 重复写入幂等；单进程运行，支持重启恢复。Search 只返回记忆证据。
Add：<实际地址>；Search：<实际地址>；Health：<实际地址>。
认证：<实际方式>，服务专用 Key 通过表单密钥字段提交。

运行环境：<真实服务器配置>；已验证容量：<实际测试结果>；实际 Add/Search 并发上限：<实测后填写>；上游配额：<实际值>。
约束：仅文本；单请求最多 16 MiB；操作超时 1700 秒；不接受图片、system 消息或额外 gold/question_date 字段。失败返回明确状态码及脱敏原因，不静默截断内容。

全部改动见仓库 CHANGES.md，包括模型及 HTTP 批量替换、同步 API、SQLite 持久化、幂等/并发控制、同用户内不变块向量复用、数据字段映射、完整消息证据输出、禁用本地 reader/judge 和日志收敛。
默认不保存完整请求日志；闲置数据 29 天清理。<补充实际云盘/快照/备份删除安排>。
```

不要将离线模拟测试写成“官方 Smoke 通过”，不要将默认并发写成“已完成 Full 容量验证”。
# v0.1.1 查询输入处理补充披露（2026-10-29）

Search 原始 query 先原样调用 text-embedding-v4；仅遇到明确的 400 / InvalidParameter / Range of input length 错误时，保留最多 8,192 UTF-8 字节的有效原文前缀，并在该轮 Embedding 尝试内重试一次。字节边界是保守保护，不宣称等于模型最大 Token 数；不进行 usage 校正、摘要、LLM 改写或查询分块合并。正常成功请求不截断，重排仍使用原始完整 query；Add、既有索引、已完成结果、候选 128、最终最多 15 和轮次展开不变。对超长查询的结果可能有影响，应随新版本向平台披露；正式恢复/重跑以平台版本规则为准。策略通过 /version.query_embedding_policy 公开。
