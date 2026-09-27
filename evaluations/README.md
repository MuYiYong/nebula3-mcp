# 远端只读 MCP 评测

[`remote_readonly.xml`](remote_readonly.xml) 包含 10 个相互独立的固定 Schema 问题，以及 4 个查询路由工作流契约。固定答案仅来自 NebulaGraph 3.8 示例图空间 `basketballplayer` 的 `DESCRIBE SPACE`、`SHOW TAGS/EDGES` 与 `DESCRIBE TAG/EDGE` 结果，不依赖业务行数、查询耗时或写操作；工作流契约只规定输入分类、工具顺序和最终答案不变量，不嵌入真实凭据。

`tests/integration/test_remote_readonly.py::test_remote_mcp_evaluation_answers` 会为每个 `qa_pair` 独立调用一次 `nebula_get_space_schema`，再把 MCP structured content 中的事实与 `expected_answer` 比较。评测文件中的 `fact_key` 只是自动核验键，不是 Server 输入。

默认测试不会连接远端。运行时提供只读账号，并显式开启：

```bash
NEBULA_RUN_REMOTE_TESTS=1 \
NEBULA_ADDRESSES=HOST:9669 \
NEBULA_USERNAME=USER \
NEBULA_PASSWORD=PASSWORD \
.venv/bin/python -m pytest tests/integration -q
```

评测不得加入 mutation/DDL，也不应使用可变数据计数作为固定答案。如果目标图空间的 Schema 被有意修改，应先重新读取 Schema、人工复核问题，再更新期望值。

4 个 `workflow_case` 分别覆盖：显式 nGQL 标量、自然语言图结果、自然语言聚合和基于图结果（VID）的后续独立查询。它们不作为 `_schema_facts()` 的固定答案循环输入，而是由文档契约测试校验完整性。
