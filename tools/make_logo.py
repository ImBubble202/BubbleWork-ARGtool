# -*- coding: utf-8 -*-
"""把 assets/logo-source.png 调成界面配色，并生成所有要用的图标。

界面主色（见 src/web/css）：深咖啡底 #170f0a / #2a2017、软木黄褐、暖金 #e4b34a、
奶白纸 #efe6d0、红绳 #e23b3b。原图的冷色（青绿 / 藏蓝，色相 180~230）整体映射到
暖金 / 咖啡色区间；暖色（木板、便签、红绳、白放大镜）保持不动。

产出：
    assets/BubbleWork.ico      七种尺寸，打包时用 --icon 嵌进 exe
    assets/logo-256.png        仓库 / 文档用
    assets/logo-128.png
    assets/mark-64.png
    src/web/img/logo-*.png     界面 favicon
    src/web/img/mark-*.png     标题栏 / 顶栏的小角标（从 logo 上切出软木板那一块）

用法：python tools/make_logo.py      （纯 Python，不需要 Pillow）
"""
import colorsys
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from png_io import read_png, write_png, resize, write_ico   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "logo-source.png")
ASSETS = os.path.join(ROOT, "assets")
WEBIMG = os.path.join(ROOT, "src", "web", "img")


def remap(r, g, b, u=0.5, vv=0.5):
    """单个像素重新上色。u/vv 是归一化坐标（右下角那块密码转盘单独放过）。"""
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    deg = h * 360.0
    if 150.0 <= deg <= 300.0:
        # 冷色 → 暖色：青绿(180°)→亮金黄(42°)，藏蓝(225°)→深咖啡(26°)
        t = min(1.0, max(0.0, (deg - 180.0) / 45.0))
        h = (42.0 - 16.0 * t) / 360.0
        s *= 1.05 - 0.50 * t
        v *= 0.96 - 0.18 * t
        # 原来那圈青色光晕：压暗压灰，变成「皮面上一圈淡淡的暖雾」，别抢软木板
        in_dial = u > 0.52 and vv > 0.50
        if v > 0.45 and not in_dial:
            v *= 0.82
            s *= 0.72
    else:
        s = min(1.0, s * 1.05)          # 木板 / 便签 / 红绳稍微再饱和一点点
    rr, gg, bb = colorsys.hsv_to_rgb(h, min(1.0, s), min(1.0, v))
    r8, g8, b8 = rr * 255.0, gg * 255.0, bb * 255.0
    if s < 0.14 and v > 0.82:           # 纯白的放大镜 → 界面里的奶白纸色
        r8 = r8 * 0.88 + 239 * 0.12
        g8 = g8 * 0.88 + 230 * 0.12
        b8 = b8 * 0.88 + 208 * 0.12
    return int(r8 + 0.5), int(g8 + 0.5), int(b8 + 0.5)


def boost(buf, sat=1.06, val=1.08, add=0.015):
    """小尺寸图标提一点亮度和对比，免得贴在深色任务栏上看不清。"""
    out = bytearray(len(buf))
    for i in range(0, len(buf), 3):
        hh, ss, vv = colorsys.rgb_to_hsv(buf[i] / 255.0, buf[i + 1] / 255.0, buf[i + 2] / 255.0)
        ss, vv = min(1.0, ss * sat), min(1.0, vv * val + add)
        rr, gg, bb = colorsys.hsv_to_rgb(hh, ss, vv)
        out[i], out[i + 1], out[i + 2] = int(rr * 255 + 0.5), int(gg * 255 + 0.5), int(bb * 255 + 0.5)
    return out


def main():
    if not os.path.isfile(SRC):
        print("找不到原图：" + SRC)
        return 1
    os.makedirs(WEBIMG, exist_ok=True)
    w, h, ch, px = read_png(SRC)
    assert ch == 3, "原图要是 RGB（不带透明通道）"

    # 逐像素重新上色
    warm = bytearray(len(px))
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * ch
            r, g, b = remap(px[i], px[i + 1], px[i + 2], (x + 0.5) / w, (y + 0.5) / h)
            warm[i], warm[i + 1], warm[i + 2] = r, g, b

    # 整张 logo：favicon + 仓库用图
    for size in (256, 128, 64, 32, 16):
        write_png(os.path.join(WEBIMG, "logo-%d.png" % size), size, size, ch,
                  resize(w, h, ch, warm, size, size))
    for size in (256, 128):
        write_png(os.path.join(ASSETS, "logo-%d.png" % size), size, size, ch,
                  resize(w, h, ch, warm, size, size))

    # 角标：整张图缩到十几像素会糊成一团，所以切出中间那块软木板（带红绳和照片）
    box = (int(0.288 * w), int(0.263 * h), int(0.668 * w), int(0.643 * h))
    cw, chh = box[2] - box[0], box[3] - box[1]
    crop = bytearray(cw * chh * 4)
    radius = int(min(cw, chh) * 0.18)
    for y in range(chh):
        for x in range(cw):
            si = ((box[1] + y) * w + (box[0] + x)) * ch
            di = (y * cw + x) * 4
            crop[di], crop[di + 1], crop[di + 2] = warm[si], warm[si + 1], warm[si + 2]
            dx, dy = min(x, cw - 1 - x), min(y, chh - 1 - y)
            a = 255
            if dx < radius and dy < radius:
                d = ((radius - dx) ** 2 + (radius - dy) ** 2) ** 0.5
                a = int(max(0.0, min(1.0, (radius - d) / 1.6)) * 255)
            crop[di + 3] = a
    for size in (128, 64, 48, 32, 24):
        write_png(os.path.join(WEBIMG, "mark-%d.png" % size), size, size, 4,
                  resize(cw, chh, 4, bytes(crop), size, size))
    write_png(os.path.join(ASSETS, "mark-64.png"), 64, 64, 4,
              resize(cw, chh, 4, bytes(crop), 64, 64))

    # exe 用的 ICO：小尺寸单独放大一点构图（整张缩到 32px 四周太空，看不清）
    zbox = (int(0.085 * w), int(0.085 * h), int(0.915 * w), int(0.915 * h))
    zw, zh = zbox[2] - zbox[0], zbox[3] - zbox[1]
    zoomed = bytearray(zw * zh * ch)
    for y in range(zh):
        for x in range(zw):
            si = ((zbox[1] + y) * w + (zbox[0] + x)) * ch
            di = (y * zw + x) * ch
            zoomed[di:di + ch] = warm[si:si + ch]

    frames = []
    for size in (256, 128, 64, 48, 32, 24, 16):
        if size <= 32:
            buf = resize(zw, zh, ch, bytes(zoomed), size, size)
        else:
            buf = resize(w, h, ch, warm, size, size)
        if size <= 48:
            buf = boost(buf)
        bgra = bytearray()
        for i in range(0, len(buf), 3):
            bgra += bytes([buf[i + 2], buf[i + 1], buf[i], 255])
        frames.append((size, size, bytes(bgra)))
    write_ico(os.path.join(ASSETS, "BubbleWork.ico"), frames)

    print("生成完毕：")
    for path in (os.path.join(ASSETS, "BubbleWork.ico"),
                 os.path.join(ASSETS, "logo-256.png"),
                 os.path.join(ASSETS, "logo-128.png"),
                 os.path.join(ASSETS, "mark-64.png")):
        print("  {:<34} {:>8} 字节".format(os.path.relpath(path, ROOT), os.path.getsize(path)))
    print("  src/web/img/ 下的 logo-*.png / mark-*.png 也一起更新了")
    return 0


if __name__ == "__main__":
    sys.exit(main())
