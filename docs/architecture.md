# 架构与取舍

## 1. 分层

```text
接入层   FastAPI 路由（chat/docs/tickets/admin/health）+ 静态站点
编排层   agent/orchestrator：意图 → 检索 → 生成 → 引用过滤 → 落库
能力层   providers（embedding / llm / rerank）+ retrieval（filters/index/hybrid/query）
数据层   SQLite（业务 + 权限 + 向量 + FTS5 + 审计）
离线层   ingest pipeline（解析 → 切分 → 嵌入 → 索引）+ eval runner
```

依赖方向单向：`api → agent → retrieval/ingest → providers → 标准库`。`providers` 不认识数据库，`retrieval` 不认识 HTTP，方便替换与测试。

## 2. 数据模型

| 表 | 用途 | 关键字段 |
|---|---|---|
| `documents` | 文档元数据 | `content_hash`（去重）、`version`、`visibility`、`effective_to`、`status` |
| `parents` | 父块（回填给模型） | `path`、`page`、`tokens` |
| `chunks` | 子块（检索单位） | `parent_id`、`ord`、`doc_version` |
| `chunks_fts` | FTS5 倒排 | `bigram_text` |
| `embeddings` | 向量 | `vec`（float32 blob） |
| `tickets` | 工单 | `UNIQUE(user_id, request_id)` 保证幂等 |
| `sessions` / `messages` | 会话与消息 | `state`、`slots`、`citations` |
| `audit` | 审计 | 建单、越权、断连、错误 |
| `eval_runs` | 评测历史 | 配置与指标 |

设计要点：

- **ACL 存在文档与 chunk 两处**：chunk 冗余一份 `visibility`，检索时不需要 JOIN 也能过滤；代价是改权限要同步更新。
- **软删 + 状态**：`deleted_at` 与 `status` 双重条件，删除立即对检索生效，物理清理另走 purge。
- **向量用 blob 存**：启动时载入 numpy 矩阵，避免每次查询反序列化。

## 3. 摄取链路

```text
上传 → sha256 去重 → 存原件 → 入队（BackgroundTasks）
     → 解析（md/txt/docx/pdf）→ 清洗（去页眉页脚、压缩空行）
     → 结构识别（标题层级 / 表格）→ 切分（父块 + 子块）
     → 中文 bigram → FTS5 → 嵌入 → 写库 → 重建内存索引
```

- **结构感知**：Markdown 按 `#` 层级、DOCX 按 Heading 样式维护标题栈，路径形如 `休假与请假管理办法/第1条 年假`。
- **父子块**：子块 ≤420 token 用于召回，父块 ≤1800 token 用于生成；同一父块只保留排名最高的子块。
- **幂等**：内容哈希相同直接返回原 `doc_id`；重传不同内容则 `version+1` 并重建该文档索引。
- **失败可见**：解析异常写入 `documents.error`，状态 `failed`，前端能看到原因。

## 4. 检索链路

```text
query → 同义词扩展 → ACL/时效过滤（候选集）
      → FTS5 BM25 Top50 + 向量 Top50
      → RRF(k=60) → 同父块去重 → 启发式重排
      → Top6 → 父块回填 → 上下文（带 [n]）
      → 证据校验（覆盖率/分数）→ 不足则拒答
```

### 为什么中文用 bigram

SQLite 的 `unicode61` 不切中文词，直接索引等于按字匹配，召回很差。把文本转成相邻两字一组（`员工 工手 手册`）再入 FTS5，等价于按 bigram 建倒排：不需要分词器、不依赖词典，实测召回显著优于原样切。代价是索引膨胀约一倍。

### 为什么 RRF 而不是加权分数

BM25 与余弦量纲不同，归一化后仍需调参，且随语料漂移。RRF 只用名次：`Σ 1/(k + rank + 1)`，k=60。三行代码，几乎不用调。

### 为什么重排是启发式

没有 GPU 与 rerank API 的情况下，用 `0.6×余弦 + 0.3×词面覆盖 + 0.1×路径命中` 近似 cross-encoder 的"交互"效果：至少让"问题词出现在条款里"权重更高。接真实 rerank 模型时替换 `providers/rerank.py` 即可。

### 为什么覆盖率能当拒答信号

离线嵌入的余弦会被长度稀释（短查询 vs 长条款），单独看分数无法区分"有点相关"和"真的能答"。改成"问题特征在 Top3 上下文里的最大覆盖率"后，两类样本的分布才分开：

