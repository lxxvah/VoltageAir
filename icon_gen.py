# icon_gen.py
"""程序图标生成 —— 每次启动在 main 里调一次，输出 PNG / ICO

图标内容：圆角外框 + 坐标轴 + 青绿色气压波形 + 红色电压尖峰
"""
import os
from PIL import Image, ImageDraw


# ==========================================================
# 图标绘制参数（以 256px 为基准，按 size 等比缩放）
# ==========================================================
BASE = 256.0

BG_COLOR         = "#ffffff"
LINE_COLOR       = "#555a62"
PRESSURE_COLOR   = "#248888"   # 气压波形：青绿色
VOLTAGE_COLOR    = "#d63031"   # 电压波形：红色

# 外框
MARGIN           = 10
RADIUS           = 28
BORDER_W         = 10

# 坐标轴
AXIS_LEFT        = 40
AXIS_RIGHT       = 220
AXIS_BOTTOM      = 210
AXIS_TOP         = 50
AXIS_W           = 8

# 波形线宽
WAVE_W           = 10

# 气压波形折线（基准坐标）
PRESSURE_WAVE = [
    (50,  180),
    (70,  160),
    (90,  120),
    (110,  90),
    (130,  60),
    (150, 110),
    (170, 150),
    (190, 180),
]

# 电压波形折线（基准坐标）
VOLTAGE_WAVE = [
    (140, 170),
    (155, 140),
    (170, 100),
    (185,  70),
    (200, 130),
    (210, 160),
]


# ==========================================================
def _draw_icon(size: int) -> Image.Image:
    """按给定边长绘制图标，返回 PIL Image。"""
    size = max(1, int(size))
    img = Image.new("RGBA", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)

    k = size / BASE

    def S(v):
        return int(round(v * k))

    def W(v):
        return max(1, int(round(v * k)))

    # ---------- 圆角外框 ----------
    m = S(MARGIN)
    r = S(RADIUS)
    draw.rounded_rectangle(
        [m, m, size - m, size - m],
        radius=r,
        outline=LINE_COLOR,
        width=W(BORDER_W),
    )

    # ---------- 坐标轴 ----------
    ax_l = S(AXIS_LEFT)
    ax_r = S(AXIS_RIGHT)
    ax_b = S(AXIS_BOTTOM)
    ax_t = S(AXIS_TOP)
    aw   = W(AXIS_W)
    draw.line([(ax_l, ax_b), (ax_r, ax_b)], fill=LINE_COLOR, width=aw)
    draw.line([(ax_l, ax_b), (ax_l, ax_t)], fill=LINE_COLOR, width=aw)

    # ---------- 气压波形（青绿色） ----------
    pw = W(WAVE_W)
    for (x1, y1), (x2, y2) in zip(PRESSURE_WAVE, PRESSURE_WAVE[1:]):
        draw.line(
            [(S(x1), S(y1)), (S(x2), S(y2))],
            fill=PRESSURE_COLOR,
            width=pw,
        )

    # ---------- 电压波形（红色） ----------
    for (x1, y1), (x2, y2) in zip(VOLTAGE_WAVE, VOLTAGE_WAVE[1:]):
        draw.line(
            [(S(x1), S(y1)), (S(x2), S(y2))],
            fill=VOLTAGE_COLOR,
            width=pw,
        )

    return img


# ==========================================================
def generate_icon(png_path: str, size: int = 64) -> str:
    """在指定路径生成 PNG 图标，返回路径。

    size: 正方形边长（px）。64 足够 UI 用（会缩到 18 显示），
          exe 打包图标建议另调 generate_ico(size=256)。
    """
    img = _draw_icon(size)
    os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
    img.save(png_path)
    return png_path


def generate_ico(ico_path: str, size: int = 256) -> str:
    """生成 .ico（只给 PyInstaller --icon 用，主程序不需要）"""
    img = _draw_icon(size)
    img.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return ico_path