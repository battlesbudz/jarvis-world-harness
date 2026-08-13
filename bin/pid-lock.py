#!/usr/bin/env python3
import argparse
import os
import tempfile
from contextlib import contextmanager
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


@contextmanager
def reclaim_mutex(path: Path):
    """Serialize stale-lock reclamation with an OS-backed lock.

    The mutex file is intentionally persistent; the kernel lock, not file existence,
    provides ownership and is released automatically if the process dies.
    """
    mutex = path.with_name(path.name + ".reclaim-mutex")
    mutex.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(mutex, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt

            # msvcrt locks byte ranges. Ensure byte zero exists, then lock it.
            if os.path.getsize(mutex) == 0:
                os.write(fd, b"0")
                os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def reclaim(path: Path, pid: str, expected_owner: str) -> int:
    """Replace only the stale lock instance the caller actually observed.

    Reclaimers are serialized. After taking the mutex we re-read the owner and
    refuse to unlink if it changed, preventing a stale-recovery contender from
    deleting a lock that another process just acquired.
    """
    with reclaim_mutex(path):
        try:
            current = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return acquire(path, pid)
        if current != expected_owner:
            return 1
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return acquire(path, pid)


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
    parser.add_argument("action", choices=["acquire", "reclaim", "release"])
    parser.add_argument("path")
    parser.add_argument("pid")
    parser.add_argument("expected_owner", nargs="?")
    args = parser.parse_args()
    path = Path(args.path)
    if args.action == "acquire":
        return acquire(path, args.pid)
    if args.action == "reclaim":
        if args.expected_owner is None:
            parser.error("reclaim requires expected_owner")
        return reclaim(path, args.pid, args.expected_owner)
    return release(path, args.pid)


if __name__ == "__main__":
    raise SystemExit(main())
