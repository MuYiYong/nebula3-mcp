# 高级安装与配置

常规安装和独立多环境配置见 [README](../README.md)。以下方式按需使用。

## 本地分发文件夹

下载同一 Release 的完整资产（install.py、wheel、plugin ZIP 和 SHA256SUMS），在该目录运行：

```bash
python3 install.py --assets . --mode mcp
```

Windows 使用 `py -3`。安装器校验本地资产的 SHA-256；Python 依赖（包括 nebula3-python）仍需网络、缓存或配置好的镜像，此方式不等于完全离线安装。

## Codex 插件模式

常规安装推荐 `--mode mcp`，便于在原生设置中分别配置多个服务器。需要插件模式时运行 `python3 install.py`（不带 `--mode mcp`），Windows 使用 `py -3 install.py`。

默认使用 Codex plugin 模式时，安装器注册 `nebula3-mcp@nebula3-mcp-local`。没有 Codex CLI 时会输出稍后注册所需的命令。插件条目与独立 MCP 条目是两种入口；只有“服务器”分组中的独立 MCP 条目提供环境变量编辑按钮。已装插件且希望在页面配置时，运行 `python3 install.py --mode mcp` 添加独立条目，然后在 Codex 设置中填写连接字段。

已有独立 `nebula3` 注册不会被默认插件安装替换。需要迁移时运行 `python3 install.py --migrate-to-plugin`；非本安装器管理的同名服务器不会被自动删除。服务器名称与悦数 5.3 版的 `nebula` 不同，两者互不覆盖。

## 连接配置命令

```bash
python3 install.py --configure
python3 install.py --config-status
python3 install.py --clear-config
```

这些命令只管理名称为 `nebula3` 的独立注册：配置使用无回显密码输入，状态只显示字段是否已设置，清除会删除该独立注册。自定义名称的服务器请在原生设置中管理。密码需要保留首尾空格时使用 `--configure`，原生环境变量编辑器可能去掉首尾空格。

新建独立注册时，安装器会预置 `NEBULA_ADDRESSES`、`NEBULA_USERNAME`、`NEBULA_PASSWORD` 三个空字段，以及 `NEBULA_CONNECT_TIMEOUT_MS=30000`、`NEBULA_ALLOW_MUTATIONS=false`、`NEBULA_ENVIRONMENT=default`。已有注册的变量不会被覆盖。当前公开的 Codex 插件接口不允许本项目在原生 MCP 设置页添加复制按钮；另一套服务器需在原生页面新建并配置。

连接配置保存在本机 Codex config.toml 中，包含明文密码。不要分享该配置文件。`nebula_configure_connection` 仅临时改变当前进程连接，不保存到此文件；工具参数可能由客户端记录。

## 其他连接字段

| 字段 | 默认值 | 说明 |
|---|---|---|
| `NEBULA_DEFAULT_SPACE` | 空 | 建立会话后执行 `USE`；图空间不存在或无权限时启动报 `INVALID_DEFAULT_SPACE`。 |
| `NEBULA_REQUEST_TIMEOUT_MS` | `60000` | 单条语句的 socket 超时。`NEBULA_CONNECT_TIMEOUT_MS` 只用于启动时的连通性探测。 |
| `NEBULA_TLS_ENABLED` | `false` | 使用 TLS 连接 graphd。 |
| `NEBULA_TLS_CA_FILE` | 空 | CA 证书路径。设置后校验服务端证书；未设置时 TLS 只加密、不校验证书，生产环境应设置。 |
| `NEBULA_MAX_ROWS` / `NEBULA_MAX_NODES` / `NEBULA_MAX_EDGES` / `NEBULA_MAX_BYTES` | `100` / `500` / `1000` / `1048576` | 结果、图元素和字节上限。 |
| `NEBULA_LOG_LEVEL` | `INFO` | 日志级别。 |

每个 MCP 进程只保留一个数据库会话，图空间选择、查询和写入都复用它。会话失效（例如超过 graphd 的 `session_idle_timeout_secs`）时返回 `connection_error`，不会静默重建会话，需重启 MCP 或调用 `nebula_configure_connection`，然后重新选择图空间。

## 单进程内的多环境配置

如果需要同一进程通过工具切换环境，仍支持以下 JSON 配置；这不是原生页面开关方案，二者择一配置。

原生设置只显示已保存的环境变量，不会根据 MCP 工具自动生成配置字段。已有单连接安装若看不到下面两个字段，需要在同一编辑器中添加。

在同一原生环境变量编辑器中添加 `NEBULA_ENVIRONMENTS`，值为 JSON。每个名称对应一套完整的 `NEBULA_*` 配置，不继承另一环境的地址、账号或写入权限。例如（所有值均为字符串）：

```json
{"dev":{"NEBULA_ADDRESSES":"DEV_HOST:9669","NEBULA_USERNAME":"USER","NEBULA_PASSWORD":"PASSWORD","NEBULA_CONNECT_TIMEOUT_MS":"30000","NEBULA_DEFAULT_SPACE":"basketballplayer"},"prod":{"NEBULA_ADDRESSES":"PROD_HOST:9669","NEBULA_USERNAME":"USER","NEBULA_PASSWORD":"PASSWORD","NEBULA_ALLOW_MUTATIONS":"false"}}
```

从单连接改为多环境时，可把原有连接字段完整放入 `default` 对象，并设置 `NEBULA_ENVIRONMENT=default`，然后继续添加其他环境。启用 `NEBULA_ENVIRONMENTS` 后，以各环境对象中的连接信息为准；外层的单连接字段不再参与连接，迁移后应移除这些重复字段，避免编辑错位置。

可再设置 `NEBULA_ENVIRONMENT=dev` 作为启动环境；配置多套但不指定默认环境时，先选择再连接，不自动猜测。环境名称支持字母、数字、下划线和连字符。保存后重启 MCP 以加载配置，之后对话中说“列出环境”或“切换到 prod”，即可调用对应工具，无需重启。单套 `NEBULA_ADDRESSES/USERNAME/PASSWORD` 配置仍可使用；其名称取 `NEBULA_ENVIRONMENT`，未指定时为 `default`。

切换成功会建立新会话，清除此前的图空间及待执行语句；不会把旧环境待执行语句自动移到新环境。历史图仍可查看；切换后执行的查询使用新环境。结果卡片显示所属环境和图空间。配置中含密码，应直接填写到原生设置；它仍按 Codex 的本地配置存储方式保存，工具列表和结果不会返回密码。

这两个环境工具不操作 Codex 原生 MCP 服务器开关。独立的 dev_nebula3、prod_nebula3 等服务器使用 README 中的切换方式，无需填写 NEBULA_ENVIRONMENTS。

## 安装冲突与卸载

传统 MCP 模式升级保留已管理的注册及环境变量。遇到非本安装器的同名条目，应先在 Codex 中检查名称和启动命令。

`--replace-registration` 仅供确认要替换旧注册时使用，会删除旧条目的环境变量，不会合并或恢复；使用前自行保留连接信息。

插件模式卸载先移除插件和本地 marketplace，再删除受管目录；移除失败时保留安装文件以便重试。删除独立 nebula3 注册可能重新显露插件同名服务器，若不使用它，请在原生设置中关闭。

## 其他 MCP 客户端

安装 Release 中的 wheel，并将 `nebula3-mcp` 设为 STDIO 启动命令，各连接字段作为环境变量传入。Server 不自动读取工作区 .env。完整字段见 [.env.example](../.env.example)。
