#!/usr/bin/env python3
import argparse
import os
import tempfile
from pathlib import Path


def acquire(path: Path, pid: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(pid + "\n")
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return 1
        return 0
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def release(path: Path, pid: str) -> int:
    try:
        owner = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return 0
    if owner != pid:
        return 1
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic PID-file lock helper")
    parser.add_argument("action", choices=["acquire", "release"])
    parser.add_argument("path")
    parser.add_argument("pid")
    args = parser.parse_args()
    path = Path(args.path)
    if args.action == "acquire":
        return acquire(path, args.pid)
    return release(path, args.pid)


if __name__ == "__main__":
    raise SystemExit(main())
