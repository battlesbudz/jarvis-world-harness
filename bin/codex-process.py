#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Codex while durably streaming structured events")
    parser.add_argument("--log", required=True)
    parser.add_argument("--errlog", required=True)
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--child-pid-file", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command after --")

    log_path = Path(args.log)
    err_path = Path(args.errlog)
    session_path = Path(args.session_file)
    child_pid_path = Path(args.child_pid_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "bufsize": 1,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(command, **popen_kwargs)
    atomic_write(child_pid_path, f"{proc.pid}\n")

    interrupted = {"signal": None}

    def signal_tree(signum: int) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signum)
            else:
                proc.terminate() if signum == signal.SIGTERM else proc.kill()
        except (ProcessLookupError, PermissionError):
            pass

    def handle_signal(signum, _frame):
        interrupted["signal"] = signum
        signal_tree(signal.SIGTERM)
        # A stuck descendant must not keep stdout/stderr pipes open forever during restart.
        timer = threading.Timer(2.0, lambda: signal_tree(signal.SIGKILL))
        timer.daemon = True
        timer.start()

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM)):
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, OSError):
            pass

    def pump_stdout() -> None:
        assert proc.stdout is not None
        with log_path.open("w", encoding="utf-8", buffering=1) as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started" and event.get("thread_id"):
                    atomic_write(session_path, str(event["thread_id"]) + "\n")
                    os.fsync(log.fileno())

    def pump_stderr() -> None:
        assert proc.stderr is not None
        with err_path.open("w", encoding="utf-8", buffering=1) as err:
            for line in proc.stderr:
                err.write(line)
                err.flush()
                sys.stderr.write(line)
                sys.stderr.flush()

    out_thread = threading.Thread(target=pump_stdout, name="codex-stdout")
    err_thread = threading.Thread(target=pump_stderr, name="codex-stderr")
    out_thread.start()
    err_thread.start()

    rc = proc.wait()
    out_thread.join()
    err_thread.join()

    # Remove only our own child PID reference so the shell never acts on a recycled PID.
    try:
        if child_pid_path.read_text(encoding="utf-8").strip() == str(proc.pid):
            child_pid_path.unlink()
    except FileNotFoundError:
        pass

    if interrupted["signal"] is not None:
        return 128 + int(interrupted["signal"])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
