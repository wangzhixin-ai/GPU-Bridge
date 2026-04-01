#!/usr/bin/env python3
"""Client CLI for submitting and managing tasks via shared filesystem."""

import argparse
import json
import os
import shutil
import string
import random
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TASKS_DIR = BASE_DIR / "tasks"
LOGS_DIR = BASE_DIR / "logs"
MONITOR_FILE = BASE_DIR / "monitor.json"


def gen_task_id():
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return time.strftime("%Y%m%d_%H%M%S") + "_" + suffix


def create_task(meta, script_content=None, files_to_sync=None):
    task_id = meta["id"]
    task_dir = TASKS_DIR / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    if script_content is not None:
        (task_dir / "script.py").write_text(script_content)

    if files_to_sync:
        files_dir = task_dir / "files"
        files_dir.mkdir(exist_ok=True)
        for src in files_to_sync:
            src_path = Path(src)
            if src_path.is_dir():
                shutil.copytree(str(src_path), str(files_dir / src_path.name), dirs_exist_ok=True)
            else:
                shutil.copy2(str(src_path), str(files_dir / src_path.name))

    tmp = task_dir / "meta.json.tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    tmp.rename(task_dir / "meta.json")

    return task_id


def read_meta(task_id):
    meta_path = TASKS_DIR / task_id / "meta.json"
    if not meta_path.exists():
        return None
    with open(meta_path) as f:
        return json.load(f)


def write_meta(task_id, meta):
    task_dir = TASKS_DIR / task_id
    tmp = task_dir / "meta.json.tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    tmp.rename(task_dir / "meta.json")


def find_log_dir(task_id):
    """Find log directory: prefer tasks/, fallback to logs/."""
    task_dir = TASKS_DIR / task_id
    if task_dir.exists():
        return task_dir
    log_dir = LOGS_DIR / task_id
    if log_dir.exists():
        return log_dir
    return None


def cmd_run(args):
    task_id = gen_task_id()
    meta = {
        "id": task_id,
        "type": "shell",
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "command": args.command,
        "working_dir": args.workdir or str(BASE_DIR.parent),
        "timeout": args.timeout,
        "env": {},
    }
    create_task(meta)
    print(f"Task submitted: {task_id}")
    if args.follow:
        do_follow(task_id)


def cmd_run_script(args):
    script_path = Path(args.script)
    if not script_path.exists():
        print(f"Error: {args.script} not found", file=sys.stderr)
        sys.exit(1)
    task_id = gen_task_id()
    meta = {
        "id": task_id,
        "type": "python",
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "command": script_path.name,
        "working_dir": args.workdir or str(BASE_DIR.parent),
        "timeout": args.timeout,
        "env": {},
    }
    create_task(meta, script_content=script_path.read_text())
    print(f"Task submitted: {task_id}")
    if args.follow:
        do_follow(task_id)


def cmd_sync(args):
    sources = args.sources
    target = args.target
    task_id = gen_task_id()
    meta = {
        "id": task_id,
        "type": "file_sync",
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "command": target,
        "working_dir": str(BASE_DIR.parent),
        "timeout": 60,
        "env": {},
    }
    create_task(meta, files_to_sync=sources)
    print(f"Task submitted: {task_id}")


