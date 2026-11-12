#!/usr/bin/env python3
"""Passive il2cpp object-graph walker via mread (process_vm_readv, zero injection)."""
import os, socket, struct, subprocess, sys, json, time
import numpy as np

PORT = 27047

class Mem:
    def __init__(self, host="127.0.0.1", port=PORT,
                 serial=None, adb=None):
        self.addr = (host, port)
        self.serial = serial or os.environ.get("AK_ADB_SERIAL", "")
        self.adb = adb or os.environ.get("AK_ADB", "adb.exe" if os.name == "nt" else "adb")
        self.sock = None
        self.maps = []

    def connect(self):
        self.sock = socket.create_connection(self.addr, timeout=15)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def read(self, a, n):
        """Read n bytes at addr a; returns bytes or None on failure."""
        for attempt in range(2):
            try:
                if self.sock is None: self.connect()
                self.sock.sendall(struct.pack("<QI", a, n))
                hdr = self._recv_all(5)
                ok, nl = hdr[0], struct.unpack("<I", hdr[1:5])[0]
                if nl == 0: return None
                return self._recv_all(nl)
            except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                self.sock = None
                if attempt: raise
        return None

    def _recv_all(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(min(1 << 20, n - len(out)))
            if not chunk: raise ConnectionResetError("eof")
            out += chunk
        return out

    def u64(self, a):
        d = self.read(a, 8)
        return struct.unpack("<Q", d)[0] if d else 0

    def u32(self, a):
        d = self.read(a, 4)
        return struct.unpack("<I", d)[0] if d else 0

    def i32(self, a):
        d = self.read(a, 4)
        return struct.unpack("<i", d)[0] if d else 0

    def cstr(self, a, maxlen=128):
        if not a: return None
        d = self.read(a, maxlen)
        if not d: return None
        end = d.find(b"\x00")
        if end < 0: return None
        try: return d[:end].decode("utf-8")
        except UnicodeDecodeError: return None

    def u16str(self, a, n):
        """Read Il2CppString chars."""
        d = self.read(a, n * 2)
        if not d: return None
        try: return d.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError: return None

    def il2str(self, a):
        """Il2CppString: klass(8) monitor(8) len@0x10 chars@0x14"""
        ln = self.i32(a + 0x10)
        if ln is None or ln <= 0 or ln > 8192: return None
        return self.u16str(a + 0x14, ln)

    def load_maps(self, pid):
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += ["exec-out", f"/data/local/tmp/mread {pid} maps"]
        out = subprocess.run(
            cmd,
            capture_output=True).stdout.decode()
        self.maps = []
        for line in out.splitlines():
            if line.startswith("maps:"):
                line = line[5:]
            parts = line.split()
            if len(parts) < 5 or "-" not in parts[0]: continue
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            # Anonymous mappings have no pathname and therefore only five
            # columns. Dropping them hid the managed GC heap from in_region().
            path = parts[5] if len(parts) > 5 else ""
            self.maps.append(dict(lo=lo, hi=hi, perm=parts[1], path=path,
                                  offset=int(parts[2], 16)))
        return self.maps

    def in_region(self, a, need_perm=None):
        for m in self.maps:
            if m["lo"] <= a < m["hi"] and (need_perm is None or need_perm in m["perm"]):
                return True
        return False


def scan_bytes(mem, needle, regions, chunk=4 << 20):
    """Yield offsets of needle in given map regions."""
    hits = []
    for m in regions:
        lo, hi = m["lo"], m["hi"]
        off = 0
        prev_tail = b""
        while lo + off < hi:
            n = min(chunk, hi - lo - off)
            data = mem.read(lo + off, n)
            if data is None:
                off += n; prev_tail = b""; continue
            buf = prev_tail + data
            i = 0
            while True:
                i = buf.find(needle, i)
                if i < 0: break
                hits.append(lo + off - len(prev_tail) + i)
                i += 1
            prev_tail = buf[-len(needle):] if len(needle) <= len(buf) else b""
            off += n
    return hits


def scan_ptrs(mem, targets, regions, chunk=4 << 20):
    """Scan regions for 8-aligned qwords whose value is in `targets` (set of ints)."""
    tarr = np.fromiter(targets, dtype=np.uint64)
    hits = []
    for m in regions:
        lo, hi = m["lo"], m["hi"]
        off = 0
        while off < hi - lo:
            n = min(chunk, hi - lo - off)
            data = mem.read(lo + off, n)
            if data is None:
                off += n; continue
            base = lo + off
            pad = (-base) % 8
            body = data[pad:]
            usable = len(body) // 8 * 8
            if usable <= 0:
                off += n; continue
            arr = np.frombuffer(body[:usable], dtype=np.uint64)
            for i in np.flatnonzero(np.isin(arr, tarr)):
                hits.append(base + pad + int(i) * 8)
            off += n
    return hits


def find_class(mem, name, namespace, verbose=True):
    """Find Il2CppClass* by name string -> pointer -> class object."""
    readable = [m for m in mem.maps if "r" in m["perm"]]
    rw_anon = [m for m in mem.maps if m["perm"] == "rw-p" and not m["path"].startswith("/")]
    str_hits = scan_bytes(mem, name.encode() + b"\x00", rw_anon)
    if not str_hits:
        str_hits = scan_bytes(mem, name.encode() + b"\x00", readable)
    str_addrs = {s for s in str_hits if mem.cstr(s) == name}
    if verbose: print(f"  '{name}': {len(str_addrs)} str copies")
    if not str_addrs:
        return None
    for hit in scan_ptrs(mem, str_addrs, rw_anon):
        cand = hit - 0x10
        if mem.u64(cand + 0x10) not in str_addrs: continue
        if mem.cstr(mem.u64(cand + 0x18)) == namespace:
            return cand
    return None


def scan_objects(mem, class_ptr, regions=None):
    """Yield addresses whose first qword == class_ptr (potential objects)."""
    if regions is None:
        regions = [m for m in mem.maps if m["perm"] == "rw-p" and not m["path"].startswith("/")]
    needle = struct.pack("<Q", class_ptr)
    return scan_bytes(mem, needle, regions)


def main():
    mem = Mem()
    pid_cmd = [mem.adb]
    if mem.serial:
        pid_cmd += ["-s", mem.serial]
    pid_cmd += ["shell", "pidof", "com.hypergryph.arknights.bilibili"]
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else int(subprocess.run(
        pid_cmd,
        capture_output=True, text=True).stdout.strip())

    maps = mem.load_maps(pid)
    print(f"pid={pid} regions={len(maps)}")

    t0 = time.time()
    cls = find_class(mem, "AutoChessGameManager", "Torappu.Battle.AutoChess")
    print(f"find_class: {hex(cls) if cls else None} ({time.time()-t0:.1f}s)")
    if not cls: return

    sf = mem.u64(cls + 0xB8)
    print(f"static_fields @0xB8 -> {hex(sf)}")
    if sf:
        for i in range(8):
            print(f"  sf[{i}] = {hex(mem.u64(sf + i*8))}")

    # scan for live instances
    t0 = time.time()
    objs = scan_objects(mem, cls)
    print(f"object scan: {len(objs)} candidates ({time.time()-t0:.1f}s)")
    for o in objs[:20]:
        gs = mem.i32(o + 0x38)
        dc = mem.u64(o + 0x40)
        print(f"  obj {hex(o)}: gameStatus@0x38={gs} dataCenter@0x40={hex(dc)}"
              f"  plaus={'Y' if (gs is not None and 0 <= gs <= 104 and dc > 0x10000) else 'n'}")

if __name__ == "__main__":
    main()
