# 贡献指南

感谢你愿意改进 DSH Hardware Monitor。本文说明代码位置、开发环境、测试命令和 PR 流程。

## 先看哪里

| 想改什么 | 位置 |
| --- | --- |
| DSH 侧边栏面板 / 输入框挂件 / 状态工具 | `plugins/dsh-hardware-monitor/` |
| Python 采集服务 / API / 平台适配 | `hardware_monitor/`、`server.py` |
| 桌面 / 移动端网页仪表盘 | `monitor.html`、`monitor-mobile.html` |
| 安装、启动脚本 | `install-dsh.ps1`、`run.bat`、`run-hidden.vbs` |
| 测试 | `tests/` |
| 文档 | `README.md`、`plugins/dsh-hardware-monitor/README.md`、`docs/` |

插件代码、Python 服务和网页同属一个仓库，**请向本仓库提 PR，不要修改 npm 包或 awesome 列表来改代码**。awesome-dsh-plugin 只负责市场条目；插件功能改动请留在本仓库。

## 环境要求

- Windows 10/11 或 Linux（Ubuntu 22.04/24.04 已适配）；
- Python 3.10；
- Node.js 20+（只用于检查 DSH 插件前端，不参与 Python 服务运行）；
- 可选：DeepSeek Harness（本地验证侧边栏面板/挂件时需要）。

## 本地开发

```bash
git clone https://github.com/tyvtjhj/system-resource-monitor.git
cd system-resource-monitor
python -m venv .venv
```

Windows：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe server.py
```

Linux/macOS：

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python server.py
```

服务默认监听 `http://127.0.0.1:5678`。DSH 插件安装可运行 `install-dsh.ps1`，或参考 `plugins/dsh-hardware-monitor/README.md`。

## 提交前测试

Windows：

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check hardware_monitor tests server.py
node --check plugins/dsh-hardware-monitor/client.js
node --check plugins/dsh-hardware-monitor/lib/index.js
```

Linux/macOS 把 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

涉及 DSH 面板/插件时，至少运行：

```bash
pytest tests/test_dsh_client.py tests/test_dsh_plugin.py -q
```

如果改了页面内联脚本，也请用 `node --check` 检查对应 HTML 中的脚本（或运行现有测试）。

## 分支与 PR

1. Fork 本仓库，从 `main` 建分支：`fix/...`、`feat/...` 或 `docs/...`；
2. 只改与问题相关的文件，避免顺手格式化整个仓库；
3. 补测试或说明为什么不需要测试；
4. 提交 PR 到 `main`，描述里写清楚：
   - 改了什么、为什么；
   - 如何验证（命令/截图）；
   - 影响的平台（Windows/Linux）和插件区域；
5. 关联 Issue（如有）。

## 代码约定

- Python 通过 `ruff`（E4/E7/E9/F）；新增逻辑尽量放在 `hardware_monitor/` 对应模块，不要在 `server.py` 堆采集逻辑；
- 插件前端保持零构建依赖，直接改 `plugins/dsh-hardware-monitor/client.js`；改完用 `node --check`；
- 不提交 `node_modules/`、`.venv/`、`apply.log`、`service.log`、`.tmp/` 或本机截图；
- UI 文案以中文为主，保留深色/浅色主题、`prefers-reduced-motion` 和键盘可访问性；
- 不要在 Issue/PR/日志里粘贴 token、密码、内网地址或完整用户名路径。

## 版本与 npm 发布

npm 包从 `plugins/dsh-hardware-monitor/` 发布。普通贡献者不需要发布 npm 或改 awesome 列表；合并后由维护者：

1. 更新 `plugins/dsh-hardware-monitor/package.json` 的 `version`；
2. 更新变更说明/文档；
3. 执行 `npm publish --access public`；
4. 等 `dsh-plugin-catalog` 重建后，dsh-market 才能搜到新版本。

## Issue 模板

提交 Bug 或功能建议时请使用仓库的 Issue 表单：

- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`

请先搜索是否已有相同 Issue。

## 许可证

原创代码采用 MIT（见 `LICENSE`）。提交 PR 即表示你同意按 MIT 许可证贡献原创代码。