def cmd_status(args):
    meta = read_meta(args.task_id)
    if not meta:
        print(f"Task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    print(f"ID:      {meta['id']}")
    print(f"Type:    {meta['type']}")
    print(f"Status:  {meta['status']}")
    print(f"Created: {meta['created_at']}")
    print(f"Command: {meta.get('command', 'N/A')}")
    result_path = TASKS_DIR / args.task_id / "result.json"
    if result_path.exists():
        with open(result_path) as f:
            result = json.load(f)
        print(f"Exit:    {result['exit_code']}")
        print(f"Finished:{result['finished_at']}")


def do_follow(task_id):
    task_dir = TASKS_DIR / task_id
    output_path = task_dir / "output.log"
    pos = 0

    while True:
        if output_path.exists():
            size = output_path.stat().st_size
            if size > pos:
                with open(output_path, "r") as f:
                    f.seek(pos)
                    data = f.read()
                    if data:
                        sys.stdout.write(data)
                        sys.stdout.flush()
                    pos = f.tell()

        meta = read_meta(task_id)
        if meta and meta["status"] in ("done", "failed", "cancelled"):
            # Final read
            time.sleep(0.2)
            if output_path.exists():
                with open(output_path, "r") as f:
                    f.seek(pos)
                    rest = f.read()
                    if rest:
                        sys.stdout.write(rest)
            result_path = task_dir / "result.json"
            if result_path.exists():
                with open(result_path) as f:
                    result = json.load(f)
                print(f"\n--- Task {meta['status']} (exit code: {result['exit_code']}) ---")
            else:
                print(f"\n--- Task {meta['status']} ---")
            break

        time.sleep(0.5)


def cmd_logs(args):
    log_dir = find_log_dir(args.task_id)
    if not log_dir:
        print(f"Task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)

    if args.follow:
        do_follow(args.task_id)
        return

    output_path = log_dir / "output.log"
    if output_path.exists():
        print(output_path.read_text(), end="")
    else:
        # Fallback: try legacy stdout.log + stderr.log
        stdout_path = log_dir / "stdout.log"
        stderr_path = log_dir / "stderr.log"
        if stdout_path.exists():
            print(stdout_path.read_text(), end="")
        if stderr_path.exists():
            print(stderr_path.read_text(), end="")


def cmd_list(args):
    if not TASKS_DIR.exists():
        print("No tasks.")
        return
    tasks = []
    for d in sorted(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = read_meta(d.name)
        if not meta:
            continue
        if args.status and meta["status"] != args.status:
            continue
        tasks.append(meta)

    if not tasks:
        print("No tasks found.")
        return

    fmt = "{:<28} {:<10} {:<10} {}"
    print(fmt.format("ID", "TYPE", "STATUS", "COMMAND"))
    print("-" * 80)
    for m in tasks:
        cmd = m.get("command", "")
        if len(cmd) > 30:
            cmd = cmd[:27] + "..."
        print(fmt.format(m["id"], m["type"], m["status"], cmd))


def cmd_cancel(args):
    meta = read_meta(args.task_id)
    if not meta:
        print(f"Task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    if meta["status"] in ("done", "failed", "cancelled"):
        print(f"Task already {meta['status']}")
        return
    meta["status"] = "cancelled"
    write_meta(args.task_id, meta)
    print(f"Task {args.task_id} cancelled.")

    if args.wait:
        print("Waiting for task to finish...")
        while True:
            meta = read_meta(args.task_id)
            if not meta:
                break
            result_path = TASKS_DIR / args.task_id / "result.json"
            if meta["status"] in ("done", "failed", "cancelled") and result_path.exists():
                with open(result_path) as f:
                    result = json.load(f)
                print(f"Task terminated (exit code: {result['exit_code']})")
                break
            time.sleep(0.5)


def cmd_clean(args):
    if not TASKS_DIR.exists():
        return
    removed = 0
    for d in list(TASKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = read_meta(d.name)
        if not meta:
            continue
        if args.all or meta["status"] in ("done", "failed", "cancelled"):
            shutil.rmtree(d)
            removed += 1
    print(f"Removed {removed} task(s).")


def cmd_monitor(args):
    def print_monitor():
        if not MONITOR_FILE.exists():
            print("No monitor data available. Is the daemon running?", file=sys.stderr)
            return False
        try:
            with open(MONITOR_FILE) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            print("Failed to read monitor data.", file=sys.stderr)
            return False

        if args.json:
            print(json.dumps(data, indent=2))
            return True

        print(f"=== GPU Status ({data.get('timestamp', 'N/A')}) ===\n")

        # GPUs
        gpus = data.get("gpus", [])
        if gpus:
            fmt = "  GPU {index}: {name}  Mem: {memory_used_mb}/{memory_total_mb} MB  Util: {utilization_pct}%  Temp: {temperature_c}C"
            for g in gpus:
                print(fmt.format(**g))
        else:
            print("  No GPU data")

        # System
        sys_info = data.get("system", {})
        load = sys_info.get("load_avg")
        if load:
            print(f"\n  Load: {load[0]:.1f} {load[1]:.1f} {load[2]:.1f}")
        mem = sys_info.get("memory", {})
        if mem:
            total = mem.get("MemTotal_kb", 0)
            avail = mem.get("MemAvailable_kb", 0)
            used = total - avail
            print(f"  Memory: {used // 1024} / {total // 1024} MB")

        # Running tasks
        tasks = data.get("running_tasks", [])
        if tasks:
            print(f"\n  Running tasks: {', '.join(tasks)}")
        else:
            print(f"\n  No running tasks")

        print()
        return True

    if args.follow:
        try:
            while True:
                os.system("clear" if os.name != "nt" else "cls")
                print_monitor()
                time.sleep(5)
        except KeyboardInterrupt:
            pass
    else:
        print_monitor()


def cmd_history(args):
    history_path = LOGS_DIR / "history.jsonl"
    if not history_path.exists():
        print("No history yet.")
        return

    records = []
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if args.status:
        records = [r for r in records if r.get("status") == args.status]

    if args.last:
        records = records[-args.last:]

    if not records:
        print("No matching records.")
        return

    if args.json:
        for r in records:
            print(json.dumps(r))
        return

    fmt = "{:<28} {:<10} {:<10} {:<6} {}"
    print(fmt.format("ID", "TYPE", "STATUS", "EXIT", "COMMAND"))
    print("-" * 85)
    for r in records:
        cmd = r.get("command", "")
        if len(cmd) > 30:
            cmd = cmd[:27] + "..."
        print(fmt.format(
            r.get("id", ""),
            r.get("type", ""),
            r.get("status", ""),
            str(r.get("exit_code", "")),
            cmd,
        ))


def cmd_wait(args):
    if args.all:
        print("Waiting for all running/pending tasks...")
        while True:
            if not TASKS_DIR.exists():
                break
            active = False
            for d in TASKS_DIR.iterdir():
                if not d.is_dir():
                    continue
                meta = read_meta(d.name)
                if meta and meta["status"] in ("pending", "running"):
                    active = True
                    break
            if not active:
                print("All tasks completed.")
                break
            time.sleep(1)
    else:
        task_ids = args.task_ids
        if not task_ids:
            print("Specify task IDs or --all", file=sys.stderr)
            sys.exit(1)
        print(f"Waiting for {len(task_ids)} task(s)...")
        remaining = set(task_ids)
        while remaining:
            for tid in list(remaining):
                meta = read_meta(tid)
                if not meta or meta["status"] in ("done", "failed", "cancelled"):
                    remaining.discard(tid)
                    status = meta["status"] if meta else "not found"
                    print(f"  {tid}: {status}")
            if remaining:
                time.sleep(1)
        print("All specified tasks completed.")


def main():
    parser = argparse.ArgumentParser(description="Remote execution client")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="Run a shell command")
    p_run.add_argument("command", help="Shell command to execute")
    p_run.add_argument("--workdir", "-w", default=None)
    p_run.add_argument("--timeout", "-t", type=int, default=300)
    p_run.add_argument("--follow", "-f", action="store_true", help="Follow output")
    p_run.add_argument("--parallel", "-p", action="store_true", help="Hint: parallel submission (no auto-follow)")
    p_run.set_defaults(func=cmd_run)

    p_script = sub.add_parser("run-script", help="Run a Python script")
    p_script.add_argument("script", help="Path to Python script")
    p_script.add_argument("--workdir", "-w", default=None)
    p_script.add_argument("--timeout", "-t", type=int, default=300)
    p_script.add_argument("--follow", "-f", action="store_true", help="Follow output")
    p_script.add_argument("--parallel", "-p", action="store_true", help="Hint: parallel submission (no auto-follow)")
    p_script.set_defaults(func=cmd_run_script)

    p_sync = sub.add_parser("sync", help="Sync files to target path")
    p_sync.add_argument("sources", nargs="+", help="Source files/dirs")
    p_sync.add_argument("target", help="Target directory on remote machine")
    p_sync.set_defaults(func=cmd_sync)

    p_status = sub.add_parser("status", help="Check task status")
    p_status.add_argument("task_id")
    p_status.set_defaults(func=cmd_status)

    p_logs = sub.add_parser("logs", help="View task output")
    p_logs.add_argument("task_id")
    p_logs.add_argument("--follow", "-f", action="store_true")
    p_logs.set_defaults(func=cmd_logs)

    p_list = sub.add_parser("list", help="List tasks")
    p_list.add_argument("--status", "-s", default=None)
    p_list.set_defaults(func=cmd_list)

    p_cancel = sub.add_parser("cancel", help="Cancel a task")
    p_cancel.add_argument("task_id")
    p_cancel.add_argument("--wait", action="store_true", help="Wait for task to fully terminate")
    p_cancel.set_defaults(func=cmd_cancel)

    p_clean = sub.add_parser("clean", help="Remove completed tasks (tasks/ only, logs/ preserved)")
    p_clean.add_argument("--all", "-a", action="store_true")
    p_clean.set_defaults(func=cmd_clean)

    p_monitor = sub.add_parser("monitor", help="Show machine status")
    p_monitor.add_argument("--follow", "-f", action="store_true", help="Refresh every 5 seconds")
    p_monitor.add_argument("--json", action="store_true", help="Output raw JSON")
    p_monitor.set_defaults(func=cmd_monitor)

    p_history = sub.add_parser("history", help="View task execution history")
    p_history.add_argument("--last", "-n", type=int, default=None, help="Show last N entries")
    p_history.add_argument("--status", "-s", default=None, help="Filter by status")
    p_history.add_argument("--json", action="store_true", help="Output as JSON lines")
    p_history.set_defaults(func=cmd_history)

    p_wait = sub.add_parser("wait", help="Wait for tasks to complete")
    p_wait.add_argument("task_ids", nargs="*", help="Task IDs to wait for")
    p_wait.add_argument("--all", "-a", action="store_true", help="Wait for all running/pending tasks")
    p_wait.set_defaults(func=cmd_wait)

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
