# Terminal Runtime Skill

## 概念与设计

由于时间和环境原因，我高强度使用openclaw遥控编程。但非常明显地，openclaw作为一个通用agent，实际上其编程上的工程能力是不如专用agent的。

更理想的做法是让openclaw作为人机交互界面、主控和监督者，让专用agent进行编码工作。

agent间相互调用和协作有很多种方案，对于我来说理想的方案是“群里互@”，因为这种形式拟人且透明。
其它方案，无论通过命令行调用或同一个程序内多agent协作要么麻烦，要么无法保持上下文，要么不透明，对我来说都不是特别理想。

怎样有个简单通用的方法，能让一个agent简单透明地操作另一个agent呢？就像一个agent在看着另一个agent的cli界面并敲键盘控制它一样。

基于这个想法，于是便有了这个项目。

AI给出的定义是：
```text
一个面向 AI Agent 的 **Headless Terminal Runtime**：通过 POSIX PTY 启动并持有真实交互式 CLI/TUI 程序，维护当前虚拟屏幕，
提供 `observe / act / wait / events` 四类原语，让 Agent 能像“看屏幕 + 操作键盘”一样控制另一个 CLI Agent 或交互式程序。

目标不是实现完整 GUI 终端模拟器，而是实现 **AI 可观察、行为正确、状态可同步、安全可审计** 的终端运行时。
```

说人话的需求就是：
```text
提供一个虚拟的，可通过web api访问的终端，这个终端是标准的pty终端，能准确地记录和维护终端交互上下文。

提供API，可以查看当前的“截图”，并且可以通过API进行标准的终端交互。

这样一来被调用者会认为自己只是运行在一个真实的终端中，而调用者可以像人类一样自然地去操作它。
```

## 技术原理及限制

管道和终端是不一样的，如果程序发现自己的IO只是管道而非终端，那么该程序就不会也无法提供终端交互的高级功能。

Windows的console机制和POSIX的完全不一样，理论上本程序在Windows下不支持。

由于本程序对外提供的主要是文字“截图”，虽然支持光标和颜色的元数据信息，但不适合用来操作高亮选择的菜单。

程序提供webui供 浏览/debug/人类接手操作 。但毕竟本程序的目的不是做一个网页终端，所以不要指望它的手感能有多好。

## SKILL化

为了让agent能准确地按设计意图使用该程序，我必须将其封装成SKILL。

由于第一次写SKILL并且主要是AI代劳，所以我认为不大理想。后续我会持续改进，以后这个仓库就是一个SKILL。


## 以下是AI生成的说明

---

## 1. 核心能力

- **持久会话**：长期持有 `bash`、`vim`、`fzf`、`dialog`、CLI agent 等交互进程。
- **虚拟屏幕**：把 PTY 输出解析成当前 `visible_text`、光标、稳定时间、更新序号。
- **语义动作**：Agent 发送 `key: DOWN`、`submit`、`paste`，Runtime 根据终端状态编码为正确字节序列。
- **Terminal Mode Tracker**：跟踪影响输入行为的模式：
  - `application_cursor_keys`
  - `bracketed_paste`
  - `alternate_screen`
  - `mouse_reporting`
  - `focus_reporting`
  - `cursor_visible`
- **同步机制**：支持等待屏幕更新、屏幕稳定、输入大概率 ready、进程退出。
- **事件流**：WebSocket 推送 `screen_updated`、`input_mode_changed`、`action_sent` 等事件。
- **安全控制**：支持 actor、session lock、human takeover、危险命令拦截、audit log。
- **调试前端**：提供独立 HTML 调试页面，无需 Vite/React 构建。

---

## 2. 文件说明

```text
scripts/
  terminal_runtime_service.py          # 服务端主程序
  terminal_runtime_client.py           # 命令行 helper
  terminal_runtime_frontend.html       # 独立前端调试页面
references/
  dialog.md                            # 工具用法与示例
requirements.txt                       # Python 依赖
pyproject.toml                         # 项目配置与测试配置
tests/                                 # pytest 测试
SKILL.md                               # Skill 说明
README.md                              # 本文档
```

---

## 3. 环境要求

当前后端使用 POSIX PTY，推荐运行在：

- Linux
- macOS
- WSL

暂不支持 Windows 原生 Console / ConPTY 后端。

---

## 4. 安装与启动

### 4.1 安装依赖

```bash
python -m pip install -r requirements.txt
# 开发依赖（测试）
python -m pip install -e ".[dev]"
```

