# GPU-Bridge

通过共享文件系统在远程 GPU 机器上执行命令和脚本的工具。

## 架构说明

本系统由两部分组成：

- **daemon（守护进程）**：运行在目标 GPU 机器上，轮询任务目录并执行任务
- **client（客户端）**：运行在任何能访问共享文件系统的机器上，提交和管理任务

两者通过共享文件系统（`gpu-bridge/tasks/`）中的 JSON 文件进行通信，无需网络连接。

## 快速开始

### 1. 启动守护进程（在目标 GPU 机器上）

```bash
# 默认 4 个并行 worker
python gpu-bridge/daemon.py

# 自定义并行数
python gpu-bridge/daemon.py --max-workers=8

# 多机器场景：给每台 GPU 机器指定唯一 node id
python gpu-bridge/daemon.py --node-id=gpu-a-h200-megatron --max-workers=4
python gpu-bridge/daemon.py --node-id=gpu-b-h100-vllm --max-workers=4

# 停止守护进程
python gpu-bridge/daemon.py --stop
python gpu-bridge/daemon.py --node-id=gpu-a-h200-megatron --stop
```

### 2. 提交任务（在客户端）

```bash
# 执行 shell 命令
python gpu-bridge/client.py run "nvidia-smi"

# 执行并实时查看输出
python gpu-bridge/client.py run "python train.py --epochs 10" -f

# 指定工作目录和超时时间（秒）
python gpu-bridge/client.py run "bash train.sh" -w /path/to/project -t 3600

# 指定目标 GPU 机器
python gpu-bridge/client.py run "bash train_a.sh" --target gpu-a-h200-megatron -f
python gpu-bridge/client.py run "bash train_b.sh" --target gpu-b-h100-vllm -f

# 执行 Python 脚本（会将脚本文件复制到远程机器）
python gpu-bridge/client.py run-script my_script.py -f
python gpu-bridge/client.py run-script my_script.py --target gpu-a-h200-megatron -f

# 同步文件到远程机器指定路径
python gpu-bridge/client.py sync local_file.py local_dir/ /target/path
```

## 任务管理

### 查看任务状态

```bash
# 列出所有任务
python gpu-bridge/client.py list

# 按状态过滤（pending / running / done / failed / cancelled）
python gpu-bridge/client.py list -s running

# 查看单个任务详情
python gpu-bridge/client.py status <task_id>
```

### 查看任务输出

```bash
# 查看已完成任务的输出
python gpu-bridge/client.py logs <task_id>

# 实时跟踪输出（类似 tail -f）
python gpu-bridge/client.py logs <task_id> -f
```

即使执行过 `clean` 清理了运行时数据，`logs` 命令仍可读取持久日志。

### 取消任务

```bash
# 发送取消信号
python gpu-bridge/client.py cancel <task_id>

# 取消并等待任务完全终止（推荐）
python gpu-bridge/client.py cancel <task_id> --wait
```

取消流程：先发送 SIGTERM 给进程组，等待 5 秒，若仍未退出则发送 SIGKILL。

### 清理任务数据

```bash
# 清理已完成/失败/取消的任务（仅清理 tasks/ 目录）
python gpu-bridge/client.py clean

# 清理全部任务
python gpu-bridge/client.py clean -a
```

`clean` 只删除 `tasks/` 目录下的运行时数据，`logs/` 目录下的持久日志不受影响。

## 并行执行

守护进程默认支持 4 个任务同时执行。

```bash
# 提交多个任务（不加 -f，使用 -p 标记并行意图）
python gpu-bridge/client.py run "python job1.py" -p
python gpu-bridge/client.py run "python job2.py" -p
python gpu-bridge/client.py run "python job3.py" -p

# 等待全部完成
python gpu-bridge/client.py wait --all

# 也可以等待指定任务
python gpu-bridge/client.py wait <id1> <id2> <id3>
```

## 多机器执行

如果两台 GPU 机器共享同一个 `GPU-Bridge` 目录，不要直接启动两个未区分身份的 daemon。应给每台机器指定唯一 `node_id`，并直接把设备、镜像/运行环境等 label 写进 `node_id`，然后提交任务时用 `--target` 指定目标机器。agent 选择机器时只需要匹配 `node_id` 中的 label，不需要额外维护一套机器能力表。

推荐命名格式：`<machine>-<device>-<runtime>`，例如 `gpu-a-h200-megatron`、`gpu-b-h100-vllm`。需要多个 label 时继续追加即可，例如 `gpu-a-h200-megatron-torch24`。

