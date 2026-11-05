# 工作计划：GNOME 指示动画、不闪屏截图、减少往返

日期 2026-11-04。基线分支 `perf/structured-first-compact-results`（紧凑 JSON 返回、结构化优先的服务说明，已完成）。

本文件只放在 `plan/gnome-indicator` 分支，不进任何给上游的 PR。动手前先读 `AGENTS.md`。

## 已核实的事实

- 截图闪屏：测试机是 GNOME 50.1 Wayland，没装 grim 和 gnome-screenshot，所以 vadgr 截图走 `org.freedesktop.portal.Screenshot`（`platform/backends/portal.py`）。xdg-desktop-portal-gnome 的 `screenshotdialog.c` 调 shell 截图时 flash 参数写死为 `TRUE`，已授权、跳过对话框时也照样闪。
- Wayland 下普通客户端画不了全局置顶、点击穿透的覆盖层，Mutter 也不支持 wlr-layer-shell。只有 gnome-shell 扩展能画。
- 官方 Win/Mac 版的表现：第一次操作时通知 "Claude is using your computer · press Esc to stop"；Esc 在任何地方都能停，按键被吞掉；点击或输入前在目标处画高亮；工作时隐藏其他应用；截图自动缩放。
- vadgr 已经缩放截图：`_compute_max_width` 把 2560 宽缩到 1366，每张约 1.5k token，和官方同一档。不再改。
- `ui_act(ref, action, text)` 只接受 ref，必须先调用 `ui_find`，所以原生应用每次交互要 2 轮。
- Flutter 卡顿：AT-SPI 每次调用的超时是 5 秒（`tools/ui/atspi.py`，`call_timeout=5.0`）。Flutter 节点的 `GetExtents`/`GetState` 不回应，每个节点都等满 5 秒；实测一个 Flutter 应用的 `ui_tree` 要 25 秒。

## 步骤 1：GNOME Shell 扩展（指示动画 + 不闪屏截图）

位置：`computer_use/platform/gnome_extension/<uuid>/`，包含 `metadata.json`、`extension.js`、`stylesheet.css`。ESM 格式（GNOME 45 及以上）。`shell-version` 只写 `["50"]`，因为只在 50 上验证过。

session bus 上的 D-Bus 方法（名字由实现者定）：

- `Begin(tool, text, x, y, w, h, own_escape) -> stopped`：亮起光框，更新胶囊文字；坐标有效时，在该点画圆环，或者画矩形框（w、h 大于 0 时）。返回值表示用户最近 30 秒内是否按过 Esc。
- `Screenshot(path, x, y, w, h)`：在 shell 进程内截图（`Shell.Screenshot`，用法以 GNOME 50 的 `js/ui/screenshot.js` 为准）；w=h=0 表示全屏。截图里不能出现覆盖层：在同一个主循环 tick 里先隐藏覆盖层、截图、再显示，实测确认。每次截图都必须亮起光框，不允许静默截屏，因为同一用户的任何进程都能调用这个接口。

视觉效果：

- 光框：每个显示器四条边都有橙色渐变，带呼吸动画；最后一次 `Begin` 之后 15 秒淡出。
- 胶囊：在顶部居中，显示当前动作和停止提示，用英文文案，例如 `Clicking "Save" · Esc to stop`。
- 高亮：点击类动作执行前，在目标点画一个约 300 ms 的扩散圆环；能拿到可信屏幕坐标的控件，画矩形框。
- 所有 actor 都设 `reactive: false`，不挡点击，也不抢焦点。

Esc 停止：

- 覆盖层活跃时抓取 Escape（建议用 `global.display.grab_accelerator`，淡出时释放），按下就记录时间，并吞掉这次按键。`captured-event` 收不到发往 Wayland 窗口的按键，不要用。
- vadgr 自己要发 Esc 时，`Begin` 传 `own_escape=true`，扩展先暂时释放抓取，让这次按键到达应用，再重新抓取。
- `Begin` 返回 `stopped=true` 时，工具不执行，直接返回 `{"error": "user_stopped", ...}`，提示模型停下来等用户。30 秒后自动解除。

Python 端：

