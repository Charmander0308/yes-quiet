"""yes-quiet 아이콘(브리티시 쇼트헤어 골드 얼굴, 이모지풍)을 PNG로 그린다. Pillow 없이 표준 라이브러리만 쓴다.
python make_icon.py  ->  icon.png(256), icon-32.png(32)
"""
import math
import struct
import zlib
from pathlib import Path

OUTLINE = (138, 90, 43)
GOLD = (233, 182, 104)
GOLD_DARK = (205, 145, 70)
CREAM = (250, 232, 196)
PINK = (240, 150, 150)
EYE = (120, 175, 60)
PUPIL = (40, 30, 20)
WHITE = (255, 255, 255)


# 모양은 256x256 좌표 기준 "거리 함수"(0보다 작으면 안쪽)로 정의한다.
def ellipse(cx, cy, rx, ry):
    return lambda x, y: (math.hypot((x - cx) / rx, (y - cy) / ry) - 1) * min(rx, ry)


def segment(ax, ay, bx, by, w):
    def d(x, y):
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - ax - t * dx, y - ay - t * dy) - w
    return d


def triangle(pts, r):
    def d(x, y):
        dist = min(segment(*pts[i], *pts[(i + 1) % 3], 0)(x, y) for i in range(3))
        cross = [(pts[(i + 1) % 3][0] - pts[i][0]) * (y - pts[i][1]) - (pts[(i + 1) % 3][1] - pts[i][1]) * (x - pts[i][0])
                 for i in range(3)]
        inside = all(c >= 0 for c in cross) or all(c <= 0 for c in cross)
        return (-dist if inside else dist) - r
    return d


def arc_bottom(cx, cy, rad, w):
    """아래쪽 반원 선 (입 모양 ω 한쪽)"""
    return lambda x, y: abs(math.hypot(x - cx, y - cy) - rad) - w if y >= cy else 99


def union(*fs):
    return lambda x, y: min(f(x, y) for f in fs)


def grow(f, w):
    return lambda x, y: f(x, y) - w


def mirror(make, *args):
    """왼쪽 모양과 좌우 대칭인 오른쪽 모양을 함께 만든다."""
    return union(make(*args), lambda x, y: make(*args)(256 - x, y))


ears = mirror(triangle, [(30, 122), (50, 46), (104, 80)], 13)
inner_ears = mirror(triangle, [(50, 108), (58, 68), (90, 86)], 5)
head = ellipse(128, 152, 114, 94)
LAYERS = [
    (grow(union(ears, head), 7), OUTLINE),
    (ears, GOLD),
    (inner_ears, PINK),
    (head, GOLD),
    (mirror(segment, 110, 74, 114, 98, 5), GOLD_DARK),  # 이마 줄무늬
    (segment(128, 70, 128, 98, 5), GOLD_DARK),
    (union(ellipse(106, 186, 32, 24), ellipse(150, 186, 32, 24), ellipse(128, 206, 18, 12)), CREAM),  # 통통한 볼·주둥이
    (mirror(ellipse, 82, 144, 24, 24), OUTLINE),
    (mirror(ellipse, 82, 144, 20, 20), EYE),
    (mirror(ellipse, 82, 145, 8, 15), PUPIL),
    (mirror(ellipse, 75, 136, 5, 5), WHITE),
    (triangle([(116, 170), (140, 170), (128, 181)], 3), PINK),
    (segment(128, 181, 128, 189, 2), OUTLINE),
    (union(arc_bottom(119, 189, 9, 2), arc_bottom(137, 189, 9, 2)), OUTLINE),
    (mirror(segment, 20, 184, 68, 190, 1.6), OUTLINE),  # 수염
    (mirror(segment, 24, 206, 70, 200, 1.6), OUTLINE),
]


def render(size, ss=3):
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG 줄마다 필터 바이트
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) * 256 / size
                    y = (py + (sy + 0.5) / ss) * 256 / size
                    for f, c in reversed(LAYERS):  # 위쪽 레이어부터 보고 처음 맞는 색
                        if f(x, y) < 0:
                            r, g, b, a = r + c[0], g + c[1], b + c[2], a + 1
                            break
            n = ss * ss
            row += bytes([r // a, g // a, b // a, 255 * a // n] if a else [0, 0, 0, 0])
        rows.append(bytes(row))
    return rows


def write_png(path, size):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8비트 RGBA
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                           + chunk(b"IDAT", zlib.compress(b"".join(render(size)))) + chunk(b"IEND", b""))


def write_ico(path, pngs):
    """PNG를 그대로 담은 .ico (Windows Vista 이후 지원). exe 아이콘용."""
    datas = [Path(p).read_bytes() for p in pngs]
    out = struct.pack("<HHH", 0, 1, len(datas))
    offset = 6 + 16 * len(datas)
    for d in datas:
        w = struct.unpack(">I", d[16:20])[0]
        out += struct.pack("<BBBBHHII", w % 256, w % 256, 0, 0, 1, 32, len(d), offset)  # 256px는 0으로 적는 규칙
        offset += len(d)
    Path(path).write_bytes(out + b"".join(datas))


if __name__ == "__main__":
    here = Path(__file__).parent
    write_png(here / "icon.png", 256)
    write_png(here / "icon-32.png", 32)
    write_ico(here / "icon.ico", [here / "icon.png", here / "icon-32.png"])
    print("ok")
