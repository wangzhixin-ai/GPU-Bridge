# GPU-Bridge Skill 配置指南

GPU-Bridge 可以作为 AI 编程助手的 skill/agent 使用，让 AI 自动在远程 GPU 服务器上提交、监控、管理任务。

## 前提条件

1. 共享文件系统（如 NFS）已挂载，client 和 server 能访问同一路径
2. Server（`gpu-bridge/daemon.py`）已在 GPU 服务器上启动

```bash
# 在 GPU 服务器上
cd /path/to/GPU-Bridge
python gpu-bridge/daemon.py
```

## Claude Code 配置

### 方式一：项目级 skill（推荐）

将本仓库克隆到共享文件系统上，Claude Code 在此目录下工作时会自动加载 `skills/gpu-bridge/SKILL.md`。

```
GPU-Bridge/
├── CLAUDE.md                  # 项目上下文，Claude Code 自动读取
├── gpu-bridge/
│   ├── daemon.py
│   └── client.py
└── skills/
    └── gpu-bridge/
        └── SKILL.md           # Skill 定义
```

### 方式二：全局 skill

将 skill 复制到 Claude Code 的全局 skills 目录，这样在任何项目中都可以使用：

```bash
# 创建 skill 目录
mkdir -p ~/.claude/skills/gpu-bridge

# 复制 SKILL.md（需要修改其中的路径为绝对路径）
cp skills/gpu-bridge/SKILL.md ~/.claude/skills/gpu-bridge/SKILL.md
```

修改 `~/.claude/skills/gpu-bridge/SKILL.md`，在 frontmatter 中添加元数据，并将 `client.py` 路径改为绝对路径：

```yaml
---
name: gpu-bridge
description: Execute commands and scripts on remote GPU server via shared filesystem.
argument-hint: <shell command or "status task_id" or "list">
user-invocable: true
allowed-tools: Bash, Read, Write, Grep, Glob
---
```

将所有 `python gpu-bridge/client.py` 替换为：

```
python /your/shared/path/GPU-Bridge/gpu-bridge/client.py
```

配置完成后，即可通过 `/gpu-bridge nvidia-smi` 等方式调用。

## OpenAI Codex 配置

Codex 通过项目根目录的 `AGENTS.md`（或 `codex.md`）读取指令。

### 1. 在项目根目录创建或编辑 `AGENTS.md`

将以下内容添加到你的项目的 `AGENTS.md` 中：

```markdown
## Remote GPU Execution

To run commands on the remote GPU server, use the client script:

\```bash
python /your/shared/path/GPU-Bridge/gpu-bridge/client.py run "<command>" -f
\```

Available subcommands:
- `run "<cmd>" -f` — Execute shell command, follow output
- `run-script <file.py> -f` — Execute Python script
- `list` — List all tasks
- `status <task_id>` — Show task details
- `logs <task_id>` — View task output
- `cancel <task_id>` — Cancel a task
- `monitor` — Show GPU/system status
- `wait --all` — Wait for all tasks to finish

When the user asks to run something on the GPU server, use these commands.
\```
```

### 2. 使用方式

在 Codex 对话中直接说"在 GPU 服务器上运行 xxx"，Codex 会根据 `AGENTS.md` 中的指令调用 `client.py`。

## 验证配置

配置完成后，可以用以下命令验证 skill 是否工作：

```
# Claude Code
/gpu-bridge nvidia-smi

# 或直接用自然语言
> 在 GPU 服务器上跑一下 nvidia-smi
```

如果任务一直停在 `pending`，说明 server 端的 `daemon.py` 没有启动。
