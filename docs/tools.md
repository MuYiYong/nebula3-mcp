# 工具用途与兼容性

工具通常由客户端自动调用，无需用户记住名称。当前公开的 11 个工具均有实际实现，接口层使用 NebulaGraph 3.8 的 nGQL 与 nebula3-python 客户端。

## 日常查询与展示

| 工具 | 实际用途 |
|---|---|
| `nebula_test_connection` | 检查会话，返回 graphd 版本（`SHOW HOSTS GRAPH`）、当前图空间和脱敏配置。 |
| `nebula_list_spaces` | 通过 `SHOW SPACES` 发现图空间，帮助用户选择查询范围。 |
| `nebula_get_space_schema` | 读取 VID 类型、Tag、Edge Type、属性和原生索引，可选附带 `SHOW CREATE TAG/EDGE`；读取后恢复原图空间。 |
| `nebula_select_space` | 在当前会话执行 `USE <space>`，自动续跑最近一条因缺少图空间而受阻的只读查询。 |
| `nebula_validate_ngql` | 检查只读策略、占位符以及 GQL/Cypher 残留；可选 EXPLAIN，不执行原查询。 |
| `nebula_execute_query` | 执行只读 nGQL，返回表格、图、图表、分析和 PROFILE。 |
| `nebula_render_result` | 将已有结果和客户端撰写的解释展示为 MCP App；不额外查询数据库。 |
| `nebula_execute_mutation` | 独立执行写入，必须启用 NEBULA_ALLOW_MUTATIONS 并逐次确认。 |

校验、执行和展示承担不同职责，不是三次查询。生成 nGQL 和解释由客户端完成；MCP Server 本身不调用模型。显式 nGQL 不得为了生成图而改写，聚合查询也不应额外查询关系来补图。

## 可选配置能力

| 工具 | 使用场景 |
|---|---|
| `nebula_configure_connection` | 临时配置当前进程，成功后替换连接，失败保留原连接；不持久化。适用于不便编辑启动配置的客户端。 |
| `nebula_list_environments` | 列出此进程通过 NEBULA_ENVIRONMENTS 配置的环境；单环境时仅显示该环境。 |
| `nebula_switch_environment` | 切换上述命名环境，先验证连接，再清除旧会话的图空间和待执行查询；失败保留旧连接。 |

这两个环境工具不能切换 Codex 中的独立 MCP 服务器。常用 dev_nebula3 / prod_nebula3 等独立条目时，在原生设置中管理各条目即可。详细配置见 [高级配置](configuration.md)。

## 兼容入口

本项目没有兼容入口。悦数 5.3 版的 `nebula_render_graph` 仅为其已发布客户端保留，这里统一使用 `nebula_render_result`。与悦数版相比，`graph`、`schema/graph_type` 参数在这里对应 `space`，`nebula_validate_gql` 对应 `nebula_validate_ngql`。

## nGQL 执行规则

- 只读判断依据 graphd 3.8 词法：`#`、`//`、`/* */` 是注释，`--` 是无向边而不是注释；字符串和反引号名称中的关键字不参与判断。
- 只读工具接受 MATCH、GO、FETCH、LOOKUP、FIND PATH、GET SUBGRAPH、YIELD、RETURN、UNWIND、SHOW、DESCRIBE、管道和 `$var = ...;` 组合；任何写入或管理关键字（INSERT、UPDATE、UPSERT、DELETE、CREATE、DROP、ALTER、SUBMIT JOB、KILL 等）都会被拒绝。
- 写入工具只接受一条写入语句，前面最多有一条 `USE <space>;`。
- PROFILE 只能包裹一条语句，因此开头的 `USE <space>;` 会先单独发送（记录在 `query.session_statement`），再执行 `PROFILE <语句>`。多条语句组合按原文执行，不自动 PROFILE。

## 返回结果参考

结果包含 nGQL、表格、图元素、图表、PROFILE、解释事实和截断标识。图与图表规格分别为 cytoscape-elements-v1 和 vega-lite-v5。

query.display_statement 用于展示和复制，query.executed_statement 记录实际发送给数据库的语句（包含自动 PROFILE），query.space 是 graphd 返回的会话图空间。顶点的标识是 VID，属性按 `tag.属性` 展开；边的标识是（起点、边类型、rank、终点），方向为存储方向 src → dst。只返回边时，端点以仅含 VID 的占位顶点显示。PROFILE 按算子树展开，含执行/总耗时、行数与执行次数。超出 JavaScript 安全范围的整数以字符串传输，避免 VID 与 rank 精度丢失。结果达到上限时分析仅针对已返回样本。
