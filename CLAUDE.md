# GPU-Bridge - AI Context

This document provides comprehensive context for AI assistants interacting with GPU-Bridge.

## System Overview

This is a **shared-filesystem-based remote execution system** for running commands on a remote GPU machine. There is no network protocol — communication happens entirely through JSON files on a shared filesystem (e.g., NFS mount).

- **daemon.py** runs on the GPU machine, polling `gpu-bridge/tasks/` for new work
- **client.py** runs on any machine with shared filesystem access, creating task entries

## Architecture

### Communication Protocol

1. Client creates `tasks/<task_id>/meta.json` with `status: "pending"`
2. Daemon detects it, sets `status: "running"`, executes the command
3. Daemon streams stdout/stderr to log files in both `tasks/` and `logs/`
4. On completion, daemon writes `result.json` and updates `meta.json` status
5. Client reads these files to track progress

### Task Lifecycle

```
pending → running → done       (exit_code == 0)
pending → running → failed     (exit_code != 0)
pending → running → cancelled  (user cancel, exit_code == -15)
```

### File Layout

```
gpu-bridge/
├── daemon.py              # GPU-side daemon process
├── client.py              # Client CLI tool
├── tasks/                 # Ephemeral runtime workspace
│   └── <task_id>/
│       ├── meta.json      # Task metadata (id, type, status, command, timestamps)
│       ├── result.json    # Execution result (exit_code, finished_at)
│       └── output.log     # Merged stdout+stderr stream
├── logs/                  # Persistent logs (survives `clean`)
│   ├── history.jsonl      # Append-only, one JSON object per completed task
│   └── <task_id>/
│       └── output.log     # Duplicate of tasks/<id>/output.log
├── monitor.json           # Machine status snapshot (refreshed every 5s by daemon)
└── daemon.pid             # PID of running daemon
```

## Client Commands Reference

All commands use: `python gpu-bridge/client.py <subcommand> [args]`

### run
Execute a shell command on the remote machine.
```
run <command> [-f] [-p] [-w workdir] [-t timeout_seconds]
```
- `-f / --follow`: Stream output in real-time until task completes
- `-p / --parallel`: Hint flag for parallel submission workflows (suppresses auto-follow)
- `-w / --workdir`: Working directory on the remote machine (default: parent of gpu-bridge/)
- `-t / --timeout`: Timeout in seconds (default: 300)

### run-script
Copy and execute a Python script on the remote machine.
```
run-script <script_path> [-f] [-p] [-w workdir] [-t timeout]
```
The script file is copied into the task directory so it doesn't need to exist on the remote machine beforehand.

### sync
Copy local files/directories to a target path on the remote machine.
```
sync <source1> [source2 ...] <target_directory>
```

### status
Show detailed status of a single task.
```
status <task_id>
```

### logs
View task stdout/stderr output. Falls back to `logs/<id>/` if `tasks/<id>/` was cleaned.
```
logs <task_id> [-f]
```

### list
List all tasks with optional status filter.
```
list [-s status]
```
Status values: `pending`, `running`, `done`, `failed`, `cancelled`

### cancel
Cancel a running or pending task.
```
cancel <task_id> [--wait]
```
- `--wait`: Block until the task fully terminates and `result.json` is written
- Cancellation sends SIGTERM to the process group, waits 5s, then SIGKILL if needed

### clean
Remove task directories from `tasks/`. Does NOT touch `logs/`.
```
clean [-a]
```
- Without `-a`: only removes done/failed/cancelled tasks
- With `-a`: removes all tasks including pending/running

### monitor
Display machine status from `monitor.json`.
```
monitor [-f] [--json]
```
- `-f / --follow`: Refresh display every 5 seconds
- `--json`: Output raw JSON instead of formatted text
- Shows: GPU index/name/memory/utilization/temperature, system load, memory, running tasks

### history
View persistent task execution history from `logs/history.jsonl`.
```
history [-n N] [-s status] [--json]
```
- `-n / --last N`: Show only the last N entries
- `-s / --status`: Filter by status (done, failed, cancelled)
- `--json`: Output as JSON lines

### wait
Block until specified tasks (or all tasks) reach a terminal state.
```
wait <task_id1> [task_id2 ...]
wait --all
```

## Daemon Details

### daemon.py

**Startup**: `python gpu-bridge/daemon.py [--max-workers=N]`
**Shutdown**: `python gpu-bridge/daemon.py --stop` or send SIGTERM/SIGINT

