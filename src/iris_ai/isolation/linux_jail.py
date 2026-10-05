"""Landlock and seccomp for a Linux child. Other platforms return ``audit``.

The check process and the long-lived host both call `apply`. It does not
import the rest of Iris, so the host can load this file by path without
putting the kernel on `sys.path`. A non-Linux call returns ``audit`` and
does not pretend a kernel jail was installed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def apply(folder: Path, roots: list[Path]) -> str:
    """Restrict this process. The label is what the caller may claim."""
    label = "audit"
    loaded_ctypes = False
    try:
        if not sys.platform.startswith("linux"):
            raise OSError("landlock is linux-only")
        import ctypes

        loaded_ctypes = True
        from ctypes import Structure, c_int, c_uint64, c_void_p

        libc = ctypes.CDLL(None, use_errno=True)

        def _ok(ret: int, what: str) -> int:
            if ret < 0:
                err = ctypes.get_errno()
                raise OSError(err, f"{what}: {os.strerror(err)}")
            return ret

        fs_read = (1 << 2) | (1 << 3)
        fs_write = (
            (1 << 1)
            | (1 << 4)
            | (1 << 5)
            | (1 << 6)
            | (1 << 7)
            | (1 << 8)
            | (1 << 9)
            | (1 << 10)
            | (1 << 11)
            | (1 << 12)
            | (1 << 13)
            | (1 << 14)
        )
        fs_handled = fs_read | fs_write | (1 << 0)

        class RulesetAttr(Structure):
            _fields_ = [("handled_access_fs", c_uint64), ("handled_access_net", c_uint64)]

        class PathBeneath(Structure):
            _fields_ = [("allowed_access", c_uint64), ("parent_fd", c_int)]

        attr = RulesetAttr(fs_handled, 3)
        rules = _ok(libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0), "landlock")
        seen: set[str] = set()
        for item in [*roots, "/usr", "/lib", "/lib64", "/proc", "/dev", str(folder)]:
            path = Path(item)
            if not path.exists():
                continue
            resolved = path.resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            access = fs_read | fs_write if resolved == folder.resolve() else fs_read
            handle = os.open(key, os.O_PATH | getattr(os, "O_CLOEXEC", 0))
            rule = PathBeneath(access, handle)
            try:
                _ok(libc.syscall(445, rules, 1, ctypes.byref(rule), 0), key)
            except OSError:
                continue
            finally:
                os.close(handle)
        _ok(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
        _ok(libc.syscall(446, rules, 0), "landlock restrict")
        os.close(rules)
        label = "landlock"
        try:
            import platform
            import struct

            blocked = {
                0xC000003E: (59, 322, 41, 42, 43, 44, 45, 46, 47, 49, 50, 53, 288, 299, 307, 101, 310, 311),
                0xC00000B7: (
                    221, 281, 198, 203, 202, 206, 207, 211, 212, 200, 201, 199, 242, 243, 269, 117, 270, 271,
                ),
            }
            machine = struct.calcsize("P")
            arch = 0xC000003E if machine == 8 and sys.byteorder == "little" else 0
            if platform.machine() in {"aarch64", "arm64"}:
                arch = 0xC00000B7
            elif platform.machine() in {"x86_64", "amd64"}:
                arch = 0xC000003E
            numbers = blocked.get(arch)
            if numbers:

                def stmt(code: int, k: int) -> bytes:
                    return struct.pack("HBBI", code, 0, 0, k & 0xFFFFFFFF)

                def jump(code: int, k: int, jt: int, jf: int) -> bytes:
                    return struct.pack("HBBI", code, jt, jf, k & 0xFFFFFFFF)

                allow, errno_ret = 0x7FFF0000, 0x00050000 | 1
                kill = 0x80000000
                instr = [stmt(0x20, 4), jump(0x15, arch, 1, 0), stmt(0x06, kill), stmt(0x20, 0)]
                for number in numbers:
                    instr.append(jump(0x15, number, 0, 1))
                    instr.append(stmt(0x06, errno_ret))
                instr.append(stmt(0x06, allow))
                blob = b"".join(instr)
                buf = ctypes.create_string_buffer(blob)

                class Prog(Structure):
                    _fields_ = [("len", ctypes.c_ushort), ("filt", c_void_p)]

                prog = Prog(len(blob) // 8, ctypes.cast(buf, c_void_p))
                _ok(libc.prctl(22, 2, ctypes.byref(prog)), "seccomp")
                label = "landlock+seccomp"
        except (OSError, struct.error):
            pass
    except (OSError, AttributeError):
        label = "audit"
    finally:
        # Only the child that just loaded ctypes should drop it. Popping it in
        # the parent removes the module other libraries already imported.
        if loaded_ctypes:
            sys.modules.pop("ctypes", None)
            sys.modules.pop("_ctypes", None)
    if os.environ.get("IRIS_CHECK_NETNS") == "1" and label != "audit":
        label += "+netns"
    elif os.environ.get("IRIS_CHECK_NETNS") == "1":
        label = "audit+netns"
    return label
