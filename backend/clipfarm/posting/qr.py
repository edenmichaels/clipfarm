"""A small QR code encoder (byte mode, error correction M, versions 1-10) that outputs SVG.

Only used for the "Send to phone" link (about 50 characters), so no third-party package is needed.
Follows ISO/IEC 18004; the tables come from the standard (versions 1-10, levels L and M).
"""
from __future__ import annotations

ECC_PER_BLOCK = {"L": [0, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18], "M": [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]}
NUM_BLOCKS = {"L": [0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4], "M": [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]}
FORMAT_BITS = {"L": 1, "M": 0}


def _raw_modules(ver: int) -> int:
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        a = ver // 7 + 2
        n -= (25 * a - 10) * a - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver: int, ecl: str) -> int:
    return _raw_modules(ver) // 8 - ECC_PER_BLOCK[ecl][ver] * NUM_BLOCKS[ecl][ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data: bytes, ecl: str) -> tuple[int, list[int]]:
    for ver in range(1, 11):
        count_bits = 8 if ver <= 9 else 16
        cap = _data_codewords(ver, ecl) * 8
        if 4 + count_bits + 8 * len(data) <= cap:
            break
    else:
        raise ValueError("Text too long for this QR encoder (max version 10).")
    bits: list[int] = []

    def put(val: int, n: int) -> None:
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4)
    put(len(data), count_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, cap - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    nblocks, ecclen = NUM_BLOCKS[ecl][ver], ECC_PER_BLOCK[ecl][ver]
    raw = _raw_modules(ver) // 8
    nshort, short_len = nblocks - raw % nblocks, raw // nblocks
    div = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        dlen = short_len - ecclen + (0 if i < nshort else 1)
        dat = cw[k:k + dlen]
        k += dlen
        ecc = _rs_remainder(dat, div)
        if i < nshort:
            dat = dat + [None]          # placeholder so every block lines up when interleaving
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != short_len - ecclen or j >= nshort:
                out.append(blk[i])
    return ver, out


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    n = ver // 7 + 2
    step = (ver * 8 + n * 3 + 5) // (n * 4 - 4) * 2
    res = [size - 7 - i * step for i in range(n - 1)]
    return [6] + sorted(res)


MASKS = [lambda x, y: (x + y) % 2, lambda x, y: y % 2, lambda x, y: x % 3, lambda x, y: (x + y) % 3,
         lambda x, y: (x // 3 + y // 2) % 2, lambda x, y: x * y % 2 + x * y % 3,
         lambda x, y: (x * y % 2 + x * y % 3) % 2, lambda x, y: ((x + y) % 2 + x * y % 3) % 2]


def matrix(text: str, ecl: str = "M") -> list[list[bool]]:
    ver, data = _codewords(text.encode("utf-8"), ecl)
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x: int, y: int, dark: bool) -> None:
        mod[y][x] = dark
        fn[y][x] = True
    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    al = _alignment_positions(ver, size)
    for i, ay in enumerate(al):
        for j, ax in enumerate(al):
            if (i == 0 and j == 0) or (i == 0 and j == len(al) - 1) or (i == len(al) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask: int) -> None:
        d = FORMAT_BITS[ecl] << 3 | mask
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (d << 10 | rem) ^ 0x5412
        b = [(bits >> i) & 1 == 1 for i in range(15)]
        for i in range(6):
            setf(8, i, b[i])
        setf(8, 7, b[6])
        setf(8, 8, b[7])
        setf(7, 8, b[8])
        for i in range(9, 15):
            setf(14 - i, 8, b[i])
        for i in range(8):
            setf(size - 1 - i, 8, b[i])
        for i in range(8, 15):
            setf(8, size - 15 + i, b[i])
        setf(8, size - 8, True)
    draw_format(0)                                      # reserves the format areas
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = ver << 12 | rem
        for i in range(18):
            bit = (bits >> i) & 1 == 1
            a, b2 = size - 11 + i % 3, i // 3
            setf(a, b2, bit)
            setf(b2, a, bit)
    i, right = 0, size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for jj in range(2):
                x = right - jj
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fn[y][x] and i < len(data) * 8:
                    mod[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2
    base = [row[:] for row in mod]
    best, best_score = None, None
    for m in range(8):
        for y in range(size):
            for x in range(size):
                mod[y][x] = base[y][x] ^ (not fn[y][x] and MASKS[m](x, y) == 0)
        draw_format(m)
        s = _penalty(mod)
        if best_score is None or s < best_score:
            best, best_score = [row[:] for row in mod], s
    return best


def _penalty(m: list[list[bool]]) -> int:
    n, score = len(m), 0
    for lines in (m, [list(c) for c in zip(*m)]):
        for row in lines:
            run, prev = 0, None
            for v in row:
                run = run + 1 if v == prev else 1
                prev = v
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
    for y in range(n - 1):
        for x in range(n - 1):
            if m[y][x] == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in m)
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def svg(text: str, scale: int = 6, border: int = 4) -> str:
    m = matrix(text)
    n = len(m) + border * 2
    path = "".join(f"M{x + border},{y + border}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{n * scale}" height="{n * scale}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')


def png(text: str, scale: int = 8, border: int = 4):
    """A numpy image (for tests and the API): white background, black modules."""
    import numpy as np
    m = np.array(matrix(text), dtype=np.uint8)
    img = np.pad(1 - m, border, constant_values=1) * 255
    return np.kron(img, np.ones((scale, scale), dtype=np.uint8))
