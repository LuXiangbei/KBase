# 构建状态

更新时间：2026-11-10 13:20（本机）

## 阶段

| 阶段 | 内容 | 状态 | 证据 |
|---|---|---|---|
| P0 | venv、骨架、config、db、health | ✅ | `/api/health` 200，providers 打印正常 |
| P1 | 解析、切分、索引、混合检索 | ✅ | `test_chunker` / `test_rrf` / `test_retrieval` 全绿 |
| P2 | chat SSE、引用、拒答 | ✅ | `test_api` 事件序列断言；curl 逐段输出 |
| P3 | Agent、工单、幂等、ACL | ✅ | `test_agent` / `test_acl` / `test_e2e` 全绿 |
| P4 | 黄金集、评测、报告 | ✅ | 84 条，exit 0，`docs/eval-report.md` |
| P5 | 前端与响应式 | ✅ | 360/768/1280 截图，overflow=0 |
| P6 | e2e、README、架构、演示稿 | ✅ | `scripts/e2e_demo.py` 输出 E2E OK |
| P7 | 接入 DeepSeek、路由、缓存、注入防御 | ✅ | 简单/复杂/多轮/注入/越权实测通过 |

## 数字

- 测试：**103 项用例全部通过**（`pytest -q` 退出码 0；10 个测试文件）
- 评测：Recall@5 **1.000**、MRR **0.888**、引用有效率 **1.000**、拒答准确率 **1.000**、误拒率 **0.000**
- 在线模型：DeepSeek `deepseek-flash`，简单问答约 0.8–2s、复杂计算约 1.3s，单次 ¥0.0008–0.0012；缓存命中 31ms、0 成本
- 语料：30 份制度文档、125 个 chunk（含 3 份 DOCX）
- 响应式：360 / 768 / 1280 三档，`scrollWidth - innerWidth = 0`

## 已知问题（不阻塞交付）

1. 离线嵌入是词面相似度，同义改写依赖 `app/retrieval/query.py` 词表；覆盖不到的说法会漏召回。
2. 抽取式回答不会归纳，长答案可能变成多条摘录拼接。
3. 无 OCR：扫描件 PDF 会标 `failed`（错误信息可见）。
4. 演示鉴权用请求头，生产需换 JWT/OIDC。
5. SQLite + 内存向量适合万级 chunk，百万级需换向量库与分片。

## 下一步

- 接真实 LLM（配置切换）并重跑黄金集，观察忠实度与答案相关性变化。
- 增加 20 条改写型难样本，单独报告"难样本 Recall"，避免 in-domain 数字过于乐观。
- 把评测门禁接进 CI（当前是脚本退出码）。
- 补 OCR 与表格结构化解析。
