---
name: terminal-runtime-skill
description: 通过 observe / act / wait 原语控制持久化的虚拟终端会话。用于交互式 CLI/TUI 程序或其他 CLI Agent 如`kimi`、`codex`。
---

# Terminal Runtime Skill

当你需要控制一个**持久化的终端会话**，比如操控另一个Agent的CLI界面时使用本Skill。

它不是一个一次性命令执行器，而是一个需要持续交互的运行环境。工作流：

```text
observe -> decide -> act -> wait -> observe again
```

Client脚本：

```text
{baseDir}/scripts/terminal_runtime_client.py
该脚本调用 Runtime 的 HTTP API，返回 JSON 或纯文本。
```

服务的默认地址为：

```text
http://127.0.0.1:18650
```

如果服务器没启动，则先启动服务器：

```text
{baseDir}/scripts/terminal_runtime_service.py
```

---

## 何时使用

- 需要控制交互式 CLI/TUI 程序（如 `vim`、`fzf`、`dialog`、`htop`）。
- 需要调用另一个 CLI Agent（如 `kimi`，`codex`）并与其协作。
- 需要持续观察屏幕状态、等待稳定、再执行下一步操作。

---

## 强制操作循环

每次控制会话都必须遵循以下循环：

1. **先 observe**：获取当前屏幕状态和检测到的 UI 状态。
2. **检查 human takeover**：如果 `control.human_takeover=true`，立即停止操作。
3. **检查 confirmation**：如果 `detected_state.confirmation_likely=true`，先询问用户再确认。
4. **发送语义动作**：使用 `text` / `submit` / `paste` / `key` / `control` / `resize`，不要直接发送原始 ANSI 转义序列。
5. **等待稳定**：每次动作后等待 `screen_stable` 或 `input_likely_ready`。
6. **再次 observe**：确认状态后再决定下一步。

---

## 进程生命周期

ATR 只监控它**直接启动**的进程。创建 session 时必须把目标程序作为 `command`，例如 `vim`、`kimi`、`fzf`、`python3 script.py`，**不要**先启动 `bash` 再在 shell 内部运行目标程序。shell 子进程对 ATR 不可见。

---

## 安全规则

出现以下任一情况时必须停止并请示用户：

- `detected_state.confirmation_likely=true`
- `control.human_takeover=true`
- 动作返回 `requires_approval=true`
- 涉及删除、覆盖、sudo、安装、网络管道到 shell、关机、重启或不可逆变更

不得擅自确认破坏性提示。

---

## 标准操作序列

```text
1. 调用 observe
2. 若安全且 ready，调用 act
3. 调用 wait --until screen_stable
4. 再次调用 observe
5. 总结或继续
```

---

## 详细用法

工具调用示例、Observation 字段解释、Shell/TUI/CLI Agent 行为、安全规则以及 `dialog` 测试用例见：

```text
{baseDir}/references/dialog.md
```
