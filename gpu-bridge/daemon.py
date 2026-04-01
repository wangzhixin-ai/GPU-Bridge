#!/usr/bin/env python3
"""GPU-side daemon that polls for and executes tasks from the shared filesystem."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TASKS_DIR = BASE_DIR / "tasks"
LOGS_DIR = BASE_DIR / "logs"
MONITOR_FILE = BASE_DIR / "monitor.json"
PID_FILE = BASE_DIR / "daemon.pid"
POLL_INTERVAL = 1
MAX_WORKERS = 4

running = True
active_processes = {}  # task_id -> subprocess.Popen
lock = threading.Lock()
history_lock = threading.Lock()


def sig_handler(signum, frame):
    global running
    running = False


def read_meta(task_dir):
    meta_path = task_dir / "meta.json"
    if not meta_path.exists():
        return None
    try:
        with open(meta_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_meta(task_dir, meta):
    tmp = task_dir / "meta.json.tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    tmp.rename(task_dir / "meta.json")


def write_result(task_dir, exit_code):
    result = {
        "exit_code": exit_code,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    tmp = task_dir / "result.json.tmp"
    with open(tmp, "w") as f:
        json.dump(result, f, indent=2)
    tmp.rename(task_dir / "result.json")


def pipe_to_files(pipe, paths):
    """Read from pipe and write to multiple files simultaneously."""
    files = [open(p, "ab") for p in paths]
    try:
        for line in iter(pipe.readline, b""):
            for f in files:
                f.write(line)
                f.flush()
    finally:
        pipe.close()
        for f in files:
            f.close()


def append_history(meta, exit_code):
    """Append a completed task record to history.jsonl."""
    record = {
        "id": meta["id"],
        "type": meta.get("type", "shell"),
        "command": meta.get("command", ""),
        "status": meta.get("status", "unknown"),
        "created_at": meta.get("created_at", ""),
        "started_at": meta.get("started_at", ""),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exit_code": exit_code,
        "working_dir": meta.get("working_dir", ""),
    }
    history_path = LOGS_DIR / "history.jsonl"
    with history_lock:
        with open(history_path, "a") as f:
            f.write(json.dumps(record) + "\n")


def execute_task(task_dir, meta):
    task_id = meta["id"]
    meta["status"] = "running"
    meta["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    write_meta(task_dir, meta)

    # Create persistent log directory
    log_dir = LOGS_DIR / task_id
    log_dir.mkdir(parents=True, exist_ok=True)

    task_type = meta.get("type", "shell")
    working_dir = meta.get("working_dir", str(BASE_DIR.parent))
    timeout = meta.get("timeout", 300)
    env = os.environ.copy()
    env.update(meta.get("env", {}))

    output_task = task_dir / "output.log"
    output_log = log_dir / "output.log"

    try:
        if task_type == "shell":
            cmd = meta["command"]
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=working_dir, env=env, start_new_session=True,
            )
        elif task_type == "python":
            script = task_dir / "script.py"
            proc = subprocess.Popen(
                [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=working_dir, env=env, start_new_session=True,
            )
        elif task_type == "file_sync":
            target = meta["command"]
            src_dir = task_dir / "files"
            os.makedirs(target, exist_ok=True)
            import shutil
            for item in src_dir.iterdir():
                dest = os.path.join(target, item.name)
                if item.is_dir():
                    shutil.copytree(str(item), dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(str(item), dest)
            meta["status"] = "done"
            write_meta(task_dir, meta)
            write_result(task_dir, 0)
            output_task.touch()
            output_log.touch()
            append_history(meta, 0)
            return
        else:
            meta["status"] = "failed"
            write_meta(task_dir, meta)
            write_result(task_dir, -1)
            err_msg = f"Unknown task type: {task_type}\n"
            for p in (output_task, output_log):
                with open(p, "w") as f:
                    f.write(err_msg)
            append_history(meta, -1)
            return
    except Exception as e:
        meta["status"] = "failed"
        write_meta(task_dir, meta)
        write_result(task_dir, -1)
        err_msg = f"Failed to start: {e}\n"
        for p in (output_task, output_log):
            with open(p, "w") as f:
                f.write(err_msg)
        append_history(meta, -1)
        return

    with lock:
        active_processes[task_id] = proc

    # Dual-write merged output to both tasks/ and logs/
    t_out = threading.Thread(target=pipe_to_files, args=(proc.stdout, [output_task, output_log]), daemon=True)
    t_out.start()

    # Poll loop with cancellation detection
    deadline = time.time() + timeout
    cancelled = False
    while proc.poll() is None:
        # Check cancellation
        current_meta = read_meta(task_dir)
        if current_meta and current_meta.get("status") == "cancelled":
            cancelled = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
            # Wait up to 5 seconds for graceful exit
            for _ in range(50):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            break
        # Check timeout
        if time.time() > deadline:
            proc.kill()
            proc.wait()
            break
        time.sleep(1)

    t_out.join(timeout=5)

    with lock:
        active_processes.pop(task_id, None)

    exit_code = proc.returncode
    if cancelled:
        meta["status"] = "cancelled"
        write_meta(task_dir, meta)
        write_result(task_dir, -15)
        append_history(meta, -15)
    else:
        meta["status"] = "done" if exit_code == 0 else "failed"
        write_meta(task_dir, meta)
        write_result(task_dir, exit_code)
        append_history(meta, exit_code)


def monitor_loop():
    """Periodically collect system/GPU stats and write monitor.json."""
    while running:
        try:
            snapshot = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "gpus": [],
                "system": {},
                "running_tasks": [],
            }

            # GPU info
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    for line in result.stdout.strip().split("\n"):
                        if not line.strip():
                            continue
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 6:
                            snapshot["gpus"].append({
                                "index": int(parts[0]),
                                "name": parts[1],
                                "memory_used_mb": int(parts[2]),
                                "memory_total_mb": int(parts[3]),
                                "utilization_pct": int(parts[4]),
                                "temperature_c": int(parts[5]),
                            })
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
                pass

            # System info
            try:
                load = os.getloadavg()
                snapshot["system"]["load_avg"] = list(load)
            except OSError:
                pass

            try:
                with open("/proc/meminfo") as f:
                    meminfo = {}
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val = parts[1].strip().split()[0]
                            if key in ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached"):
                                meminfo[key + "_kb"] = int(val)
                    snapshot["system"]["memory"] = meminfo
            except (OSError, ValueError):
                pass

            # Running tasks
            with lock:
                snapshot["running_tasks"] = list(active_processes.keys())

            # Atomic write
            tmp = MONITOR_FILE.parent / "monitor.json.tmp"
            with open(tmp, "w") as f:
                json.dump(snapshot, f, indent=2)
            tmp.rename(MONITOR_FILE)

        except Exception:
            pass

        for _ in range(50):  # 5 seconds in 0.1s increments, checking running
            if not running:
                break
            time.sleep(0.1)


def get_pending_tasks():
    if not TASKS_DIR.exists():
        return []
    tasks = []
    for d in TASKS_DIR.iterdir():
        if not d.is_dir():
            continue
        meta = read_meta(d)
        if meta and meta.get("status") == "pending":
            tasks.append((d, meta))
    tasks.sort(key=lambda x: x[1].get("created_at", ""))
    return tasks


def stop_daemon():
    if not PID_FILE.exists():
        print("No daemon PID file found.")
        return
    pid = int(PID_FILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to daemon (PID {pid}).")
    except ProcessLookupError:
        print(f"Daemon (PID {pid}) not running.")
    PID_FILE.unlink(missing_ok=True)


def main():
    if "--stop" in sys.argv:
        stop_daemon()
        return

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    max_workers = MAX_WORKERS
    for arg in sys.argv[1:]:
        if arg.startswith("--max-workers="):
            max_workers = int(arg.split("=", 1)[1])

    print(f"Daemon started (PID {os.getpid()}, max_workers={max_workers})")

    # Start monitor thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

    workers = []

    try:
        while running:
            # Clean finished threads
            workers = [t for t in workers if t.is_alive()]

            if len(workers) < max_workers:
                pending = get_pending_tasks()
                for task_dir, meta in pending:
                    if len(workers) >= max_workers:
                        break
                    t = threading.Thread(target=execute_task, args=(task_dir, meta), daemon=True)
                    t.start()
                    workers.append(t)

            time.sleep(POLL_INTERVAL)
    finally:
        print("Daemon shutting down...")
        with lock:
            for proc in active_processes.values():
                try:
                    proc.kill()
                except OSError:
                    pass
        PID_FILE.unlink(missing_ok=True)
        MONITOR_FILE.unlink(missing_ok=True)
        print("Daemon stopped.")


if __name__ == "__main__":
    main()