### 4.2 启动服务

```bash
python scripts/terminal_runtime_service.py
```

默认地址：

```text
http://127.0.0.1:18650
```

健康检查：

```bash
curl http://127.0.0.1:18650/health
```

---

## 5. 可选配置

服务通过环境变量配置：

```bash
export ATR_HOST=127.0.0.1
export ATR_PORT=18650
export ATR_API_TOKEN=your-secret-token
export ATR_MAX_SESSIONS=32
export ATR_IDLE_TTL_SEC=0
export ATR_RAW_LOG_LIMIT=4000
export ATR_AUDIT_LIMIT=2000
export ATR_EVENT_LIMIT=2000
python scripts/terminal_runtime_service.py
```

说明：

- `ATR_API_TOKEN` 为空时不启用鉴权。
- 设置 `ATR_API_TOKEN` 后，HTTP 请求需要：

```bash
-H "Authorization: Bearer your-secret-token"
```

WebSocket 需要：

```text
/events?token=your-secret-token
```

---

## 6. 使用前端调试页

### 6.1 方式 A：直接打开 HTML

因为后端已启用 CORS，最简单方式是直接用浏览器打开：

```text
terminal_runtime_frontend.html
```

页面顶部设置：

```text
API Base = http://127.0.0.1:18650
```

如果启用了 token，则填入 token。

### 6.2 方式 B：用任意静态服务器发布

```bash
python -m http.server 8080
```

然后打开：

```text
http://127.0.0.1:8080/terminal_runtime_frontend.html
```

### 6.3 方式 C：通过服务访问

服务启动后自带 `/ui` 路由，直接访问：

```text
http://127.0.0.1:18650/ui
```

---

## 7. API 快速示例

### 7.1 创建 session

> ⚠️ **进程生命周期须知**：ATR 只监控它直接启动的进程。当你需要控制某个特定程序（如 `vim`、`kimi`、`fzf` 等）时，**请直接将该程序作为 `command` 启动**，而不是先启动 `bash` 再在内部运行。ATR 不会追踪 shell 内的子进程。

```bash
curl -X POST http://127.0.0.1:18650/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "id": "kimi-session",
    "command": "kimi",
    "rows": 40,
    "cols": 120,
    "owner": "openclaw",
    "purpose": "run kimi CLI agent"
  }'
```

**推荐做法：直接启动目标进程**

ATR 只监控它直接创建的进程。如果需要控制 `vim`、`kimi` 或某个长时间脚本，请直接将其作为 `command` 启动：

```bash
curl -X POST http://127.0.0.1:18650/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "id": "vim-session",
    "command": "vim /path/to/file.txt",
    "rows": 30,
    "cols": 100
  }'
```

| 做法 | `process.pid` 指向 | `wait process_exit` 等待 | 状态准确性 |
|------|-------------------|-------------------------|------------|
| ✅ 直接启动 `vim` | vim 本身 | vim 退出 | 准确 |
| ❌ 启动 `bash` 再运行 `vim` | bash | bash 退出 | 子进程不可见 |

若确实需要交互式 shell，请通过屏幕输出（如回到 prompt）推断子任务状态。

### 7.2 观察当前屏幕

```bash
curl http://127.0.0.1:18650/sessions/kimi-session/observe
```

### 7.3 获取纯文本截图

```bash
curl http://127.0.0.1:18650/sessions/kimi-session/screenshot
```

### 7.4 提交命令

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/actions \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "openclaw",
    "action": {
      "type": "submit",
      "text": "echo hello && pwd && ls"
    }
  }'
```

### 7.5 发送方向键

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/actions \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "openclaw",
    "action": {
      "type": "key",
      "key": "DOWN"
    }
  }'
```

Runtime 会根据 `application_cursor_keys` 自动选择：

```text
normal mode:      ESC [ B
application mode: ESC O B
```

### 7.6 粘贴多行文本

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/actions \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "openclaw",
    "action": {
      "type": "paste",
      "text": "line1\nline2\nline3"
    }
  }'
```

如果终端处于 `bracketed_paste` 模式，Runtime 会自动包裹 bracketed paste 序列。

### 7.7 等待屏幕稳定

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/wait \
  -H "Content-Type: application/json" \
  -d '{
    "until": "screen_stable",
    "timeout_ms": 10000,
    "stable_ms": 800
  }'
```

### 7.8 等待输入大概率 ready

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/wait \
  -H "Content-Type: application/json" \
  -d '{
    "until": "input_likely_ready",
    "timeout_ms": 10000
  }'
