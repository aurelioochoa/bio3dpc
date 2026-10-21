"""Host implementation of the BREW AEEHelperFuncs table.

``AEEMod_Load`` expects, immediately before the module image, ``[base-8]`` = the
helper-table version and ``[base-4]`` = a pointer to an ``AEEHelperFuncs``
struct (see AEEStdLib.h).  ``GET_HELPER()`` in the guest reads that struct; the
field order below *is* the binary ABI - never reorder it, only append at the
end if a newer SDK adds fields (and bump HELPER_VERSION).

Each field points at a trap slot: when the guest calls e.g. ``MALLOC``, control
lands in the trap region and the Python handler runs with ARM AAPCS arguments.
The hottest trivial helpers (``native_helpers.ROUTINES``) point at ARM routines
instead, which fall back to their trap slot for large copies and fills.
"""
from __future__ import annotations

import calendar
import math
import random
import struct
import time

from . import native_helpers

# Field order follows AEEHelperFuncs in BREW 3.1.5 SP02 sdk/inc/AEEStdLib.h.
HELPER_FIELDS: tuple[str, ...] = (
    "memmove", "memset",
    "strcpy", "strcat", "strcmp", "strlen", "strchr", "strrchr", "sprintf",
    "wstrcpy", "wstrcat", "wstrcmp", "wstrlen", "wstrchr", "wstrrchr", "wsprintf",
    "strtowstr", "wstrtostr", "wstrtofloat", "floattowstr", "utf8towstr",
    "wstrtoutf8", "wstrlower", "wstrupper", "chartype",
    "SetupNativeImage",
    "malloc", "free", "wstrdup", "realloc",
    "wwritelongex", "wstrsize", "wstrncopyn",
    "OEMStrLen", "OEMStrSize",
    "GetAEEVersion",
    "atoi",
    "f_op", "f_cmp",
    "dbgprintf", "wstrcompress",
    "LocalTimeOffset", "GetRand", "GetTimeMS", "GetUpTimeMS", "GetSeconds",
    "GetJulianDate",
    "sysfree",
    "GetAppInstance",
    "strtoul", "strncpy", "strncmp", "stricmp", "strnicmp", "strstr", "memcmp",
    "memchr", "strexpand", "stristr", "memstr", "wstrncmp", "strdup", "strbegins",
    "strends", "strchrend", "strchrsend", "memrchr", "memchrend", "memrchrbegin",
    "strlower", "strupper", "wstricmp", "wstrnicmp",
    "inet_aton", "inet_ntoa", "swapl", "swaps",
    "GetFSFree", "GetRAMFree",
    "vsprintf", "vsnprintf", "snprintf",
    "JulianToSeconds", "strlcpy", "strlcat", "wstrlcpy", "wstrlcat",
    "setstaticptr",
    "f_assignstr", "f_assignint", "wwritelong", "dbgheapmark",
    "lockmem", "unlockmem", "dumpheap",
    "strtod", "f_calc", "sleep", "getlasterror", "wgs84_to_degrees",
    "dbgevent", "AEEOS_IsBadPtr", "aee_basename", "aee_makepath", "aee_splitpath",
    "aee_stribegins",
    "GetUTCSeconds", "f_toint", "f_get",
    "qsort", "trunc", "utrunc", "err_realloc", "err_strdup",
    "inet_pton", "inet_ntop", "GetALSContext",
)

# GET_HELPER_VER() value; the guest compares this against AEESTDLIB_VER etc.
HELPER_VERSION = 0x00010000

ALLOC_NO_ZMEM = 0x80000000

# f_op / f_cmp operation codes (AEEStdLib.h)
(FO_ADD, FO_SUB, FO_MUL, FO_DIV, FO_CMP_L, FO_CMP_LE, FO_CMP_E, FO_CMP_G,
 FO_CMP_GE, FO_POW) = range(10)
FCALC_FLOOR, FCALC_CEIL, FCALC_SQRT = 10, 11, 12
FGET_HUGEVAL, FGET_FLTMAX, FGET_FLTMIN = 13, 14, 15
FCALC_SIN, FCALC_COS, FCALC_ABS, FCALC_TAN = 16, 17, 18, 19


