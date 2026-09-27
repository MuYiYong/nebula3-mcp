# 开发与维护

普通用户请使用 README 的 Release 安装方式。以下内容供修改源码、运行测试和维护发布流程时参考。

## 开发与本地验证

开发者在仓库检出后可使用 editable 安装；这不是普通用户的 Release 安装方式：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

发布构建可在干净环境验证 wheel：

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
python3 -m venv /tmp/nebula3-mcp-wheel-venv
/tmp/nebula3-mcp-wheel-venv/bin/python -m pip install dist/nebula3_mcp-*.whl
/tmp/nebula3-mcp-wheel-venv/bin/nebula3-mcp --version
```

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/python -m nebula3_mcp --help
```

真实远端测试连接 NebulaGraph 3.8 集群，需要示例图空间 `basketballplayer`（可用 `NEBULA_TEST_SPACE` 指定其他同 Schema 图空间），并在运行时提供连接字段，应使用只读账号。不要把凭据加入测试文件：

```bash
NEBULA_RUN_REMOTE_TESTS=1 \
NEBULA_ADDRESSES=HOST:9669 \
NEBULA_USERNAME=USER \
NEBULA_PASSWORD=PASSWORD \
.venv/bin/python -m pytest tests/integration -q
```

UI 验证：

```bash
cd ui
npm ci
npm test
npm run typecheck
npm run build
```

## 自动发布

推送到 `main` 后，GitHub Actions 自动运行 Python/UI 测试、类型和静态检查，以及 Linux/Windows 安装器兼容性验证。所有检查通过后构建安装包并发布 GitHub Release；PR 只验证不发布，也支持从 Actions 手动重跑主分支。

Release 标题为 `v年.月.日 Build小时分钟`，例如 `v26.09.19 Build1409`；标签为 `v26.09.19_Build1409`。时间取源提交的北京时间，重跑同一提交保持不变。Python 包使用兼容安装器的内部版本，例如 `0.1.0+build.202609191409`，确保每次构建升级到独立版本目录。

发布先上传草稿并回读校验，再公开；已公开版本不被重跑覆盖。同一分钟不同提交发生标签冲突时发布会失败，需要新的提交时间，不能覆盖旧标签。开发与发布检查不需要数据库凭据，CI 不代表已连接用户的数据库或已验收每个客户端的图形界面。

## 分发检查

公开提交不包含本机连接配置、任务记录或运行结果。安装包仅包含运行所需源码、内嵌 UI 与包元数据；插件包使用固定文件清单。发布前检查源码、历史差异和压缩包中的敏感信息，保持 SHA256SUMS 与实际资产一致。
