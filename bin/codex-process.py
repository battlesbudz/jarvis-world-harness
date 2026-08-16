#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from process_group import has_executable_members


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


def remove_own_pid(path: Path, pid: int) -> None:
    """Remove a PID reference only when it still names this process."""
    try:
        if path.read_text(encoding="utf-8").strip() == str(pid):
            path.unlink()
    except FileNotFoundError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Codex while durably streaming structured events")
    parser.add_argument("--log", required=True)
    parser.add_argument("--errlog", required=True)
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--child-pid-file", required=True)
    parser.add_argument("--wrapper-pid-file", required=True)
    parser.add_argument("--wrapper-lock-file", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--stop-file", required=True)
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
    wrapper_pid_path = Path(args.wrapper_pid_file)
    wrapper_lock_path = Path(args.wrapper_lock_file)
    ready_path = Path(args.ready_file)
    stop_path = Path(args.stop_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    wrapper_pid = os.getpid()
    wrapper_lock_path.parent.mkdir(parents=True, exist_ok=True)
    wrapper_lock_fd = os.open(wrapper_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(wrapper_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as e:
        os.close(wrapper_lock_fd)
        raise RuntimeError("another Codex process wrapper already owns its kernel lock") from e
    early_interrupt = {"signal": None}

    def handle_early_signal(signum, _frame):
        # Keep the wrapper alive until Popen either returns a process group that can
        # be cleaned up or fails without creating one.
        early_interrupt["signal"] = signum

    managed_signals = (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", signal.SIGTERM))
    for sig in managed_signals:
        try:
            signal.signal(sig, handle_early_signal)
        except (ValueError, OSError):
            pass
    atomic_write(wrapper_pid_path, f"{wrapper_pid}\n")

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
    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except BaseException:
        remove_own_pid(wrapper_pid_path, wrapper_pid)
        remove_own_pid(ready_path, wrapper_pid)
        raise
    atomic_write(child_pid_path, f"{proc.pid}\n")

    interrupted = {"signal": None}
    escalation_timer = {"timer": None}
    escalation_deadline = {"value": None}

    def signal_tree(signum: int) -> None:
        try:
            if os.name == "posix":
                # The process-group id remains proc.pid even after the original Codex
                # leader exits. Descendants can still own inherited stdout/stderr pipes,
                # so escalation must target the group independently of proc.poll().
                os.killpg(proc.pid, signum)
            elif proc.poll() is None:
                proc.terminate() if signum == signal.SIGTERM else proc.kill()
        except (ProcessLookupError, PermissionError):
            pass

    def handle_signal(signum, _frame):
        interrupted["signal"] = signum
        signal_tree(signal.SIGTERM)
        # The first interrupt establishes one fixed grace deadline. Repeated signals
        # must not postpone escalation and let a stubborn descendant survive forever.
        if escalation_timer["timer"] is None:
            escalation_deadline["value"] = time.monotonic() + 2.0
            timer = threading.Timer(2.0, lambda: signal_tree(signal.SIGKILL))
            timer.daemon = True
            escalation_timer["timer"] = timer
            timer.start()

    for sig in managed_signals:
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, OSError):
            pass
    if early_interrupt["signal"] is not None:
        handle_signal(int(early_interrupt["signal"]), None)

    # Restart requests use a file watched by the actual lock-inheriting wrapper.
    # This avoids sending a signal to a PID that may have been recycled after the
    # runner shell was hard-killed.
    stop_monitor_done = threading.Event()

    def monitor_stop_file() -> None:
        while not stop_monitor_done.wait(0.05):
            if stop_path.exists():
                try:
                    os.kill(wrapper_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                return

    stop_thread = threading.Thread(target=monitor_stop_file, name="codex-stop-monitor", daemon=True)
    stop_thread.start()

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
    if interrupted["signal"] is None:
        # Restart does not report success until this readiness handshake agrees
        # with wrapper/child PID metadata and the authoritative runner lock.
        atomic_write(ready_path, f"{wrapper_pid}\n")

    rc = proc.wait()
    if interrupted["signal"] is None and os.name == "posix" and has_executable_members(proc.pid):
        # A normally exiting leader may leave descendants holding inherited output
        # pipes open. Clean the group before joining the pumps; otherwise those
        # joins can wait forever for EOF while the runner lease remains held.
        signal_tree(signal.SIGTERM)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and has_executable_members(proc.pid):
            time.sleep(0.01)
        if has_executable_members(proc.pid):
            signal_tree(signal.SIGKILL)
    out_thread.join()
    err_thread.join()
    stop_monitor_done.set()
    stop_thread.join()
    timer = escalation_timer["timer"]
    if interrupted["signal"] is not None:
        # Pump completion proves only pipe EOF. A redirected descendant may still be
        # alive, so preserve the full grace period and always signal the process group
        # before the wrapper returns and releases the inherited runner lock.
        deadline = escalation_deadline["value"]
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
        signal_tree(signal.SIGKILL)
    if timer is not None:
        timer.cancel()
        timer.join()

    # Remove only our own PID references so restart never acts on recycled metadata.
    remove_own_pid(child_pid_path, proc.pid)
    remove_own_pid(wrapper_pid_path, wrapper_pid)
    remove_own_pid(ready_path, wrapper_pid)
    try:
        stop_path.unlink()
    except FileNotFoundError:
        pass
    fcntl.flock(wrapper_lock_fd, fcntl.LOCK_UN)
    os.close(wrapper_lock_fd)

    if interrupted["signal"] is not None:
        return 128 + int(interrupted["signal"])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