# Argument arity and return kind per helper (name -> (nargs, result, variadic)).
# Unlisted helpers default to (4, "u32", False), which only reads registers.
HELPER_SPECS: dict[str, tuple[int, str, bool]] = {
    "memmove": (3, "u32", False), "memset": (3, "u32", False),
    "memcmp": (3, "u32", False), "memchr": (3, "u32", False),
    "memrchr": (3, "u32", False), "memchrend": (3, "u32", False),
    "memrchrbegin": (3, "u32", False), "memstr": (3, "u32", False),
    "strlen": (1, "u32", False), "strcpy": (2, "u32", False),
    "strncpy": (3, "u32", False), "strcat": (2, "u32", False),
    "strcmp": (2, "u32", False), "strncmp": (3, "u32", False),
    "stricmp": (2, "u32", False), "strnicmp": (3, "u32", False),
    "strchr": (2, "u32", False), "strrchr": (2, "u32", False),
    "strstr": (2, "u32", False), "stristr": (2, "u32", False),
    "strbegins": (2, "u32", False), "aee_stribegins": (2, "u32", False),
    "strends": (2, "u32", False), "strchrend": (2, "u32", False),
    "strchrsend": (2, "u32", False), "strlower": (1, "u32", False),
    "strupper": (1, "u32", False), "strdup": (1, "u32", False),
    "strlcpy": (3, "u32", False), "strlcat": (3, "u32", False),
    "atoi": (1, "u32", False), "strtoul": (3, "u32", False),
    "strtod": (2, "f64", False),
    "wstrlen": (1, "u32", False), "wstrcpy": (2, "u32", False),
    "wstrcat": (2, "u32", False), "wstrcmp": (2, "u32", False),
    "wstricmp": (2, "u32", False), "wstrncmp": (3, "u32", False),
    "wstrnicmp": (3, "u32", False), "wstrchr": (2, "u32", False),
    "wstrrchr": (2, "u32", False), "strtowstr": (3, "u32", False),
    "wstrtostr": (3, "u32", False), "wstrtofloat": (1, "f64", False),
    "floattowstr": (3, "u32", False), "utf8towstr": (4, "u32", False),
    "wstrtoutf8": (4, "u32", False), "wstrlower": (1, "void", False),
    "wstrupper": (1, "void", False), "wstrdup": (1, "u32", False),
    "wstrlcpy": (3, "u32", False), "wstrlcat": (3, "u32", False),
    "wstrsize": (1, "u32", False), "wstrncopyn": (4, "u32", False),
    "wwritelong": (2, "u32", False), "wwritelongex": (4, "u32", False),
    "wstrcompress": (4, "void", False), "strexpand": (4, "void", False),
    "OEMStrLen": (1, "u32", False), "OEMStrSize": (1, "u32", False),
    "sprintf": (4, "u32", True), "snprintf": (4, "u32", True),
    "dbgprintf": (4, "void", True), "wsprintf": (4, "u32", True),
    "vsprintf": (3, "u32", False),
    "dbgevent": (3, "void", False),
    "malloc": (1, "u32", False), "realloc": (2, "u32", False),
    "free": (1, "void", False), "sysfree": (1, "void", False),
    "dbgheapmark": (3, "u32", False), "dumpheap": (0, "void", False),
    "lockmem": (1, "u32", False), "unlockmem": (1, "u32", False),
    "setstaticptr": (2, "u32", False),
    "GetTimeMS": (0, "u32", False), "GetUpTimeMS": (0, "u32", False),
    "GetSeconds": (0, "u32", False), "GetUTCSeconds": (0, "u32", False),
    "LocalTimeOffset": (1, "u32", False),
    "GetJulianDate": (2, "void", False), "JulianToSeconds": (1, "u32", False),
    "GetRand": (2, "void", False), "sleep": (1, "void", False),
    "getlasterror": (1, "u32", False), "wgs84_to_degrees": (1, "f64", False),
    "f_op": (5, "f64", False), "f_cmp": (5, "u32", False),
    "f_calc": (3, "f64", False), "f_toint": (2, "u32", False),
    "f_assignstr": (1, "f64", False), "f_assignint": (1, "f64", False),
    "f_get": (1, "f64", False), "trunc": (2, "u32", False),
    "utrunc": (2, "u32", False),
    "GetAEEVersion": (3, "u32", False), "SetupNativeImage": (4, "u32", False),
    "chartype": (1, "u32", False), "GetAppInstance": (0, "u32", False),
    "GetFSFree": (1, "u32", False), "GetRAMFree": (2, "u32", False),
    "AEEOS_IsBadPtr": (3, "u32", False), "aee_basename": (1, "u32", False),
    "aee_makepath": (4, "u32", False), "aee_splitpath": (2, "u32", False),
    "swapl": (1, "u32", False), "swaps": (1, "u32", False),
    "inet_aton": (2, "u32", False), "inet_ntoa": (3, "void", False),
    "err_realloc": (2, "u32", False), "err_strdup": (2, "u32", False),
    "qsort": (4, "void", False),
}


