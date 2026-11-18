"""Test helper: decode a QR module matrix the way a scanner does, written separately from fmpro/qr.py so the tests
catch encoder mistakes. Reads the format bits (nearest valid word), removes the mask, walks the zigzag, splits the
blocks, checks every Reed-Solomon block (all syndromes zero) and parses byte mode. Raises ValueError on any fault."""

# alignment centres from the standard's table (versions 1-20)
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
         9: [6, 26, 46], 10: [6, 28, 50], 11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
         15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78], 18: [6, 30, 56, 82], 19: [6, 30, 58, 86],
         20: [6, 34, 62, 90]}
# (EC codewords per block, [(blocks, data codewords per block), ...]) for level M, from the standard's table
BLOCKS_M = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
            6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
            10: (26, [(4, 43), (1, 44)]), 11: (30, [(1, 50), (4, 51)]), 12: (22, [(6, 36), (2, 37)]),
            13: (22, [(8, 37), (1, 38)]), 14: (24, [(4, 40), (5, 41)]), 15: (24, [(5, 41), (5, 42)])}
FORMAT_LEVEL = {1: "L", 0: "M", 3: "Q", 2: "H"}

EXP, LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a, b):
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def _format_words():
    out = {}
    for data in range(32):
        g, v = 0b10100110111, data << 10
        for i in range(14, 9, -1):
            if v & (1 << i):
                v ^= g << (i - 10)
        out[((data << 10) | v) ^ 0b101010000010010] = data
    return out


def decode(m):
    n = len(m)
    ver = (n - 17) // 4
    if n != ver * 4 + 17 or ver not in BLOCKS_M:
        raise ValueError(f"unsupported size {n}")
    fn = [[False] * n for _ in range(n)]

    def mark(x0, y0, w, h):
        for y in range(max(0, y0), min(n, y0 + h)):
            for x in range(max(0, x0), min(n, x0 + w)):
                fn[y][x] = True
    mark(0, 0, 9, 9)
    mark(n - 8, 0, 8, 9)
    mark(0, n - 8, 9, 8)
    mark(6, 0, 1, n)
    mark(0, 6, n, 1)
    a = ALIGN[ver]
    for cy in a:
        for cx in a:
            if (cx, cy) in ((6, 6), (6, a[-1]), (a[-1], 6)):
                continue
            mark(cx - 2, cy - 2, 5, 5)
    if ver >= 7:
        mark(n - 11, 0, 3, 6)
        mark(0, n - 11, 6, 3)

    # format bits, first copy (bit 14 = most significant)
    seq = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(i, 8) for i in range(5, -1, -1)]
    raw = 0
    for k, (x, y) in enumerate(seq):
        raw |= int(m[y][x]) << k
    words = _format_words()
    best = min(words, key=lambda w: bin(w ^ raw).count("1"))
    if bin(best ^ raw).count("1") > 3:
        raise ValueError("format bits unreadable")
    data = words[best]
    level, mask = FORMAT_LEVEL[data >> 3], data & 7
    if level != "M":
        raise ValueError(f"expected level M, got {level}")
    if not m[n - 8][8]:
        raise ValueError("dark module missing")
    masks = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
             lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
             lambda x, y: (x * y) % 2 + (x * y) % 3 == 0, lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
             lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0]

    bits = []
    col, upward = n - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for y in rows:
            for x in (col, col - 1):
                if not fn[y][x]:
                    bits.append(int(m[y][x]) ^ int(masks[mask](x, y)))
        upward = not upward
        col -= 2
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - len(bits) % 8, 8)]

    ec, groups = BLOCKS_M[ver]
    sizes = [d for cnt, d in groups for _ in range(cnt)]
    nb = len(sizes)
    blocks = [[] for _ in range(nb)]
    k = 0
    for i in range(max(sizes)):
        for b in range(nb):
            if i < sizes[b]:
                blocks[b].append(cw[k])
                k += 1
    for i in range(ec):
        for b in range(nb):
            blocks[b].append(cw[k])
            k += 1
    for b, blk in enumerate(blocks):                     # every block must be a valid RS codeword
        for i in range(ec):
            s = 0
            for c in blk:
                s = _mul(s, EXP[i]) ^ c
            if s:
                raise ValueError(f"block {b} fails the Reed-Solomon check")
    stream = [c for b, blk in enumerate(blocks) for c in blk[:sizes[b]]]
    sb = "".join(f"{c:08b}" for c in stream)
    if sb[:4] != "0100":
        raise ValueError("not byte mode")
    cbits = 8 if ver <= 9 else 16
    length = int(sb[4:4 + cbits], 2)
    body = sb[4 + cbits:4 + cbits + length * 8]
    return bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8)).decode("utf-8")
