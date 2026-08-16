#!/usr/bin/env python3
import os
from pathlib import Path


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
                    status = (entry / "status").read_text(encoding="utf-8")
                    values = {
                        key: value.strip()
                        for line in status.splitlines()
                        if ":" in line
                        for key, value in [line.split(":", 1)]
                    }
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