def _double(lo: int, hi: int) -> float:
    return struct.unpack("<d", struct.pack("<II", lo & 0xFFFFFFFF, hi & 0xFFFFFFFF))[0]


class HelperTable:
    """Builds the guest AEEHelperFuncs table and implements its functions."""

    def __init__(self, machine, log, trace=None):
        self.m = machine
        self.log = log
        self.trace = trace
        self.unsupported_seen: dict[str, int] = {}
        self.start_time = time.monotonic()
        self.static_ptrs: dict[int, int] = {}
        self.allocs: dict[int, bool] = {}   # addr -> zero_filled flag
        self._applet = 0
        self._applet_word = 0

    @property
    def applet(self) -> int:
        return self._applet

    @applet.setter
    def applet(self, value: int) -> None:
        self._applet = value
        if self._applet_word:
            self.m.w32(self._applet_word, value)

    # ------------------------------------------------------------------ table
    def build(self, native: bool = True) -> int:
        """Fill the guest AEEHelperFuncs table and return its guest address.

        ``native=False`` keeps every entry on its Python handler (call counts
        and traces then cover all helpers).
        """
        table = self.m.alloc(4 * len(HELPER_FIELDS))
        slots = {}
        for name in HELPER_FIELDS:
            handler = getattr(self, f"h_{name}", None)
            if handler is None:
                handler = self._unsupported(name)
            nargs, result, variadic = HELPER_SPECS.get(name, (4, "u32", False))
            slots[name] = self.m.bind(f"helper::{name}", handler, nargs, result, variadic)
        if native:
            entries = native_helpers.install(self.m, slots)
            slots.update(entries)
            self._applet_word = native_helpers.applet_word(entries)
            self.applet = self._applet
        for index, name in enumerate(HELPER_FIELDS):
            self.m.w32(table + 4 * index, slots[name])
        return table

    def _unsupported(self, name):
        def handler(machine, *args):
            self.unsupported_seen[name] = self.unsupported_seen.get(name, 0) + 1
            if self.unsupported_seen[name] <= 3:
                self.log.warn(f"helper {name}(...) not implemented",
                              n=len(args), a0=hex(args[0]) if args else "-")
            return 0
        return handler

    def _log(self, name, args, ret=None):
        if self.trace:
            self.trace("helper", name, args=args, ret=ret)

    # ----------------------------------------------------------------- memory
    def h_memmove(self, m, pd, ps, size):
        if size and not m.move(pd, ps, size):
            m.write(pd, m.read(ps, size))
        self._log("memmove", (pd, ps, size), pd)
        return pd

    def h_memset(self, m, pd, c, size):
        if size and not m.fill(pd, c & 0xFF, size):
            m.write(pd, bytes([c & 0xFF]) * size)
        self._log("memset", (pd, c, size), pd)
        return pd

    def h_memcmp(self, m, a, b, size):
        if size == 0:
            return 0
        x, y = m.read(a, size), m.read(b, size)
        return (x > y) - (x < y)

    def h_memchr(self, m, src, c, size):
        needle = bytes([c & 0xFF])
        idx = m.read(src, size).find(needle)
        return src + idx if idx >= 0 else 0

    def h_memrchr(self, m, src, c, size):
        needle = bytes([c & 0xFF])
        idx = m.read(src, size).rfind(needle)
        return src + idx if idx >= 0 else 0

    def h_memchrend(self, m, src, c, size):
        needle = bytes([c & 0xFF])
        data = m.read(src, size)
        idx = data.rfind(needle)
        return src + idx + 1 if idx >= 0 else 0

    def h_memrchrbegin(self, m, src, c, size):
        needle = bytes([c & 0xFF])
        data = m.read(src, size)
        idx = data.rfind(needle)
        return src + (idx + 1 if idx >= 0 else 0)

    def h_memstr(self, m, hay, needle, n):
        data = m.read(hay, n)
        idx = data.find(m.cstring_bytes(needle))
        return hay + idx if idx >= 0 else 0

    # ----------------------------------------------------------------- string
    def h_strlen(self, m, s):
        if not s:
            return 0
        return len(m.cstring_bytes(s))

    def h_strcpy(self, m, dst, src):
        m.write_string(dst, m.cstring_bytes(src))
        return dst

    def h_strncpy(self, m, dst, src, n):
        data = m.cstring_bytes(src)
        chunk = data[:n]
        if len(chunk) < n:
            chunk += b"\0" * (n - len(chunk))
        m.write(dst, chunk)
        return dst

    def h_strcat(self, m, dst, src):
        m.write_string(dst + self.h_strlen(m, dst), m.cstring_bytes(src))
        return dst

    def h_strcmp(self, m, a, b):
        x, y = m.cstring_bytes(a), m.cstring_bytes(b)
        return 1 if x > y else (-1 if x < y else 0)

    def h_strncmp(self, m, a, b, n):
        x, y = m.cstring_bytes(a)[:n], m.cstring_bytes(b)[:n]
        return 1 if x > y else (-1 if x < y else 0)

    def h_stricmp(self, m, a, b):
        x, y = m.cstring_bytes(a).lower(), m.cstring_bytes(b).lower()
        return 1 if x > y else (-1 if x < y else 0)

    def h_strnicmp(self, m, a, b, n):
        x, y = m.cstring_bytes(a)[:n].lower(), m.cstring_bytes(b)[:n].lower()
        return 1 if x > y else (-1 if x < y else 0)

    def h_strchr(self, m, s, c):
        idx = m.cstring_bytes(s).find(bytes([c & 0xFF]))
        return s + idx if idx >= 0 else 0

    def h_strrchr(self, m, s, c):
        idx = m.cstring_bytes(s).rfind(bytes([c & 0xFF]))
        return s + idx if idx >= 0 else 0

    def h_strstr(self, m, hay, needle):
        idx = m.cstring_bytes(hay).find(m.cstring_bytes(needle))
        return hay + idx if idx >= 0 else 0

    def h_stristr(self, m, hay, needle):
        idx = m.cstring_bytes(hay).lower().find(m.cstring_bytes(needle).lower())
        return hay + idx if idx >= 0 else 0

    def h_strbegins(self, m, prefix, s):
        return 1 if m.cstring_bytes(s).startswith(m.cstring_bytes(prefix)) else 0

    def h_aee_stribegins(self, m, prefix, s):
        return 1 if m.cstring_bytes(s).lower().startswith(
            m.cstring_bytes(prefix).lower()) else 0

    def h_strends(self, m, suffix, s):
        return 1 if m.cstring_bytes(s).endswith(m.cstring_bytes(suffix)) else 0

    def h_strchrend(self, m, s, c):
        idx = m.cstring_bytes(s).find(bytes([c & 0xFF]))
        return s + idx + 1 if idx >= 0 else 0

    def h_strchrsend(self, m, s, chars):
        data = m.cstring_bytes(s).decode("latin-1")
        alt = m.cstring_bytes(chars).decode("latin-1")
        idx = min((data.find(ch) for ch in alt if data.find(ch) >= 0), default=-1)
        return s + idx + 1 if idx >= 0 else 0

    def h_strlower(self, m, s):
        m.write_string(s, m.cstring_bytes(s).lower())
        return s

    def h_strupper(self, m, s):
        m.write_string(s, m.cstring_bytes(s).upper())
        return s

    def h_strdup(self, m, s):
        pointer = m.alloc(len(m.cstring_bytes(s)) + 1)
        m.write_string(pointer, m.cstring_bytes(s))
        self.allocs[pointer] = True
        return pointer

    def h_strlcpy(self, m, dst, src, n):
        data = m.cstring_bytes(src)
        if n:
            m.write(dst, data[: n - 1] + b"\0")
        return len(data)

    def h_strlcat(self, m, dst, src, n):
        cur = m.cstring_bytes(dst)
        data = m.cstring_bytes(src)
        room = max(0, n - len(cur) - 1)
        m.write_string(dst + len(cur), data[:room])
        return len(cur) + len(data)

    def h_atoi(self, m, s):
        try:
            return int(m.string(s).strip().split()[0], 10) & 0xFFFFFFFF
        except (ValueError, IndexError):
            return 0

    def h_strtoul(self, m, s, endptr, base):
        text = m.string(s)
        value = 0
        index = 0
        try:
            value = int(text.strip().split()[0], base or 10)
            index = len(text) - len(text.lstrip())
        except (ValueError, IndexError):
            value = 0
        if endptr:
            m.w32(endptr, s + index)
        return value & 0xFFFFFFFF

    def h_strtod(self, m, s, endptr):
        text = m.string(s)
        stripped = text.lstrip()
        try:
            value = float(stripped.split()[0].rstrip("fF"))
            index = len(text) - len(stripped)
        except (ValueError, IndexError):
            value, index = 0.0, 0
        if endptr:
            m.w32(endptr, s + index)
        return value

    # ------------------------------------------------------------- wide char
    @staticmethod
    def _wstr(m, pointer, limit=65536):
        if not pointer:
            return ""
        raw = m.read(pointer, min(limit, m.readable_limit(pointer)))
        units = struct.unpack(f"<{len(raw) // 2}H", raw[: len(raw) // 2 * 2])
        out = []
        for unit in units:
            if unit == 0:
                break
            out.append(unit)
        return "".join(chr(u) for u in out)

    @staticmethod
    def _write_wstr(m, pointer, text):
        data = b"".join(struct.pack("<H", ord(ch) & 0xFFFF) for ch in text) + b"\0\0"
        m.write(pointer, data)
        return pointer

    def h_wstrlen(self, m, p):
        return len(self._wstr(m, p))

    def h_wstrcpy(self, m, dst, src):
        return self._write_wstr(m, dst, self._wstr(m, src))

    def h_wstrcat(self, m, dst, src):
        return self._write_wstr(m, dst, self._wstr(m, dst) + self._wstr(m, src))

    def h_wstrcmp(self, m, a, b):
        x, y = self._wstr(m, a), self._wstr(m, b)
        return 1 if x > y else (-1 if x < y else 0)

    def h_wstricmp(self, m, a, b):
        x, y = self._wstr(m, a).lower(), self._wstr(m, b).lower()
        return 1 if x > y else (-1 if x < y else 0)

    def h_wstrncmp(self, m, a, b, n):
        x, y = self._wstr(m, a)[:n], self._wstr(m, b)[:n]
        return 1 if x > y else (-1 if x < y else 0)

    def h_wstrnicmp(self, m, a, b, n):
        x, y = self._wstr(m, a)[:n].lower(), self._wstr(m, b)[:n].lower()
        return 1 if x > y else (-1 if x < y else 0)

    def h_wstrchr(self, m, s, ch):
        pointer = s
        while True:
            unit = m.u16(pointer)
            if unit == 0:
                return 0
            if unit == (ch & 0xFFFF):
                return pointer
            pointer += 2

    def h_wstrrchr(self, m, s, ch):
        found = 0
        pointer = s
        while True:
            unit = m.u16(pointer)
            if unit == 0:
                return found
            if unit == (ch & 0xFFFF):
                found = pointer
            pointer += 2

    def h_strtowstr(self, m, src, dest, n):
        self._write_wstr(m, dest, m.string(src)[: max(0, n - 1)])
        return dest

    def h_wstrtostr(self, m, src, dest, n):
        m.write_string(dest, self._wstr(m, src)[: max(0, n - 1)])
        return dest

    def h_wstrtofloat(self, m, p):
        try:
            return float(self._wstr(m, p).strip())
        except ValueError:
            return 0.0

    def h_floattowstr(self, m, value, psz, n):
        text = f"{value:g}"
        self._write_wstr(m, psz, text[: max(0, n - 1)])
        return 1

    def h_utf8towstr(self, m, src, nlen, dest, nsize):
        text = m.read(src, nlen).decode("utf-8", "replace")
        self._write_wstr(m, dest, text[: max(0, nsize // 2 - 1)])
        return 1

    def h_wstrtoutf8(self, m, src, nlen, dest, nsize):
        text = self._wstr(m, src, nlen * 2)
        data = text.encode("utf-8")[: max(0, nsize - 1)]
        m.write(dest, data + b"\0")
        return 1

    def h_wstrlower(self, m, s):
        return self._write_wstr(m, s, self._wstr(m, s).lower())

    def h_wstrupper(self, m, s):
        return self._write_wstr(m, s, self._wstr(m, s).upper())

    def h_wstrdup(self, m, s):
        text = self._wstr(m, s)
        pointer = m.alloc(2 * (len(text) + 1))
        self._write_wstr(m, pointer, text)
        self.allocs[pointer] = True
        return pointer

    def h_wstrlcpy(self, m, dst, src, n):
        text = self._wstr(m, src)
        self._write_wstr(m, dst, text[: max(0, n - 1)])
        return len(text)

    def h_wstrlcat(self, m, dst, src, n):
        cur, add = self._wstr(m, dst), self._wstr(m, src)
        room = max(0, n - len(cur) - 1)
        self._write_wstr(m, dst, cur + add[:room])
        return len(cur) + len(add)

    def h_wstrsize(self, m, p):
        return len(self._wstr(m, p)) + 1

    def h_wstrncopyn(self, m, dest, cb, src, nlen):
        text = self._wstr(m, src, max(0, nlen) * 2 if nlen >= 0 else 65536)
        self._write_wstr(m, dest, text[: max(0, cb // 2 - 1)])
        return dest

    def h_wwritelong(self, m, buf, value):
        return self._write_wstr(m, buf, str(value))

    def h_wwritelongex(self, m, buf, value, pad, premaining):
        text = f"{value:d}"
        if pad:
            text = text.rjust(pad)
        self._write_wstr(m, buf, text)
        if premaining:
            m.w32(premaining, max(0, m.u32(premaining) - len(text)))
        return buf

    def h_wstrcompress(self, m, src, nchars, dest, nsize):
        text = self._wstr(m, src, nchars * 2)[:nchars]
        data = text.encode("ascii", "ignore")[:nsize]
        m.write(dest, data)
        return 0

    def h_strexpand(self, m, src, ncount, dest, nsize):
        data = m.read(src, ncount)
        text = data.decode("latin-1")
        self._write_wstr(m, dest, text[: max(0, nsize // 2 - 1)])
        return 0

    # -------------------------------------------------------------- OEM/bytes
    def h_OEMStrLen(self, m, p):
        length = 0
        while m.read(p + length, 1) != b"\0":
            length += 1
        return length

    def h_OEMStrSize(self, m, p):
        return self.h_OEMStrLen(m, p) + 1

    # ---------------------------------------------------------------- printf
    def _format(self, m, fmt: str, words: list[int], arg_start: int):
        out: list[str] = []
        ai = arg_start
        i = 0
        n = len(fmt)

        def take(count=1):
            nonlocal ai
            chunk = words[ai:ai + count]
            ai += count
            if len(chunk) < count:
                chunk += [0] * (count - len(chunk))
            return chunk

        while i < n:
            ch = fmt[i]
            if ch != "%":
                out.append(ch)
                i += 1
                continue
            i += 1
            if i >= n:
                break
            flags = ""
            while i < n and fmt[i] in "-+0 #":
                flags += fmt[i]
                i += 1
            width = ""
            while i < n and fmt[i].isdigit():
                width += fmt[i]
                i += 1
            precision = ""
            if i < n and fmt[i] == ".":
                precision = "."
                i += 1
                while i < n and fmt[i].isdigit():
                    precision += fmt[i]
                    i += 1
            length = ""
            while i < n and fmt[i] in "lhLqjzt":
                length += fmt[i]
                i += 1
            if i >= n:
                break
            conv = fmt[i]
            i += 1
            if conv == "%":
                out.append("%")
                continue
            spec = "%" + flags + width + precision + (length if length else "")
            spec_simple = "%" + flags + width + precision
            if conv in "di":
                value = take()[0]
                if value >= 0x80000000:
                    value -= 0x100000000
                out.append((spec_simple + "d") % value)
            elif conv == "u":
                out.append((spec_simple + "d") % take()[0])
            elif conv in "xX":
                out.append((spec_simple + conv) % take()[0])
            elif conv == "o":
                out.append((spec_simple + "o") % take()[0])
            elif conv == "c":
                out.append(chr(take()[0] & 0xFF))
            elif conv == "p":
                out.append("0x%08x" % take()[0])
            elif conv == "s":
                pointer = take()[0]
                text = ""
                if pointer and m.contains(pointer):
                    text = m.string(pointer)
                else:
                    text = "(null)"
                out.append((spec_simple + "s") % text)
            elif conv in "fgGeE":
                # Soft-float varargs: a double arrives as two words (roll with
                # it; layout follows AAPCS 8-byte alignment in registers).
                lo, hi = take(2)
                value = _double(lo, hi)
                out.append((spec_simple + conv) % value)
            else:
                out.append(f"%{conv}")
        return "".join(out)

    def h_sprintf(self, m, dest, fmt, *_rest):
        words = list(_rest)
        text = self._format(m, m.string(fmt), words, 0)
        m.write_string(dest, text)
        return len(text)

    def h_snprintf(self, m, dest, size, fmt, *_rest):
        words = list(_rest)
        text = self._format(m, m.string(fmt), words, 0)[: max(0, size - 1)]
        m.write_string(dest, text)
        return len(text)

    def h_vsprintf(self, m, dest, fmt, va):
        # AEEOldVaList is opaque; rendering only the format keeps callers safe.
        text = self._format(m, m.string(fmt), [], 0)
        m.write_string(dest, text)
        return len(text)

    def h_dbgprintf(self, m, fmt, *_rest):
        words = list(_rest)
        text = self._format(m, m.string(fmt), words, 0)
        self.log.info(f"[guest] {text}")
        return None

    def h_wsprintf(self, m, dest, size, fmt, *_rest):
        words = list(_rest)
        text = self._format(m, self._wstr(m, fmt), words, 0)
        self._write_wstr(m, dest, text[: max(0, size // 2 - 1)])
        return 0

    def h_dbgevent(self, m, evt, cls, payload):
        self.log.debug(f"[guest dbgevent] evt={evt} cls=0x{cls:08x} p={payload}")
        return None

    # ----------------------------------------------------------------- memory2
    def h_malloc(self, m, size):
        zero = not (size & ALLOC_NO_ZMEM)
        size &= ~ALLOC_NO_ZMEM
        size &= 0x7FFFFFFF
        if size == 0:
            return 0
        pointer = m.alloc(size)
        self.allocs[pointer] = zero
        self._log("malloc", (size,), pointer)
        return pointer

    def h_realloc(self, m, pointer, size):
        size &= 0x7FFFFFFF
        if pointer == 0:
            return self.h_malloc(m, size)
        old = m.allocations.get(pointer, 0)
        new = m.alloc(size)
        if new:
            m.write(new, m.read(pointer, min(old, size)))
            m.free(pointer)
            self.allocs.pop(pointer, None)
            self.allocs[new] = True
        return new

    def h_free(self, m, pointer):
        if pointer:
            try:
                m.free(pointer)
                self.allocs.pop(pointer, None)
            except Exception as error:  # noqa: BLE001
                self.log.warn(f"free of unknown pointer 0x{pointer:x}: {error}")
        return None

    def h_sysfree(self, m, pointer):
        return self.h_free(m, pointer)

    def h_dbgheapmark(self, m, pointer, psz_file, line):
        return pointer

    def h_dumpheap(self, m):
        return None

    def h_lockmem(self, m, pp):
        return 1

    def h_unlockmem(self, m, pp):
        return 0

    def h_setstaticptr(self, m, spid, value):
        previous = self.static_ptrs.get(spid, 0)
        self.static_ptrs[spid] = value
        return previous

    # ------------------------------------------------------------------- time
    def h_GetTimeMS(self, m):
        return int((time.monotonic() - self.start_time) * 1000) & 0xFFFFFFFF

    def h_GetUpTimeMS(self, m):
        return self.h_GetTimeMS(m)

    def h_GetSeconds(self, m):
        return int(time.time()) & 0xFFFFFFFF

    def h_GetUTCSeconds(self, m):
        return int(time.time()) & 0xFFFFFFFF

    def h_LocalTimeOffset(self, m, pdst):
        if pdst:
            m.w32(pdst, 0)
        return 0

    def h_GetJulianDate(self, m, seconds, pdate):
        if pdate:
            t = time.gmtime(seconds & 0xFFFFFFFF)
            # JulianType is seven uint16s; wWeekDay is 0=Monday..6=Sunday.
            m.write(pdate, struct.pack(
                "<7H", t.tm_year, t.tm_mon, t.tm_mday,
                t.tm_hour, t.tm_min, t.tm_sec, t.tm_wday))
        return None

    def h_JulianToSeconds(self, m, pdate):
        if not pdate:
            return 0
        year, month, day, hour, minute, second, _weekday = struct.unpack(
            "<7H", m.read(pdate, 14))
        try:
            return int(calendar.timegm(
                (year, month, day, hour, minute, second, 0, 0, 0))) & 0xFFFFFFFF
        except (ValueError, OverflowError):
            return 0

    def h_GetRand(self, m, dest, size):
        if dest and size:
            m.write(dest, random.randbytes(size))
        return None

    def h_sleep(self, m, msecs):
        time.sleep(min(msecs, 1000) / 1000.0)
        return None

    def h_getlasterror(self, m, region):
        return 0

    def h_wgs84_to_degrees(self, m, latlon):
        return float(latlon) / 3600000.0

    # --------------------------------------------------------------- floating
    def h_f_op(self, m, v1lo, v1hi, v2lo, v2hi, op):
        a, b = _double(v1lo, v1hi), _double(v2lo, v2hi)
        if op == FO_ADD:
            return a + b
        if op == FO_SUB:
            return a - b
        if op == FO_MUL:
            return a * b
        if op == FO_DIV:
            return a / b if b else math.inf
        if op == FO_POW:
            try:
                return a ** b
            except (OverflowError, ValueError):
                return math.inf
        return 0.0

    def h_f_cmp(self, m, v1lo, v1hi, v2lo, v2hi, op):
        a, b = _double(v1lo, v1hi), _double(v2lo, v2hi)
        return 1 if {
            FO_CMP_L: a < b, FO_CMP_LE: a <= b, FO_CMP_E: a == b,
            FO_CMP_G: a > b, FO_CMP_GE: a >= b,
        }.get(op, False) else 0

    def h_f_calc(self, m, xlo, xhi, op):
        x = _double(xlo, xhi)
        if op == FCALC_FLOOR:
            return math.floor(x)
        if op == FCALC_CEIL:
            return math.ceil(x)
        if op == FCALC_SQRT:
            return math.sqrt(x) if x >= 0 else math.nan
        if op == FCALC_SIN:
            return math.sin(x)
        if op == FCALC_COS:
            return math.cos(x)
        if op == FCALC_TAN:
            return math.tan(x)
        if op == FCALC_ABS:
            return abs(x)
        return x

    def h_f_toint(self, m, lo, hi):
        return int(_double(lo, hi)) & 0xFFFFFFFF

    def h_f_assignstr(self, m, psz):
        try:
            return float(m.string(psz).strip())
        except ValueError:
            return 0.0

    def h_f_assignint(self, m, value):
        if value >= 0x80000000:
            value -= 0x100000000
        return float(value)

    def h_f_get(self, m, kind):
        if kind == FGET_HUGEVAL:
            return math.inf
        if kind == FGET_FLTMAX:
            return 3.4028234663852886e38
        if kind == FGET_FLTMIN:
            return 1.1754943508222875e-38
        return 0.0

    def h_trunc(self, m, lo, hi):
        return int(_double(lo, hi)) & 0xFFFFFFFF

    def h_utrunc(self, m, lo, hi):
        return int(_double(lo, hi)) & 0xFFFFFFFF

    # ------------------------------------------------------------------- misc
    def h_GetAEEVersion(self, m, psz, size, flags):
        if psz and size > 0 and (flags & 0x0001):
            m.write_string(psz, "3.1.5")
        return 0x031005

    def h_SetupNativeImage(self, m, cls, buffer, image_info, prealloc):
        self.log.warn("SetupNativeImage called", cls=hex(cls))
        return 0

    def h_chartype(self, m, ch):
        return 1

    def h_GetAppInstance(self, m):
        return self.applet

    def h_GetFSFree(self, m, ptotal):
        if ptotal:
            m.w32(ptotal, 0x04000000)
        return 0x04000000

    def h_GetRAMFree(self, m, ptotal, plargest):
        if ptotal:
            m.w32(ptotal, 0x2000000)
        if plargest:
            m.w32(plargest, 0x1000000)
        return 0x2000000

    def h_AEEOS_IsBadPtr(self, m, chktype, buf, length):
        if buf == 0:
            return 1
        return 0 if m.contains(buf, max(1, length)) else 1

    def h_aee_basename(self, m, path):
        data = m.cstring_bytes(path)
        index = max(data.rfind(b"/"), data.rfind(b"\\"))
        return path + (index + 1 if index >= 0 else 0)

    def h_aee_makepath(self, m, directory, filename, out, pn_out_len):
        dir_part = m.string(directory)
        file_part = m.string(filename)
        joined = dir_part.rstrip("/\\") + "/" + file_part
        if out:
            m.write_string(out, joined)
        if pn_out_len:
            m.w32(pn_out_len, len(joined))
        return 0

    def h_aee_splitpath(self, m, path, directory):
        data = m.string(path)
        index = max(data.rfind("/"), data.rfind("\\"))
        if directory:
            m.write_string(directory, data[: index + 1] if index >= 0 else "")
        return 0

    def h_swapl(self, m, value):
        return int.from_bytes(value.to_bytes(4, "little"), "big")

    def h_swaps(self, m, value):
        return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

    def h_inet_aton(self, m, pc, addr_out):
        parts = m.string(pc).split(".")
        if len(parts) != 4:
            return 0
        try:
            value = sum(int(p) << (8 * i) for i, p in enumerate(reversed(parts)))
        except ValueError:
            return 0
        if addr_out:
            m.w32(addr_out, value)
        return 1

    def h_inet_ntoa(self, m, addr, pc, length):
        text = ".".join(str((addr >> (8 * i)) & 0xFF) for i in range(3, -1, -1))
        if pc and length:
            m.write_string(pc, text[: length - 1])
        return None

    def h_err_realloc(self, m, size, pp):
        pointer = m.alloc(size)
        if pointer:
            m.w32(pp, pointer)
            self.allocs[pointer] = True
            return 0
        m.w32(pp, 0)
        return 2  # ENOMEMORY

    def h_err_strdup(self, m, src, pp):
        text = m.cstring_bytes(src)
        pointer = m.alloc(len(text) + 1)
        m.write_string(pointer, text)
        m.w32(pp, pointer)
        self.allocs[pointer] = True
        return 0

    def h_qsort(self, m, base, nmemb, size, pfn):
        self.log.warn("qsort not implemented yet", base=hex(base), n=nmemb)
        return None