Key behaviors:
- **Polling**: Checks `tasks/` every 1 second for `status: "pending"` tasks
- **Concurrency**: Default 4 worker threads, configurable via `--max-workers`
- **Process groups**: Uses `start_new_session=True` so child processes form their own process group. This enables clean cancellation of shell commands and their subprocesses via `os.killpg()`
- **Merged output**: stdout and stderr are combined into a single `output.log` via `stderr=subprocess.STDOUT`, so all process output appears in one file in chronological order
- **Cancellation detection**: Each worker thread polls `meta.json` every second. When `status == "cancelled"` is detected: SIGTERM → wait 5s → SIGKILL
- **Monitor thread**: A background thread runs `monitor_loop()`, collecting GPU stats (via `nvidia-smi`), system load (`os.getloadavg()`), and memory info (`/proc/meminfo`) every 5 seconds. Written atomically to `monitor.json`
- **History**: After each task completes, a JSON record is appended to `logs/history.jsonl` under a thread lock

### Task Types

| Type | Description | How it runs |
|------|-------------|-------------|
| `shell` | Shell command string | `subprocess.Popen(cmd, shell=True, ...)` |
| `python` | Python script file | Script copied to task dir, run with `sys.executable` |
| `file_sync` | File copy operation | Uses `shutil.copytree`/`copy2` to target path |

### meta.json Schema
```json
{
  "id": "20260401_034156_8ja5qr",
  "type": "shell",
  "status": "pending",
  "created_at": "2026-04-01T03:41:56",
  "started_at": "2026-04-01T03:41:57",
  "command": "python train.py",
  "working_dir": "/path/to/workdir",
  "timeout": 300,
  "env": {}
}
```

### result.json Schema
```json
{
  "exit_code": 0,
  "finished_at": "2026-04-01T03:42:00"
}
```

### history.jsonl Record Schema
```json
{
  "id": "20260401_034156_8ja5qr",
  "type": "shell",
  "command": "python train.py",
  "status": "done",
  "created_at": "2026-04-01T03:41:56",
  "started_at": "2026-04-01T03:41:57",
  "finished_at": "2026-04-01T03:42:00",
  "exit_code": 0,
  "working_dir": "/path/to/workdir"
}
```

### monitor.json Schema
```json
{
  "timestamp": "2026-04-01T03:41:42",
  "gpus": [
    {
      "index": 0,
      "name": "NVIDIA H200",
      "memory_used_mb": 9395,
      "memory_total_mb": 49140,
      "utilization_pct": 85,
      "temperature_c": 75
    }
  ],
  "system": {
    "load_avg": [9.6, 12.6, 13.5],
    "memory": {
      "MemTotal_kb": 1056454656,
      "MemFree_kb": 900000000,
      "MemAvailable_kb": 950000000,
      "Buffers_kb": 1000000,
      "Cached_kb": 50000000
    }
  },
  "running_tasks": ["20260401_034156_8ja5qr"]
}
```

## Usage Patterns for AI Assistants

### Running a single command and checking output
```bash
python gpu-bridge/client.py run "nvidia-smi" -f
```

### Running a long training job
```bash
python gpu-bridge/client.py run "python train.py --epochs 100" -t 86400 -f
```

### Parallel job submission
```bash
python gpu-bridge/client.py run "python exp1.py" -p
python gpu-bridge/client.py run "python exp2.py" -p
python gpu-bridge/client.py run "python exp3.py" -p
python gpu-bridge/client.py wait --all
python gpu-bridge/client.py history -n 3
```

### Checking machine status before submitting
```bash
python gpu-bridge/client.py monitor
```

### Investigating a failed task
```bash
python gpu-bridge/client.py status <task_id>
python gpu-bridge/client.py logs <task_id>
```

### Cleaning up after many runs
```bash
python gpu-bridge/client.py clean
python gpu-bridge/client.py history -n 20
```

## Important Notes

- **Atomic writes**: All JSON files use tmp+rename pattern to prevent partial reads
- **Thread safety**: `history.jsonl` writes are protected by a dedicated lock; `active_processes` dict uses a separate lock
- **Graceful shutdown**: Daemon catches SIGTERM/SIGINT, kills all active processes, cleans up PID and monitor files
- **No network required**: Everything operates through the shared filesystem
- **Task IDs**: Format is `YYYYMMDD_HHMMSS_xxxxxx` (timestamp + 6 random alphanumeric chars)
- **Default timeout**: 300 seconds (5 minutes) — always set appropriate timeout for long-running tasks
- **Log persistence**: `logs/` survives `clean` operations. To fully remove history, manually delete the `logs/` directory
