# nebula3-mcp

在 Codex 中连接 NebulaGraph 3.8，执行 nGQL，查看表格、交互图、PROFILE 和结果解释。MCP 安装在使用者电脑上，无需部署到数据库服务器。

本项目面向 NebulaGraph 3.x（以 3.8.0 验证）。连接悦数图数据库 5.3 请使用 [nebula-mcp](https://github.com/MuYiYong/nebula-mcp)，两者可同时安装。

## 安装

准备 Python 3.10+、Codex，以及可访问的 NebulaGraph 3.8 graphd 服务（默认端口 9669）。安装需要访问 GitHub 和 Python 包源。

macOS / Linux：

```bash
curl -fL https://github.com/MuYiYong/nebula3-mcp/releases/latest/download/install.py -o install.py
python3 install.py --mode mcp
```

Windows PowerShell：

```powershell
Invoke-WebRequest -Uri "https://github.com/MuYiYong/nebula3-mcp/releases/latest/download/install.py" -OutFile install.py
py -3 install.py --mode mcp
```

安装器会校验并安装程序包，将 `nebula3` 注册为独立 MCP 服务器。如果未找到 Codex CLI，按安装器输出的命令完成注册。下载或分享固定版本请使用 [GitHub Releases](https://github.com/MuYiYong/nebula3-mcp/releases)。

## 配置连接

如果 Codex 中的 `nebula3` 只出现在 **“来自插件”** 分组，且这一行没有齿轮按钮，就不能在该插件条目上编辑连接参数。请先按上面的安装命令运行 `python3 install.py --mode mcp`（Windows 为 `py -3 install.py --mode mcp`）；已经安装过插件也可以运行。完成后重新打开 Codex 设置，在 **插件 → MCP → 服务器** 分组找到带齿轮的独立 `nebula3`，点击齿轮，在环境变量中填写：

新建的独立条目会预置以下字段名；地址、用户名和密码留空，其他字段采用表中的默认值。已有条目升级时保留原配置，不会重置这些字段。

| 字段 | 内容 |
|---|---|
| `NEBULA_ADDRESSES` | graphd 地址 `HOST:PORT`；多个地址用逗号分隔 |
| `NEBULA_USERNAME` | 数据库用户名 |
| `NEBULA_PASSWORD` | 数据库密码 |
| `NEBULA_CONNECT_TIMEOUT_MS` | `30000` |
| `NEBULA_ALLOW_MUTATIONS` | `false`，默认只读 |
| `NEBULA_ENVIRONMENT` | 环境名称，例如 `dev_nebula3`，用于结果标识 |
| `NEBULA_DEFAULT_SPACE` | 可选，连接后自动 `USE` 的图空间；需要时自行添加 |

保存并重启该 MCP，然后在新对话中输入“测试 Nebula3 连接”。如果仍只看到“来自插件”的条目，先重启 Codex，再确认安装命令没有报错；不要在无齿轮的插件条目上寻找环境变量编辑入口。插件条目可以继续保留，连接配置以“服务器”分组中的独立 `nebula3` 为准。

连接信息保存在本机 Codex `config.toml` 中，包含明文密码；页面编辑密码时也可能可见，请勿分享该文件或把它加入版本库。若更希望在终端无回显输入密码，可使用[高级配置命令](https://github.com/MuYiYong/nebula3-mcp/blob/main/docs/configuration.md#连接配置命令)，但页面配置不需要运行该命令。

### 多套环境

每套环境添加一个独立的 STDIO MCP 服务器，名称由你定义，例如 `dev_nebula3`、`prod_nebula3`、`test_nebula3`，不限制为两套。

根据当前公开的 Codex 插件接口，本项目不能在原生 MCP 设置页的 `nebula3` 齿轮旁添加“复制集群”按钮。添加另一套环境时，请在同一设置页新建独立 MCP 服务器并按下面步骤配置。

1. 在 MCP 设置中添加自定义服务器，填写一个不重复的名称。
2. 复制已安装 `nebula3` 的命令和参数，复用同一程序，无需重复安装。`launcher.py` 路径应作为一个完整参数，含空格时不要拆开。
3. 分别填写该环境的地址、用户名和密码；建议 `NEBULA_ENVIRONMENT` 与服务器名称一致。
4. 保存后按客户端提示重启对应服务器，测试连接。

切换时关闭当前条目、开启目标条目。若同时启用多套，请在查询请求中明确指定服务器名称。尚未配置的条目，以及不再使用的默认或插件条目，应保持关闭。

## 使用

直接输入 nGQL：

> 使用 dev_nebula3，执行 USE basketballplayer; MATCH (v:player)-[e:follow]->(n) RETURN v, e, n LIMIT 20

GO、FETCH、LOOKUP、FIND PATH、GET SUBGRAPH、SHOW、DESCRIBE、管道 `|` 和 `$var = ...;` 组合语句都可以直接执行。

用自然语言提出查询：

> 使用 dev_nebula3，先查看图空间和 Schema，再查找 Tim Duncan 关注的球员。

NebulaGraph 3.x 的图空间是会话状态。未选图空间时，客户端会列出可用图空间并请你选择；选择后自动继续刚才的查询，不需要重复发送。后续查询沿用所选图空间，语句中的 `USE <space>;` 也会切换后续查询的图空间。

自然语言、Cypher 或 GQL 转换可配合客户端的 [ngql-skills 技能](https://github.com/MuYiYong/nebula-ngql-skills)。已有 nGQL 可以直接执行，不需要额外安装该技能。

查询结果可显示表格、交互图、图表、PROFILE 和解释。点击顶点查看 VID、Tag 与属性，点击边查看边类型、起点 → 终点和 rank；复制图标复制原始 nGQL 并提示“已复制”。自动添加的 PROFILE 不出现在复制的语句中。客户端不支持 MCP Apps 时，仍可查看文本和表格结果。

默认只读。需要写入时，应使用具备相应权限的账号，将 `NEBULA_ALLOW_MUTATIONS` 设为 `true`，重启 MCP，并在每次写入时明确确认。请按业务需要限制数据库账号权限（例如只授予目标图空间的 GUEST 角色）。

## 升级

重新执行上面的下载与安装命令即可。安装器会保留已管理服务器的连接配置；自定义条目共用同一启动程序，升级后重启相关 MCP。

## 卸载

如添加了多个自定义条目，先在 Codex 中删除共用此程序的条目，再卸载程序：

```bash
python3 install.py --uninstall
```

Windows 使用 `py -3 install.py --uninstall`。卸载会删除受管 MCP 程序；仅删除某个环境时，在 Codex 中删除对应条目即可，不必卸载程序。

## 常见问题

- **CONFIGURATION_REQUIRED**：程序已启动，但连接信息尚未填完整。检查地址、用户名和密码。
- **SPACE_SELECTION_REQUIRED**：当前会话未选择图空间，或 `USE` 的图空间不存在。按提示选择图空间后，原查询会自动继续。
- **连接失败**：检查 graphd 服务、端口、网络和账号权限；保存配置后重启相应 MCP。
- **属性值为空**：MATCH 中需写成 `v.<tag>.<属性>`（例如 `v.player.name`），`v.name` 在 3.x 中返回空值。
- **LOOKUP 或按属性过滤的 MATCH 报错**：需要先为该 Tag/Edge 属性创建原生索引；可在 Schema 中查看已有索引。
- **没有交互图**：语句需要返回顶点、边或路径；聚合值通常只显示表格或图表。只返回边时，端点仅显示 VID。客户端也需要支持 MCP Apps。
- **结果被截断**：仅展示了部分数据，解释和统计也仅针对返回部分；请收紧查询范围。
- **安装时提示同名冲突**：先检查已有条目的启动命令，避免覆盖其他程序的配置。

[高级安装与配置](https://github.com/MuYiYong/nebula3-mcp/blob/main/docs/configuration.md) · [工具用途与兼容性](https://github.com/MuYiYong/nebula3-mcp/blob/main/docs/tools.md)
