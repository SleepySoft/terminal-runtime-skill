## 工具 helper 用法

通过本地 helper 脚本使用 `exec`/shell 工具调用。

### 观察屏幕

```bash
python {baseDir}/scripts/terminal_runtime_client.py observe --session-id bash-main
```

调试视图：

```bash
python {baseDir}/scripts/terminal_runtime_client.py observe --session-id bash-main --view debug
```

### 截图

```bash
python {baseDir}/scripts/terminal_runtime_client.py screenshot --session-id bash-main
```

### 输入文本并回车

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type submit --text 'echo hello && pwd && ls'
```

### 发送按键

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type key --key DOWN
```

### 粘贴多行文本

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type paste --text 'line1
line2
line3'
```

### 等待

```bash
python {baseDir}/scripts/terminal_runtime_client.py wait --session-id bash-main --until screen_stable --timeout-ms 30000 --stable-ms 1000
```

### 锁

```bash
python {baseDir}/scripts/terminal_runtime_client.py lock-acquire --session-id bash-main --actor openclaw --lease-ms 30000
```

然后将返回的 lock token 传给动作：

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --lock-token '<token>' --type key --key DOWN
```

---

## 进程生命周期

ATR 只监控它**直接启动**的进程。创建 session 时必须把目标程序作为 `command`，不要通过 shell 包装：

| 方式 | `process.pid` 指向 | `wait process_exit` 等待 | 是否可用 |
|------|-------------------|-------------------------|---------|
| ✅ 直接：`vim`、`kimi`、`fzf` | 目标程序 | 目标程序 | 是 |
| ❌ 间接：先启动 `bash` 再运行 | `bash` | `bash` 退出 | 否（子进程不可见）|

如果需要交互式 shell，请通过屏幕输出推断子进程状态（例如 prompt 是否返回）。

---

## 如何解读 observation

重点关注以下字段：

```json
{
  "screen": {
    "visible_text": "当前屏幕文本",
    "stable_for_ms": 1200,
    "update_seq": 10
  },
  "input_modes": {
    "application_cursor_keys": true,
    "bracketed_paste": true,
    "alternate_screen": true
  },
  "detected_state": {
    "input_readiness": {
      "status": "likely_ready",
      "confidence": 0.8,
      "reasons": ["screen_stable", "prompt_detected"]
    },
    "shell_prompt_likely": true,
    "tui_likely": false,
    "menu_likely": false,
    "confirmation_likely": false,
    "error_likely": false
  },
  "control": {
    "human_takeover": false
  }
}
```

同时检查 `process` 健康状态：

- `pid_exists`：通过 `os.kill(pid, 0)` 实时检查。如果 `alive=true` 但 `pid_exists=false`，进程可能是僵尸或孤儿进程，需要重建 session。
- `reader_alive`：如果为 `false`，内部 reader 线程已崩溃，屏幕不再更新，需要重建 session。

`input_readiness` 是概率值。如果不是 `likely_ready`，请等待或重新 observe，不要盲打。

---

## Shell prompt 行为

如果 `shell_prompt_likely=true`，终端可能处于 shell 提示符等待输入。

对 shell 命令使用 `submit`：

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type submit --text 'pwd && ls'
```

在 shell 提示符下不要使用 `UP` / `DOWN` 作为菜单导航，这两个键会操作 shell 历史。

---

## TUI 行为

如果 `tui_likely=true` 或 `alternate_screen=true`，终端可能正在运行 TUI 程序，例如 `dialog`、`fzf`、`vim`、`htop` 或 `mc`。

使用语义按键动作：

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type key --key DOWN
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type key --key ENTER
```

不要发送原始转义字节，例如 `\x1b[A` 或 `\x1bOB`。Runtime 会跟踪终端模式并正确编码按键。

---

## CLI Agent 行为

控制另一个 CLI Agent 时：

1. 调用 observe。
2. 确认它已准备好输入。
3. 发送有边界的指令，最好要求它先制定计划再修改文件。
4. 等待屏幕稳定。
5. 再次 observe。
6. 向用户总结进展。
7. 遇到确认提示或危险动作拦截时停止。

示例指令：

```text
请分析当前项目并先提出计划，暂时不要修改文件。
```

---

## 安全规则

出现以下任一情况时必须停止并询问用户：

- `detected_state.confirmation_likely=true`
- `control.human_takeover=true`
- 动作结果包含 `requires_approval=true`
- 涉及删除、覆盖、sudo、安装、网络管道到 shell、关机、重启或不可逆变更

不得擅自确认破坏性提示。

---

## 标准序列

```text
1. observe
2. 若安全且 ready，act
3. wait screen_stable
4. observe
5. 总结或继续
```

---

# Dialog 测试

在 Agent Terminal Runtime 中创建一个 bash session，然后 submit：

```bash
dialog --menu "Choose an action" 15 50 5 1 "Show date" 2 "List files" 3 "Print working directory" 4 "Exit" 2>/tmp/dialog-result
```

观察：

```bash
python {baseDir}/scripts/terminal_runtime_client.py observe --session-id bash-main
```

发送 DOWN：

```bash
python {baseDir}/scripts/terminal_runtime_client.py act --session-id bash-main --actor openclaw --type key --key DOWN
```

等待并观察：

```bash
python {baseDir}/scripts/terminal_runtime_client.py wait --session-id bash-main --until screen_stable
python {baseDir}/scripts/terminal_runtime_client.py observe --session-id bash-main
```
