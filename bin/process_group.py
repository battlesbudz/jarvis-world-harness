#!/usr/bin/env python3
import ctypes
import os
import signal
import sys
import time
from pathlib import Path


class ProcessTreeError(RuntimeError):
    pass


def _status_values(path: Path) -> dict[str, str]:
    return {
        key: value.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }


def enable_child_subreaper() -> None:
    """Adopt orphaned descendants so setsid/double-fork cannot escape cleanup."""
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise ProcessTreeError("full descendant ownership requires Linux child-subreaper support")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise ProcessTreeError(f"could not enable child subreaper: errno {error}")


def caller_namespace_index(proc_root: Path) -> int:
    """Return the NSpgid index that is visible to this process.

    Namespace IDs in procfs start at the namespace that mounted procfs. The
    caller may itself be nested below that mount, so neither the first nor last
    entry is universally correct. NSpid for /proc/self has one entry per level
    from the mount namespace through the caller's namespace.
    """
    try:
        status = (proc_root / "self" / "status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("NSpid:"):
                values = line.split(":", 1)[1].split()
                return max(0, len(values) - 1)
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        pass
    # A synthetic proc tree, or procfs without namespace metadata, represents
    # the namespace at its own mount point.
    return 0


def executable_descendant_pids(
    proc_root: Path = Path("/proc"),
    ancestor_pid: int | None = None,
) -> set[int]:
    """Return caller-visible PIDs for every non-zombie descendant.

    Parent links and directory names are interpreted in the namespace that
    mounted procfs. Signals use the NSpid value at the caller's namespace depth.
    Once child-subreaper mode is enabled, detached or double-forked processes are
    reparented beneath this process and remain discoverable here.
    """
    try:
        own = _status_values(proc_root / "self" / "status")
        own_namespace_pids = own["NSpid"].split()
        own_mount_pid = int(own_namespace_pids[0])
        namespace_index = len(own_namespace_pids) - 1
    except (FileNotFoundError, ProcessLookupError, PermissionError, KeyError, ValueError, OSError) as e:
        raise ProcessTreeError(f"could not identify the caller in procfs: {e}") from e

    processes: dict[int, tuple[int, int, str]] = {}
    try:
        entries = list(proc_root.iterdir())
    except (FileNotFoundError, PermissionError, OSError) as e:
        raise ProcessTreeError(f"could not scan procfs descendants: {e}") from e
    for entry in entries:
        if not entry.name.isdecimal():
            continue
        try:
            values = _status_values(entry / "status")
            mount_pid = int(values.get("Pid", entry.name))
            mount_ppid = int(values["PPid"])
            namespace_pids = values["NSpid"].split()
            caller_pid = int(namespace_pids[namespace_index])
            state = values["State"].split()[0]
        except (FileNotFoundError, ProcessLookupError, KeyError, ValueError, IndexError):
            continue
        except PermissionError:
            # Descendants run as the same user, so an unreadable process cannot be
            # safely proven unrelated while a serialized lease is at stake.
            raise ProcessTreeError(f"permission denied while inspecting {entry / 'status'}")
        processes[mount_pid] = (mount_ppid, caller_pid, state)

    if ancestor_pid is None or ancestor_pid == os.getpid():
        root_mount_pid = own_mount_pid
    else:
        roots = [
            mount_pid
            for mount_pid, (_mount_ppid, caller_pid, _state) in processes.items()
            if caller_pid == ancestor_pid
        ]
        if len(roots) != 1:
            raise ProcessTreeError(f"could not uniquely identify ancestor PID {ancestor_pid}")
        root_mount_pid = roots[0]

    descendants: set[int] = set()
    frontier = {root_mount_pid}
    while frontier:
        children = {
            mount_pid
            for mount_pid, (mount_ppid, _caller_pid, _state) in processes.items()
            if mount_ppid in frontier and mount_pid not in descendants
        }
        descendants.update(children)
        frontier = children
    return {
        processes[mount_pid][1]
        for mount_pid in descendants
        if processes[mount_pid][2] not in {"Z", "X"}
    }


def terminate_executable_descendants(
    term_grace_seconds: float = 2.0,
    kill_grace_seconds: float = 2.0,
    proc_root: Path = Path("/proc"),
    ancestor_pid: int | None = None,
    exclude_pids: set[int] | None = None,
) -> bool:
    """Terminate all descendants across process groups and sessions.

    Re-scan while signaling so descendants cannot escape by forking during
    cleanup. PID reuse is prevented by subreaper-owned zombies until they are
    explicitly reaped after the tree contains no executable process.
    """
    excluded = set(exclude_pids or ())
    initial = executable_descendant_pids(proc_root, ancestor_pid) - excluded
    if not initial:
        return False

    deadline = time.monotonic() + max(0.0, term_grace_seconds)
    term_signaled: set[int] = set()
    while True:
        pids = executable_descendant_pids(proc_root, ancestor_pid) - excluded
        if not pids:
            return True
        for pid in pids - term_signaled:
            try:
                os.kill(pid, signal.SIGTERM)
                term_signaled.add(pid)
            except ProcessLookupError:
                pass
            except PermissionError as e:
                raise ProcessTreeError(f"permission denied terminating descendant {pid}") from e
        if time.monotonic() >= deadline:
            break
        time.sleep(0.01)

    deadline = time.monotonic() + max(0.0, kill_grace_seconds)
    while True:
        pids = executable_descendant_pids(proc_root, ancestor_pid) - excluded
        if not pids:
            return True
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as e:
                raise ProcessTreeError(f"permission denied killing descendant {pid}") from e
        if time.monotonic() >= deadline:
            raise ProcessTreeError(f"descendants survived SIGKILL: {sorted(pids)}")
        time.sleep(0.01)


def reap_exited_children() -> None:
    """Reap subreaper-adopted zombies after their executable tree is gone."""
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "exec-subreaper":
        enable_child_subreaper()
        os.environ["JWH_SUBREAPER_ACTIVE"] = "1"
        os.execvp(sys.argv[2], sys.argv[2:])
    if len(sys.argv) == 3 and sys.argv[1] == "terminate-descendants":
        try:
            ancestor_pid = int(sys.argv[2])
        except ValueError as e:
            raise ProcessTreeError(f"invalid ancestor PID {sys.argv[2]!r}") from e
        terminate_executable_descendants(
            ancestor_pid=ancestor_pid,
            exclude_pids={os.getpid()},
        )
        return 0
    raise ProcessTreeError("usage: process_group.py exec-subreaper COMMAND... | terminate-descendants PID")


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except ProcessTreeError as error:
        print(f"process tree error: {error}", file=sys.stderr)
        raise SystemExit(2)


def has_executable_members(pgid: int, proc_root: Path = Path("/proc")) -> bool:
    """Return whether a POSIX process group contains a non-zombie process.

    Linux keeps unreaped zombies visible to killpg(2), but zombies have already
    closed every descriptor and cannot execute or mutate the worktree. Prefer
    /proc state when available; fall back to the conservative kernel probe.
    """
    if os.name == "posix" and proc_root.is_dir():
        try:
            namespace_index = caller_namespace_index(proc_root)
            entries = proc_root.iterdir()
            for entry in entries:
                if not entry.name.isdecimal():
                    continue
                try:
                    values = _status_values(entry / "status")
                    state = values["State"].split()[0]
                    # NSpgid is ordered from the PID namespace that mounted this
                    # procfs toward nested namespaces. Select the caller's level,
                    # which may be the mount namespace or a nested container.
                    namespace_pgids = values["NSpgid"].split()
                    member_pgid = int(namespace_pgids[namespace_index])
                except KeyError:
                    try:
                        stat = (entry / "stat").read_text(encoding="utf-8")
                        fields = stat[stat.rfind(")") + 2 :].split()
                        state, member_pgid = fields[0], int(fields[2])
                    except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
                        continue
                    except PermissionError:
                        return True
                except (FileNotFoundError, ProcessLookupError, ValueError, IndexError):
                    continue
                except PermissionError:
                    return True
                if member_pgid == pgid and state not in {"Z", "X"}:
                    return True
            return False
        except (FileNotFoundError, PermissionError, OSError):
            pass

    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