| 样本类型 | 覆盖率区间（本机 60 条） |
|---|---|
| 可回答 | 0.13 – 0.75 |
| 应拒答（过安全拦截后） | 0.00 – 0.15 |

阈值取 0.16 是扫出来的，不是拍的；换语料必须重新扫。原始查询与同义词扩展各算一次取最大值，避免同义词把分母撑大导致误拒。

## 5. Agent 编排

```text
RECEIVED → ROUTED → NEED_SLOT ⇄ ROUTED → READY → EXECUTING → DONE / HANDOFF
```

- **意图**：规则优先（工单号、建单动词、寒暄），默认走问答。规则可解释、可测试，成本为零。
- **槽位**：`category`、`summary` 必填；缺一个问一个；上一轮处于 `NEED_SLOT` 时，下一轮补充信息继续走建单而不是掉回问答。
- **工具白名单**：只有 `search_policy` / `get_ticket_status` / `create_ticket` / `handoff`；越界直接 403 + 审计。
- **幂等**：`(user_id, request_id)` 唯一索引，重复提交返回同一工单并标 `deduped`。
- **安全拦截**：套取系统提示词、要求绕过审批的请求，在检索前就被拦下，不进模型、不进上下文。

## 6. 流式与并发

- SSE 事件：`meta`（会话 + 引用占位）→ `delta`（逐段文本）→ `done`（用量/延迟/最终引用）。
- **引用先给、后过滤**：`meta` 下发候选，`done` 只保留正文真正引用到的编号，避免"检索到但没用"的文档冒充依据。
- **断连处理**：逐段检查 `request.is_disconnected()`，断开即停止生成并写审计。
- **锁的坑**：流式生成期间若复用请求连接，会长期持有写事务，导致 `database is locked`。解决：用户消息立即 commit，生成结束用新连接落库。

## 7. 前端

- 零构建：`index.html + app.js + styles.css`，FastAPI 静态挂载。
- 断点：`≥1024` 三栏；`768–1023` 两栏 + 引用底部抽屉；`<768` 单栏 + 侧栏抽屉 + 输入框吸底。
- SSE 用 `fetch + ReadableStream` 自己解析（EventSource 不支持 POST 与自定义头）。
- 细节：`100dvh`、`env(safe-area-inset-bottom)`、44px 触控目标、输入框 16px 防 iOS 缩放、`aria-live`、`prefers-color-scheme`。
- 自检：`?audit=1` 把 `scrollWidth - innerWidth` 写进 `<title>`，截图脚本据此判定横向溢出。

## 8. 测试策略

| 层 | 测什么 | 例子 |
|---|---|---|
| 单元 | 纯函数 | 切分预算与重叠、RRF 顺序与并列、ACL 四种 scope |
| 集成 | 组件协作 | 上传 → 索引 → 检索命中；父块回填；阈值拒答 |
| API | 契约 | SSE 事件顺序、错误事件、幂等建单、CORS |
| 端到端 | 真实流程 | 上传 3 份 → 提问 → 引用 → 拒答 → 建单重放 |
| 指标 | 评测正确性 | 完美/回归两类样本的 summarize、golden 形状 |
| 前端 | 静态约束 | viewport、断点、无固定宽度、`getReader` |

测试用临时目录隔离数据库，每个用例重新建索引，不共享状态。

## 9. 缓存与可观测

**语义缓存**（`app/cache.py`）：命中需要同时满足三个条件——查询向量相似度 ≥0.95、签名一致、未过期。签名 = `用户角色 + 部门 + 所有可见文档的 id:version`。这样文档重建、权限调整、租户变化都会自然失效，不需要手工清缓存。拒答与建单结果不进缓存（它们依赖会话与副作用）。

**trace**（`app/tracing.py`）：环形缓冲保留最近 200 次请求，记录检索候选数、重排耗时、引用数、token、成本、是否命中缓存。`/api/traces/{id}` 可回放；badcase 排查不再靠翻日志。

**成本**：按输入/输出 token 分别计价，随 `done` 事件返回，`/api/metrics` 聚合。

## 10. 如果要做成生产系统

1. 向量换 pgvector/Qdrant，按租户分区；FTS 换 OpenSearch 或 PG 分词。
2. 嵌入与生成接真实模型，保留本地基线做降级。
3. 认证换 JWT/OIDC，权限接组织架构同步。
4. 生成加流式 function calling、工具并行、取消传播。
5. 观测接 OpenTelemetry + Langfuse，评测进 CI 门禁。
6. 多租户配额、计费、审计留存按合规要求补齐。
