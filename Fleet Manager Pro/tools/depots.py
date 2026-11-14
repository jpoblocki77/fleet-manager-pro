"""Developer tool helper: which companies have a depot in which city, read from the game's own archives.

SCS HashFS v2 archives store paths only as CityHash64 hashes. Each company's depots are the files in
def/company/<company>/editor/, and each of those files names its city (company_def { city: ... }).
Used by build_cargo.py.
"""
import os
import re
import struct
import zlib

M = (1 << 64) - 1
K0, K1, K2 = 0xc3a5c85c97cb3127, 0xb492b66fbe98f273, 0x9ae16a3b2f90404f
K3 = 0xc949d7c7509e6557
KMUL = 0x9ddfea08eb382d69


def _f64(s, i):
    return struct.unpack_from("<Q", s, i)[0]


def _f32(s, i):
    return struct.unpack_from("<I", s, i)[0]


def _rot(v, n):
    return v if n == 0 else ((v >> n) | (v << (64 - n))) & M


def _smix(v):
    return v ^ (v >> 47)


def _bswap(v):
    return int.from_bytes(v.to_bytes(8, "little"), "big")


def _h16(u, v, mul=KMUL):
    a = ((u ^ v) * mul) & M
    a ^= a >> 47
    b = ((v ^ a) * mul) & M
    b ^= b >> 47
    return (b * mul) & M


def _h0to16(s, n):                      # CityHash64 v1.0.3 (the version the SCS archives use)
    if n > 8:
        a, b = _f64(s, 0), _f64(s, n - 8)
        return _h16(a, _rot((b + n) & M, n)) ^ b
    if n >= 4:
        return _h16((n + (_f32(s, 0) << 3)) & M, _f32(s, n - 4))
    if n > 0:
        y = s[0] + (s[n >> 1] << 8)
        z = n + (s[n - 1] << 2)
        return (_smix(((y * K2) ^ (z * K3)) & M) * K2) & M
    return K2


def _h17to32(s, n):
    a = (_f64(s, 0) * K1) & M
    b = _f64(s, 8)
    c = (_f64(s, n - 8) * K2) & M
    d = (_f64(s, n - 16) * K0) & M
    return _h16((_rot((a - b) & M, 43) + _rot(c, 30) + d) & M, (a + _rot(b ^ K3, 20) - c + n) & M)


def _h33to64(s, n):
    z = _f64(s, 24)
    a = (_f64(s, 0) + (n + _f64(s, n - 16)) * K0) & M
    b = _rot((a + z) & M, 52)
    c = _rot(a, 37)
    a = (a + _f64(s, 8)) & M
    c = (c + _rot(a, 7)) & M
    a = (a + _f64(s, 16)) & M
    vf = (a + z) & M
    vs = (b + _rot(a, 31) + c) & M
    a = (_f64(s, 16) + _f64(s, n - 32)) & M
    z = _f64(s, n - 8)
    b = _rot((a + z) & M, 52)
    c = _rot(a, 37)
    a = (a + _f64(s, n - 24)) & M
    c = (c + _rot(a, 7)) & M
    a = (a + _f64(s, n - 16)) & M
    wf = (a + z) & M
    ws = (b + _rot(a, 31) + c) & M
    r = _smix(((vf + ws) * K2 + (wf + vs) * K0) & M)
    return (_smix((r * K0 + vs) & M) * K2) & M


def _weak(s, i, a, b):
    w, x, y, z = _f64(s, i), _f64(s, i + 8), _f64(s, i + 16), _f64(s, i + 24)
    a = (a + w) & M
    b = _rot((b + a + z) & M, 21)
    c = a
    a = (a + x + y) & M
    b = (b + _rot(a, 44)) & M
    return (a + z) & M, (b + c) & M