- `platform/backends/gnome_shell.py`：jeepney 客户端（项目已经用 jeepney 调 portal）。扩展不在线时，所有方法都什么也不做。
- `platform/backends/linux_providers.py`：新增一个截图 provider，优先级 65，条件是 Wayland、GNOME、扩展在 bus 上。扩展不在时，自动退回 portal。
- 指示调用接在 `mcp_server.py` 的 `_CompactMCPServer.tool()` 包装上，这是唯一入口。只对原生 UI（`ui_*`）、像素类工具（截图、点击、键盘、鼠标）和 `app_open` 生效。
- 安装：现有的 `install-deps` 子命令加上 `gnome-extensions install --force` 和 `gnome-extensions enable`，并提示用户必须注销重新登录一次（Wayland 下不能热重载 shell）。

## 步骤 2：`ui_act` 直接接受 name/role/app

- ref 为空时，用 name/role/app 查找。恰好 1 个匹配才执行；0 个时返回 not_found 和最接近的候选；多个时返回 ambiguous 和最多 5 个候选 ref。现有按 ref 的调用保持兼容。
- 更新服务说明和 docstring：原生应用的第 2 层改成直接调用 `ui_act(name=..., role=...)`，有歧义时再用 `ui_find`。
- 测试：覆盖 0、1、多个匹配，以及同时给了 ref 和 name 时 ref 优先。

## 步骤 3：Flutter 卡顿

- `GetExtents`/`GetState` 单独用 0.5 秒超时。同一个应用超时过一次后，本次遍历不再调用这两个接口，字段记为 unknown。
- 测试：模拟一个永远不返回的节点，断言总耗时少于 2 秒，其余节点照常返回。

## 步骤 4（可选，等步骤 2 的 A/B 结果出来再定）：`batch` 工具

- 参数 `steps: [{tool, args}]`，按顺序执行，遇到错误就停，返回每一步的紧凑结果。只允许步骤 1 列出的那些桌面类工具。

## 云会话能做和不能做的

- 能做：写代码；跑单元测试（pytest，D-Bus 和扩展用 mock）；ruff；`AGENTS.md` 里的私有文档名扫描；`scripts/check_no_ai_attribution.py`；扫 em dash 和 en dash。
- 不能做（要回到测试机上）：安装扩展并重新登录、看动画效果、确认截图里没有光框、测试 Esc、测 Flutter 耗时、跑 A/B。

## 测试机验收

- 装好扩展并重新登录后，`get_platform_info` 显示截图走的是扩展。
- 截图不闪；截图边缘的像素里没有橙色光框。
- 点击前出现圆环，胶囊文字正确；按 Esc 后，下一次调用返回 `user_stopped`，30 秒后恢复。
- Flutter 应用的 `ui_tree` 少于 2 秒。
- A/B：3 个任务（计算器算一个算式、文本编辑器输入并保存、Flutter 应用读一个值），Opus 和 Sonnet 各跑 2 次，用 `claude -p --strict-mcp-config --output-format stream-json`，记录轮数、耗时、总 token 和成功率。基线是 `perf/structured-first-compact-results`。

## 预期（待 A/B 验证）

- 原生应用每次交互从 2 轮变成 1 轮；5 步的任务从约 12 轮变成约 7 轮，加上 batch 约 3 轮。
- 每少一轮，就省一次整段上下文的缓存读取（约 2 到 5 万 token）和 2 到 5 秒。原生应用任务的总 token 预计再降 30% 到 60%，耗时降 40% 到 60%。
- Flutter 应用从 25 秒降到 1 到 2 秒。截图本身的 token 不变。

## 分支和提交

- 每一步一个分支，从 `perf/structured-first-compact-results` 切出（步骤 1 要用到它的包装器），推到 fork。
- 遵守 `AGENTS.md`：提交和 PR 里不写 AI 署名（CI 会检查）；不用 em dash 和 en dash；不提私有文档名；在 CHANGELOG 0.7.9 Unreleased 下补条目。
- 测试机验收通过之后，再给上游开 PR。

## 不做的

- 隐藏其他应用、按应用逐个授权：官方有这两项，但这次没提需求，而且会打断用户正在用的窗口。
