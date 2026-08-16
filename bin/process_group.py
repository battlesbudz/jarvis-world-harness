#!/usr/bin/env python3
import os
from pathlib import Path


def has_executable_members(pgid: int, proc_root: Path = Path("/proc")) -> bool:
    """Return whether a POSIX process group contains a non-zombie process.

    Linux keeps unreaped zombies visible to killpg(2), but zombies have already
    closed every descriptor and cannot execute or mutate the worktree. Prefer
    /proc state when available; fall back to the conservative kernel probe.
    """
    if os.name == "posix" and proc_root.is_dir():
        try:
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
                    # The last NSpgid value is the process-group id visible in the
                    # innermost PID namespace, matching Popen.pid/killpg arguments.
                    member_pgid = int(values["NSpgid"].split()[-1])
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