def cityhash64(data):
    s = data.encode() if isinstance(data, str) else data
    n = len(s)
    if n <= 16:
        return _h0to16(s, n)
    if n <= 32:
        return _h17to32(s, n)
    if n <= 64:
        return _h33to64(s, n)
    x = _f64(s, n - 40)
    y = (_f64(s, n - 16) + _f64(s, n - 56)) & M
    z = _h16((_f64(s, n - 48) + n) & M, _f64(s, n - 24))
    v = _weak(s, n - 64, n, z)
    w = _weak(s, n - 32, (y + K1) & M, x)
    x = (x * K1 + _f64(s, 0)) & M
    left, i = (n - 1) & ~63, 0
    while True:
        x = (_rot((x + y + v[0] + _f64(s, i + 8)) & M, 37) * K1) & M
        y = (_rot((y + v[1] + _f64(s, i + 48)) & M, 42) * K1) & M
        x ^= w[1]
        y = (y + v[0] + _f64(s, i + 40)) & M
        z = (_rot((z + w[0]) & M, 33) * K1) & M
        v = _weak(s, i, (v[1] * K1) & M, (x + w[0]) & M)
        w = _weak(s, i + 32, (z + w[1]) & M, (y + _f64(s, i + 16)) & M)
        z, x = x, z
        i += 64
        left -= 64
        if left == 0:
            break
    return _h16((_h16(v[0], w[0]) + _smix(y) * K1 + z) & M, (_h16(v[1], w[1]) + x) & M)


class Archive:
    """Path lookup in an SCS HashFS v2 archive."""

    def __init__(self, path):
        self.f = open(path, "rb")
        head = self.f.read(64)
        self.ok = head[:4] == b"SCS#" and struct.unpack_from("<H", head, 4)[0] == 2
        self.ents = {}
        if not self.ok:
            return
        n_ent, ent_c, _n_meta, meta_c, ent_off, meta_off = struct.unpack_from("<IIIIQQ", head, 12)
        self.f.seek(ent_off)
        ent = zlib.decompress(self.f.read(ent_c))
        self.f.seek(meta_off)
        self.meta = zlib.decompress(self.f.read(meta_c))
        for i in range(n_ent):
            h, mi, mc = struct.unpack_from("<QIH", ent, i * 16)
            self.ents[h] = (mi, mc)

    def _blob(self, path, want):
        e = self.ents.get(cityhash64(path))
        if not e:
            return None
        mi, mc = e
        for k in range(mc):
            word = struct.unpack_from("<I", self.meta, (mi + k) * 4)[0]
            idx, kind = word & 0xFFFFFF, word >> 24
            if kind not in want:
                continue
            csz_flags, size, _unk, off = struct.unpack_from("<IIII", self.meta, idx * 4)
            csize, comp = csz_flags & 0xFFFFFF, csz_flags >> 28
            self.f.seek(off * 16)
            raw = self.f.read(csize)
            try:
                return zlib.decompress(raw) if comp == 1 else (raw if comp == 0 else None)
            except zlib.error:
                return None
        return None

    def read(self, path):
        return self._blob(path, (0x80,))

    def listdir(self, path):
        """Names in a directory; sub-directories start with '/'."""
        d = self._blob(path, (0x81,))
        if not d or len(d) < 4:
            return []
        n = struct.unpack_from("<I", d, 0)[0]
        lens = d[4:4 + n]
        out, p = [], 4 + n
        for ln in lens:
            out.append(d[p:p + ln].decode("utf-8", "replace"))
            p += ln
        return out

    def close(self):
        self.f.close()


CITY_FIELD = re.compile(rb"\bcity\s*:\s*\"?\.?(?:city\.)?([a-z0-9_]+)")


def depots(folder, company_ids):
    """{city_id: sorted [company_id, ...]} for every company depot in the game (base game + DLC archives)."""
    out = {}
    archives = sorted(a for a in os.listdir(folder) if a.endswith(".scs") and (a == "def.scs" or a.startswith("dlc_")))
    for a in archives:
        arc = Archive(os.path.join(folder, a))
        if not arc.ok:
            continue
        for cid in company_ids:
            base = f"def/company/{cid}/editor"
            for name in arc.listdir(base):
                if name.startswith("/") or not name.endswith(".sii"):
                    continue
                data = arc.read(f"{base}/{name}") or b""
                for m in CITY_FIELD.finditer(data):
                    out.setdefault(m.group(1).decode(), set()).add(cid)
        arc.close()
    return {c: sorted(v) for c, v in sorted(out.items())}
