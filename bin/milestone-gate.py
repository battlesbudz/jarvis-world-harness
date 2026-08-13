#!/usr/bin/env python3
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def current_milestone() -> str:
    override = os.environ.get("MILESTONE_ID", "").strip()
    if override:
        return override
    text = (ROOT / "MILESTONE.md").read_text(encoding="utf-8")
    m = re.search(r"Active Milestone\s*[—-]\s*([A-Za-z0-9._-]+)\s*:", text)
    if not m:
        raise RuntimeError("could not determine milestone id from MILESTONE.md")
    return m.group(1)


def run_check(check: dict) -> dict:
    cid = str(check.get("id") or "unnamed")
    command = check.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        return {"id": cid, "passed": False, "error": "invalid command"}
    timeout = int(check.get("timeout_seconds", 120))
    started = time.time()
    try:
        proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        return {
            "id": cid,
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "duration_seconds": round(time.time() - started, 3),
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
        }
    except subprocess.TimeoutExpired as e:
        return {
            "id": cid,
            "passed": False,
            "error": f"timeout after {timeout}s",
            "duration_seconds": round(time.time() - started, 3),
            "stdout": (e.stdout or "")[-12000:] if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "")[-12000:] if isinstance(e.stderr, str) else "",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the active Jarvis World Harness milestone gate")
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    parser.add_argument("--no-record", action="store_true", help="do not write .harness/milestone-gate.json")
    args = parser.parse_args()

    try:
        milestone = current_milestone()
        config_path = ROOT / "milestones" / milestone / "gate.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("milestone") != milestone:
            raise RuntimeError(f"gate config milestone mismatch: {config.get('milestone')!r} != {milestone!r}")
        checks = config.get("checks")
        if not isinstance(checks, list) or not checks:
            raise RuntimeError("gate config has no checks")
    except Exception as e:
        print(f"milestone gate configuration error: {e}", file=sys.stderr)
        return 2

    results = [run_check(c) for c in checks]
    passed = all(r.get("passed") is True for r in results)
    payload = {
        "milestone": milestone,
        "passed": passed,
        "evaluated_at_epoch": int(time.time()),
        "checks": results,
    }

    if not args.no_record:
        state = ROOT / ".harness"
        state.mkdir(exist_ok=True)
        (state / "milestone-gate.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        marker = state / "MILESTONE-PASSED"
        if passed:
            marker.write_text(milestone + "\n", encoding="utf-8")
        elif marker.exists():
            marker.unlink()

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
