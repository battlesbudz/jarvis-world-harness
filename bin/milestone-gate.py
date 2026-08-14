#!/usr/bin/env python3
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class GateConfigurationError(RuntimeError):
    pass


def current_milestone() -> str:
    override = os.environ.get("MILESTONE_ID", "").strip()
    if override:
        return override
    try:
        text = (ROOT / "MILESTONE.md").read_text(encoding="utf-8")
    except OSError as e:
        raise GateConfigurationError(f"could not read MILESTONE.md: {e}") from e
    m = re.search(r"Active Milestone\s*[—-]\s*([A-Za-z0-9._-]+)\s*:", text)
    if not m:
        raise GateConfigurationError("could not determine milestone id from MILESTONE.md")
    return m.group(1)


def load_config(milestone: str) -> tuple[Path, list[dict]]:
    config_path = ROOT / "milestones" / milestone / "gate.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise GateConfigurationError(f"could not load {config_path.relative_to(ROOT)}: {e}") from e
    if not isinstance(config, dict):
        raise GateConfigurationError("gate config must be a JSON object")
    if config.get("milestone") != milestone:
        raise GateConfigurationError(
            f"gate config milestone mismatch: {config.get('milestone')!r} != {milestone!r}"
        )
    checks = config.get("checks")
    if not isinstance(checks, list) or not checks:
        raise GateConfigurationError("gate config has no checks")
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise GateConfigurationError(f"check #{index + 1} must be an object")
    return config_path, checks


def signal_process_tree(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    """Signal the check's process group even after its original leader exits."""
    try:
        if os.name == "posix":
            # The process group can outlive its leader. Do not gate this on poll(): a
            # descendant may still own inherited stdout/stderr and edit the worktree.
            os.killpg(proc.pid, sig)
        elif proc.poll() is None:
            proc.send_signal(sig)
    except (ProcessLookupError, PermissionError):
        pass


def terminate_process_tree(proc: subprocess.Popen[str], grace_seconds: float = 2.0) -> tuple[str, str]:
    """Terminate a timed-out check and all descendants before returning.

    The harness is POSIX-oriented (bash + flock), so each check is started in its own
    session/process group. SIGTERM gives cooperative cleanup a chance; SIGKILL prevents
    stubborn descendants from surviving after the supervisor releases the runner lock.
    """
    signal_process_tree(proc, signal.SIGTERM)

    grace_deadline = time.monotonic() + grace_seconds
    stdout = ""
    stderr = ""
    streams_drained = False
    try:
        stdout, stderr = proc.communicate(timeout=grace_seconds)
        streams_drained = True
    except subprocess.TimeoutExpired:
        pass

    # Pipe EOF proves only that no survivor still owns these particular streams.
    # Preserve the full cooperative grace period, then always escalate against the
    # process group so a redirected descendant cannot outlive the gate.
    remaining_grace = grace_deadline - time.monotonic()
    if remaining_grace > 0:
        time.sleep(remaining_grace)
    signal_process_tree(proc, signal.SIGKILL)

    if streams_drained:
        return stdout or "", stderr or ""

    try:
        stdout, stderr = proc.communicate(timeout=grace_seconds)
        return stdout or "", stderr or ""
    except subprocess.TimeoutExpired as e:
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=grace_seconds)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        raise GateConfigurationError(
            "timed-out check did not release its output pipes after process-tree "
            "SIGKILL; descendant state is untrusted"
        ) from e


def run_check(check: dict) -> dict:
    cid = str(check.get("id") or "unnamed")
    command = check.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
        raise GateConfigurationError(f"check {cid!r} has invalid command")
    raw_timeout = check.get("timeout_seconds", 120)
    if isinstance(raw_timeout, bool):
        raise GateConfigurationError(f"check {cid!r} has invalid timeout_seconds")
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError) as e:
        raise GateConfigurationError(f"check {cid!r} has invalid timeout_seconds") from e
    if timeout <= 0:
        raise GateConfigurationError(f"check {cid!r} timeout_seconds must be > 0")

    started = time.time()
    try:
        popen_kwargs = {
            "cwd": ROOT,
            "text": True,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = terminate_process_tree(proc)
            return {
                "id": cid,
                "passed": False,
                "error": f"timeout after {timeout}s; process tree terminated",
                "duration_seconds": round(time.time() - started, 3),
                "stdout": stdout[-12000:],
                "stderr": stderr[-12000:],
            }
    except (FileNotFoundError, PermissionError, OSError) as e:
        raise GateConfigurationError(f"check {cid!r} could not execute {command[0]!r}: {e}") from e

    return {
        "id": cid,
        "passed": proc.returncode == 0,
        "exit_code": proc.returncode,
        "duration_seconds": round(time.time() - started, 3),
        "stdout": (stdout or "")[-12000:],
        "stderr": (stderr or "")[-12000:],
    }


def write_payload(payload: dict, milestone: str, allow_marker: bool) -> None:
    state = ROOT / ".harness"
    state.mkdir(exist_ok=True)
    target = state / "milestone-gate.json"
    tmp = state / f"milestone-gate.json.tmp.{os.getpid()}"
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    marker = state / "MILESTONE-PASSED"
    if allow_marker and payload.get("passed") is True:
        marker.write_text(milestone + "\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the active Jarvis World Harness milestone gate")
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    parser.add_argument("--no-record", action="store_true", help="do not write .harness/milestone-gate.json")
    args = parser.parse_args()

    milestone = os.environ.get("MILESTONE_ID", "").strip() or "unknown"
    try:
        milestone = current_milestone()
        _config_path, checks = load_config(milestone)
        results = [run_check(check) for check in checks]
    except GateConfigurationError as e:
        payload = {
            "milestone": milestone,
            "passed": False,
            "configuration_error": str(e),
            "evaluated_at_epoch": int(time.time()),
            "checks": [],
        }
        if not args.no_record:
            write_payload(payload, milestone, allow_marker=False)
        if args.json:
            print(json.dumps(payload, indent=2))
        print(f"milestone gate configuration error: {e}", file=sys.stderr)
        return 2

    passed = all(r.get("passed") is True for r in results)
    payload = {
        "milestone": milestone,
        "passed": passed,
        "evaluated_at_epoch": int(time.time()),
        "checks": results,
    }

    if not args.no_record:
        write_payload(payload, milestone, allow_marker=True)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for result in results:
            status = "PASS" if result.get("passed") else "FAIL"
            print(f"[{status}] {result['id']}")
            if not result.get("passed"):
                if result.get("stdout"):
                    print(result["stdout"].rstrip())
                if result.get("stderr"):
                    print(result["stderr"].rstrip(), file=sys.stderr)
                if result.get("error"):
                    print(result["error"], file=sys.stderr)
        print(f"Milestone {milestone}: {'PASSED' if passed else 'NOT PASSED'}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