```bash
# 在机器 A 上：H200 + megatron 镜像/环境
python gpu-bridge/daemon.py --node-id=gpu-a-h200-megatron --max-workers=4

# 在机器 B 上：H100 + vllm 镜像/环境
python gpu-bridge/daemon.py --node-id=gpu-b-h100-vllm --max-workers=4

# 在客户端提交到指定机器
python gpu-bridge/client.py run "bash exp_a.sh" --target gpu-a-h200-megatron -p
python gpu-bridge/client.py run "bash exp_b.sh" --target gpu-b-h100-vllm -p

# 也可以用环境变量设置默认目标
GPU_BRIDGE_TARGET_NODE=gpu-a-h200-megatron python gpu-bridge/client.py run "nvidia-smi" -f
```

daemon 的消费规则：

- `--node-id=gpu-a-h200-megatron` 只消费 `target_node == "gpu-a-h200-megatron"` 的任务。
- 未指定 `--node-id` 的默认 daemon 使用 `node_id=default`，会兼容消费没有 `target_node` 的旧任务。
- 非 default daemon 默认不会消费未指定目标的任务；如确实需要，可加 `--accept-untargeted`。

查看指定目标的任务：

```bash
python gpu-bridge/client.py list --target gpu-a-h200-megatron
python gpu-bridge/client.py history --target gpu-b-h100-vllm
```

## 机器监控

守护进程每 5 秒采集一次机器状态（GPU、CPU、内存），单机兼容模式可读取 `monitor.json`；多机器模式写入 `nodes/<node_id>/monitor.json`。`monitor --all` 默认只显示 60 秒内更新过的在线节点，机器异常关机后会自动从默认列表里消失。

```bash
# 查看当前状态
python gpu-bridge/client.py monitor

# 查看指定机器或全部在线机器（默认只显示 monitor 文件 60 秒内有 heartbeat 的节点）
python gpu-bridge/client.py monitor --node gpu-a-h200-megatron
python gpu-bridge/client.py monitor --all

# 排查异常关机/离线节点时，显示过期 monitor
python gpu-bridge/client.py monitor --all --include-stale

# 持续刷新（每 5 秒）
python gpu-bridge/client.py monitor -f

# 输出原始 JSON（方便程序解析）
python gpu-bridge/client.py monitor --json
```

输出示例：
```
=== GPU Status ===

  GPU 0: NVIDIA H200  Mem: 9395/143771 MB  Util: 85%  Temp: 75C
  GPU 1: NVIDIA H200  Mem: 9394/143771 MB  Util: 95%  Temp: 69C
  ...

  Load: 9.6 12.6 13.5
  Memory: 88174 / 1031694 MB

  Running tasks: 20260401_034156_8ja5qr
```

## 历史记录

所有执行完的任务（包括成功、失败、取消的）都会记录到 `logs/history.jsonl`，不受 `clean` 影响。

```bash
# 查看全部历史
python gpu-bridge/client.py history

# 最近 10 条
python gpu-bridge/client.py history -n 10

# 按状态过滤
python gpu-bridge/client.py history -s failed

# JSON 格式输出
python gpu-bridge/client.py history --json
```

## 目录结构

```
gpu-bridge/
├── daemon.py          # 守护进程（运行在目标 GPU 机器上）
├── client.py          # 客户端 CLI（运行在任意能访问共享文件系统的机器上）
├── tasks/             # 运行时工作区（clean 可删除）
│   └── <task_id>/
│       ├── meta.json      # 任务元信息（状态、命令等）
│       ├── result.json    # 执行结果（退出码、完成时间）
│       └── output.log     # 合并的标准输出+标准错误
├── logs/              # 持久日志（clean 不删除）
│   ├── history.jsonl      # 所有任务的执行记录
│   └── <task_id>/
│       └── output.log     # 输出日志副本
├── nodes/             # 多机器 daemon 运行时状态
│   └── <node_id>/
│       ├── monitor.json   # 该机器状态快照
│       └── daemon.pid     # 该机器守护进程 PID
├── monitor.json       # 旧版/兼容机器状态快照
└── daemon.pid         # 旧版/兼容守护进程 PID 文件
```

## 任务状态流转

```
pending → running → done      （正常完成，exit_code=0）
pending → running → failed    （执行失败，exit_code≠0）
pending → running → cancelled （用户取消，exit_code=-15）
```

## 注意事项

- 任务 ID 格式为 `YYYYMMDD_HHMMSS_xxxxxx`，基于提交时间自动生成
- 默认超时 300 秒（5 分钟），可通过 `-t` 参数调整
- `run-script` 会将脚本文件复制到任务目录，目标机器上不需要预先存在该文件
- `sync` 用于将本地文件/目录同步到目标机器上的指定路径
- 守护进程通过 SIGTERM/SIGINT 信号优雅退出，会自动清理所有活跃进程
