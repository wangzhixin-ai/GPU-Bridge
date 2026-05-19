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
| `run` | `run "<cmd>" [-f] [-p] [-w dir] [-t sec] [-N node]` | Execute shell command |
| `run-script` | `run-script <file.py> [-f] [-p] [-w dir] [-t sec] [-N node]` | Execute Python script |
| `sync` | `sync <src...> <target> [-N node]` | Copy files to remote server |
| `status` | `status <task_id>` | Show task details |
| `logs` | `logs <task_id> [-f]` | View task output |
| `list` | `list [-s status] [-N node]` | List tasks |
| `cancel` | `cancel <task_id> [--wait]` | Cancel a task |
| `clean` | `clean [-a]` | Remove finished tasks from tasks/ |
| `monitor` | `monitor [-N node] [--all] [--include-stale] [--ttl sec] [-f] [--json]` | Show GPU/system status |
| `history` | `history [-n N] [-s status] [-N node] [--json]` | View execution history |
| `wait` | `wait <id...>` or `wait --all` | Wait for tasks to finish |

## Flags

- `-f / --follow`: Stream output in real-time
- `-p / --parallel`: Parallel submission hint (don't auto-follow)
- `-t / --timeout`: Timeout in seconds (default: 300)
- `-w / --workdir`: Working directory on remote server
- `-N / --target`: Target daemon node id for `run`/`run-script`; `--target-node` for `sync`; node filter for `list`/`history`/`monitor`

## Multi-node routing

Use unique daemon node ids when multiple GPU machines share one queue. Put routing labels directly in the node id, including device and runtime/image labels, so agents can choose by matching labels in the id.

Recommended node id shape: `<machine>-<device>-<runtime>`, for example `gpu-a-h200-megatron` or `gpu-b-h100-vllm`.

```bash
python gpu-bridge/daemon.py --node-id=gpu-a-h200-megatron --max-workers=4
python gpu-bridge/daemon.py --node-id=gpu-b-h100-vllm --max-workers=4
python gpu-bridge/client.py run "bash exp_a.sh" --target gpu-a-h200-megatron -p
python gpu-bridge/client.py run "bash exp_b.sh" --target gpu-b-h100-vllm -p
python gpu-bridge/client.py monitor --all
```

Agent routing rule: infer required labels from the task, inspect online node ids with `monitor --all`, then submit to a `node_id` containing all required labels. `monitor --all` hides stale nodes by default using a 60s monitor-file heartbeat TTL; use `--include-stale` only for diagnosis. If no online node id matches, ask the user instead of guessing. Non-default daemons only consume tasks explicitly targeted to their `node_id`.

## Patterns

### Single command
```bash
python gpu-bridge/client.py run "nvidia-smi" --target gpu-a-h200-megatron -f
```

### Long training job
```bash
python gpu-bridge/client.py run "bash train.sh" --target gpu-a-h200-megatron -t 86400 -f
```

### Parallel jobs
```bash
python gpu-bridge/client.py run "python exp1.py" --target gpu-a-h200-megatron -p
python gpu-bridge/client.py run "python exp2.py" --target gpu-a-h200-megatron -p
python gpu-bridge/client.py run "python exp3.py" --target gpu-b-h100-vllm -p
python gpu-bridge/client.py wait --all
python gpu-bridge/client.py history -n 3
```

### Check server status before submit
```bash
python gpu-bridge/client.py monitor --all
```

### Investigate failure
```bash
python gpu-bridge/client.py status <task_id>
python gpu-bridge/client.py logs <task_id>
```