```

---

## 8. 推荐测试软件

### 8.1 dialog

```bash
sudo apt update
sudo apt install dialog
```

在 session 中提交：

```bash
dialog --menu "Choose an action" 15 50 5 1 "Show date" 2 "List files" 3 "Print working directory" 4 "Exit" 2>/tmp/dialog-result
```

测试：

- `DOWN`
- `UP`
- `TAB`
- `ENTER`
- `ESC`

重点观察：

```json
"input_modes": {
  "application_cursor_keys": true,
  "alternate_screen": true
}
```

### 8.2 fzf

```bash
sudo apt install fzf
printf "apple\nbanana\ncherry\norange\n" | fzf \
  --pointer=">" \
  --marker="*" \
  --height=10 \
  --border=none \
  --color='fg:-1,bg:-1,hl:-1,fg+:-1,bg+:-1,hl+:-1,pointer:7,marker:7,info:7,prompt:7'
```

测试：

- 输入过滤文本
- 上下键移动
- Enter 选择

### 8.3 vim

```bash
sudo apt install vim
vim test_virtual_terminal.txt
```

测试：

- `i`
- `paste`
- `ESC`
- `:wq`
- `ENTER`

### 8.4 htop

```bash
sudo apt install htop
htop
```

测试动态刷新、事件频率和 screen stable 判断。

---

## 9. Observation Schema 说明

`GET /sessions/{id}/observe` 默认返回 Agent 视图。

核心字段：

```json
{
  "session_id": "kimi-session",
  "timestamp": 0,
  "metadata": {
    "owner": "openclaw",
    "purpose": "test terminal runtime",
    "tags": [],
    "created_at": 0,
    "last_active_at": 0
  },
  "process": {
    "state": "RUNNING",
    "pid": 123,
    "alive": true,
    "pid_exists": true,
    "reader_alive": true,
    "returncode": null,
    "command": "bash",
    "cwd": null
  },
  "screen": {
    "rows": 30,
    "cols": 100,
    "cursor": {"row": 0, "col": 0, "visible": true},
    "visible_text": "...",
    "lines": [],
    "stable_for_ms": 1000,
    "update_seq": 1,
    "last_changed_at": 0
  },
  "input_modes": {
    "application_cursor_keys": false,
    "bracketed_paste": true,
    "alternate_screen": false,
    "mouse_reporting": false,
    "focus_reporting": false,
    "cursor_visible": true
  },
  "detected_state": {
    "input_readiness": {
      "status": "likely_ready",
      "confidence": 0.8,
      "reasons": ["screen_stable", "prompt_detected"]
    },
    "prompt_likely": true,
    "shell_prompt_likely": true,
    "tui_likely": false,
    "menu_likely": false,
    "confirmation_likely": false,
    "error_likely": false
  },
  "control": {
    "lock": {"active": false, "actor": null, "lease_until": null},
    "human_takeover": false,
    "human_takeover_actor": null,
    "human_takeover_reason": null
  }
}
```

---

## 10. Action Schema 说明

所有 action 都是语义动作，不建议 Agent 直接发送 escape 字节。

### 10.1 submit

```json
{
  "actor": "openclaw",
  "action": {
    "type": "submit",
    "text": "echo hello"
  }
}
```

### 10.2 text

```json
{
  "actor": "openclaw",
  "action": {
    "type": "text",
    "text": "abc"
  }
}
```

### 10.3 paste

```json
{
  "actor": "openclaw",
  "action": {
    "type": "paste",
    "text": "multi-line\ncontent"
  }
}
```

### 10.4 key

```json
{
  "actor": "openclaw",
  "action": {
    "type": "key",
    "key": "DOWN"
  }
}
```

支持常用 key：

```text
ENTER, TAB, SHIFT_TAB, ESC, BACKSPACE, DELETE, INSERT,
UP, DOWN, LEFT, RIGHT, HOME, END, PAGEUP, PAGEDOWN,
F1 ~ F12
```

### 10.5 control

```json
{
  "actor": "openclaw",
  "action": {
    "type": "control",
    "key": "CTRL_C"
  }
}
```

### 10.6 resize

```json
{
  "actor": "openclaw",
  "action": {
    "type": "resize",
    "rows": 40,
    "cols": 120
  }
}
```

---

## 11. Lock 与 Human Takeover

### 11.1 获取锁

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/locks/acquire \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "openclaw",
    "lease_ms": 30000
  }'
```

返回：

