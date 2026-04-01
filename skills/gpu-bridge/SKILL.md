# GPU-Bridge Skill

Execute commands and scripts on a remote GPU server via shared filesystem.

TRIGGER: User asks to run commands, scripts, or training jobs on a remote GPU server. Or user mentions "gpu-bridge", "GPU-Bridge", "submit task".

## How it works

This skill uses a client-server architecture over a shared filesystem (NFS):
- **server (daemon.py)** runs on the GPU server, polling `gpu-bridge/tasks/` for work
- **client (client.py)** runs on any machine with filesystem access, submitting tasks

The server must be started on the GPU machine before use.

## Commands

All commands: `python gpu-bridge/client.py <subcommand> [args]`

| Command | Usage | Description |
|---------|-------|-------------|
| `run` | `run "<cmd>" [-f] [-p] [-w dir] [-t sec]` | Execute shell command |
| `run-script` | `run-script <file.py> [-f] [-p] [-w dir] [-t sec]` | Execute Python script |
| `sync` | `sync <src...> <target>` | Copy files to remote server |
| `status` | `status <task_id>` | Show task details |
| `logs` | `logs <task_id> [-f]` | View task output |
| `list` | `list [-s status]` | List tasks |
| `cancel` | `cancel <task_id> [--wait]` | Cancel a task |
| `clean` | `clean [-a]` | Remove finished tasks from tasks/ |
| `monitor` | `monitor [-f] [--json]` | Show GPU/system status |
| `history` | `history [-n N] [-s status] [--json]` | View execution history |
| `wait` | `wait <id...>` or `wait --all` | Wait for tasks to finish |

## Flags

- `-f / --follow`: Stream output in real-time
- `-p / --parallel`: Parallel submission hint (don't auto-follow)
- `-t / --timeout`: Timeout in seconds (default: 300)
- `-w / --workdir`: Working directory on remote server

## Patterns

### Single command
```bash
python gpu-bridge/client.py run "nvidia-smi" -f
```

### Long training job
```bash
python gpu-bridge/client.py run "bash train.sh" -t 86400 -f
```

### Parallel jobs
```bash
python gpu-bridge/client.py run "python exp1.py" -p
python gpu-bridge/client.py run "python exp2.py" -p
python gpu-bridge/client.py run "python exp3.py" -p
python gpu-bridge/client.py wait --all
python gpu-bridge/client.py history -n 3
```

### Check server status before submit
```bash
python gpu-bridge/client.py monitor
```

### Investigate failure
```bash
python gpu-bridge/client.py status <task_id>
python gpu-bridge/client.py logs <task_id>
```
