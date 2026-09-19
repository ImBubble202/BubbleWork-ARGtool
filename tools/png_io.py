# -*- coding: utf-8 -*-
"""够用的 PNG 读写（不依赖 Pillow）。

   读：8bit RGB/RGBA、非隔行的 PNG
   写：8bit RGB/RGBA PNG、多尺寸 ICO（BMP 格式，Windows 全兼容）

只给 tools/make_logo.py 用，所以功能刚好够。
"""
import struct
import zlib


def read_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是 PNG"
    pos = 8
    idat = b""
    width = height = bitdepth = colortype = interlace = 0
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            width, height, bitdepth, colortype, _cm, _fl, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    assert bitdepth == 8 and interlace == 0, f"只支持 8bit 非隔行（bitdepth={bitdepth} interlace={interlace}）"
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colortype]
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray(width * height * channels)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        f = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if f == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif f == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif f == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif f != 0:
            raise ValueError("未知的 PNG 滤波类型 " + str(f))
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)


def write_png(path, width, height, channels, pixels):
    ctype = {3: 2, 4: 6}[channels]
    raw = bytearray()
    stride = width * channels
    for y in range(height):
        raw.append(0)                       # 不用滤波：文件大一点，但简单可靠
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body
                + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    open(path, "wb").write(png)


def resize(width, height, channels, pixels, nw, nh):
    """任意尺寸缩小（面积平均），做图标够用。"""
    out = bytearray(nw * nh * channels)
    sx = width / nw
    sy = height / nh
    for y in range(nh):
        y0, y1 = int(y * sy), min(height, max(int(y * sy) + 1, int((y + 1) * sy)))
        for x in range(nw):
            x0, x1 = int(x * sx), min(width, max(int(x * sx) + 1, int((x + 1) * sx)))
            acc = [0] * channels
            n = 0
            for yy in range(y0, y1):
                base = (yy * width) * channels
                for xx in range(x0, x1):
                    o = base + xx * channels
                    for c in range(channels):
                        acc[c] += pixels[o + c]
                    n += 1
            o2 = (y * nw + x) * channels
            for c in range(channels):
                out[o2 + c] = acc[c] // n
    return out


def write_ico(path, images):
    """images = [(w, h, BGRA bytes)] → BMP(DIB) 形式的 ICO。"""
    entries, blobs = [], []
    for w, h, bgra in images:
        header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)
        xor = bytearray()
        for y in range(h - 1, -1, -1):                 # DIB 自下而上
            xor += bgra[y * w * 4:(y + 1) * w * 4]
        andmask = bytes(((w + 31) // 32) * 4 * h)
        blob = header + bytes(xor) + andmask
        blobs.append(blob)
        entries.append((w, h, len(blob)))
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for (w, h, size) in entries:
        out += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, size, offset)
        offset += size
    for blob in blobs:
        out += blob
    open(path, "wb").write(bytes(out))