```json
{
  "ok": true,
  "lock_token": "...",
  "lease_until": 0
}
```

### 11.2 释放锁

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/locks/release \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "openclaw",
    "lock_token": "..."
  }'
```

### 11.3 Human takeover

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/takeover/start \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "human",
    "reason": "manual debug"
  }'
```

结束：

```bash
curl -X POST http://127.0.0.1:18650/sessions/kimi-session/takeover/end \
  -H "Content-Type: application/json" \
  -d '{
    "actor": "human"
  }'
```

---

## 12. Audit 与 Debug

### 12.1 Audit log

```bash
curl http://127.0.0.1:18650/sessions/kimi-session/audit
```

### 12.2 Recent events

```bash
curl http://127.0.0.1:18650/sessions/kimi-session/events/recent
```

### 12.3 Raw log

```bash
curl http://127.0.0.1:18650/sessions/kimi-session/logs/raw
```

### 12.4 Debug observe

```bash
curl "http://127.0.0.1:18650/sessions/kimi-session/observe?view=debug"
```

---

## 13. WebSocket

连接：

```text
ws://127.0.0.1:18650/sessions/kimi-session/events
```

如果启用了 token：

```text
ws://127.0.0.1:18650/sessions/kimi-session/events?token=your-secret-token
```

典型事件：

```text
connected
session_started
screen_updated
input_mode_changed
action_sent
dangerous_action_blocked
lock_acquired
lock_released
human_takeover_started
human_takeover_ended
process_exited
```

---

## 14. OpenClaw 接入建议

推荐把 Runtime 暴露成三个核心 skill：

```text
terminal_observe
terminal_act
terminal_wait
```

### 14.1 terminal_observe

输入：

```json
{
  "session_id": "kimi-session"
}
```

输出：直接返回 `GET /sessions/{id}/observe` 的 Agent 视图。

### 14.2 terminal_act

输入：

```json
{
  "session_id": "kimi-session",
  "actor": "openclaw",
  "action": {
    "type": "key",
    "key": "DOWN"
  }
}
```

### 14.3 terminal_wait

输入：

```json
{
  "session_id": "kimi-session",
  "until": "screen_stable",
  "timeout_ms": 30000,
  "stable_ms": 1000
}
```

### 14.4 推荐 Agent 协议

给 OpenClaw 的系统说明中建议加入：

```text
你正在控制一个持久终端会话。
每次操作前先 observe。
每次操作后 wait screen_stable 或 wait input_likely_ready。
不要直接发送 ANSI escape；使用语义 action。
如果 detected_state.confirmation_likely 为 true，先向用户确认。
如果 control.human_takeover 为 true，停止操作并等待。
如果 action 返回 requires_approval，停止并向用户解释。
```

---

## 15. 当前边界

当前服务端仍然不是完整 xterm：

- 没有 cell attribute 输出。
- 没有鼠标点击编码。
- 没有 tmux persistence。
- 没有 Windows ConPTY backend。
- 没有复杂多租户权限系统。

但它已经覆盖 AI 操作交互式 CLI 所需的最小生产可用核心：

```text
PTY + screen text + mode tracker + semantic action + wait/events + lock/takeover + audit/safety
```

---

## 16. 典型验证顺序

建议按顺序测试：

```text
1. bash 普通命令
2. dialog 上下键和 alternate screen
3. fzf 输入过滤
4. vim raw mode
5. htop 动态刷新
6. dangerous action block
7. lock
8. human takeover
9. WebSocket events
```

---

## 17. 故障排查

### 页面连不上服务

检查服务：

```bash
curl http://127.0.0.1:18650/health
```

检查前端顶部：

```text
API Base
Token
```

### WSL 访问问题

如果 Windows 浏览器访问不了 WSL 中的 `127.0.0.1`，尝试：

```bash
hostname -I
```

然后使用：

```text
http://<WSL_IP>:18650
```

或者启动服务时设置：

```bash
export ATR_HOST=0.0.0.0
python scripts/terminal_runtime_service.py
```

### TUI 程序方向键异常

观察：

```bash
curl "http://127.0.0.1:18650/sessions/kimi-session/observe?view=debug"
```

重点看：

```json
"application_cursor_keys": true
```

如果为 true，Runtime 会自动发送 application cursor sequence。

### 输出很乱

优先看：

```bash
curl http://127.0.0.1:18650/sessions/kimi-session/screenshot
```

不要直接看 raw log。raw log 包含 ANSI 控制序列，是调试用途。
