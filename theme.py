# theme.py
"""界面风格与设计 Token —— 所有颜色/字体/圆角/QSS 集中在此

★ QSS 由 make_qss(scale) 动态生成，scale 由窗口宽度决定，
   实现"窗口越大，字体/间距/圆角都跟着变大"的自适应效果。
"""


class T:
    # ========== 色彩 ==========
    canvas        = "#ffffff"
    primary       = "#080808"
    on_primary    = "#ffffff"
    ink           = "#080808"
    body          = "#363636"
    body_mid      = "#5a5a5a"
    mute          = "#898989"
    mute_soft     = "#ababab"
    hairline      = "#d8d8d8"
    surface_soft  = "#fafafa"

    # ========== 强调色 ==========
    accent_blue   = "#3b89ff"
    accent_orange = "#ff6b00"
    accent_green  = "#00d722"
    accent_red    = "#ee1d36"
    accent_yellow = "#ffae13"

    # ========== 字体 ==========
    FONT = ('"Inter", "Segoe UI", "PingFang SC", '
            '"Microsoft YaHei", system-ui, sans-serif')
    MONO = ('"JetBrains Mono", "Inconsolata", "Menlo", "Consolas", monospace')


def make_qss(scale: float = 1.0) -> str:
    """按 scale 生成 QSS。scale=1.0 相当于原版所有尺寸。

    scale 由 app.py 按窗口宽度/1440 计算，范围 [1.0, 2.0]。
    """
    def px(v: float) -> str:
        return "%dpx" % max(1, round(v * scale))

    # 缓存，避免同一 scale 反复重建字符串
    key = round(scale, 3)
    if make_qss._cache.get("key") == key:
        return make_qss._cache["qss"]

    qss = f"""
/* ============================================================
   全局
   ============================================================ */
QMainWindow, QWidget {{
    background: {T.canvas}; color: {T.ink};
    font-family: {T.FONT}; font-size: {px(14)};
}}

/* ============================================================
   按钮
   ============================================================ */
QPushButton {{
    background: {T.canvas}; color: {T.ink};
    border: 1px solid {T.hairline};
    border-radius: {px(4)};
    padding: {px(6)} {px(12)};
    font-weight: 500;
}}
QPushButton:hover    {{ background: #f7f7f7; }}
QPushButton:pressed  {{ background: #ededed; }}
QPushButton:disabled {{ color: {T.mute_soft}; border-color: #eaeaea; }}

QPushButton#primary {{
    background: {T.primary}; color: {T.on_primary};
    border: 1px solid {T.primary};
}}
QPushButton#primary:hover   {{ background: #222222; }}
QPushButton#primary:pressed {{ background: #000000; }}

/* ============================================================
   下拉框
   ============================================================ */
QComboBox {{
    background: {T.canvas}; color: {T.ink};
    border: 1px solid {T.hairline};
    border-radius: {px(4)};
    padding: {px(5)} {px(10)};
}}
QComboBox:hover {{ border-color: #b8b8b8; }}
QComboBox::drop-down {{ border: none; width: {px(22)}; }}
QComboBox QAbstractItemView {{
    background: {T.canvas}; color: {T.ink};
    border: 1px solid {T.hairline};
    selection-background-color: #f2f2f2;
    selection-color: {T.ink};
    outline: none;
}}

/* ============================================================
   复选框
   ============================================================ */
QCheckBox {{ color: {T.body}; spacing: {px(6)}; }}
QCheckBox::indicator {{
    width: {px(14)}; height: {px(14)};
    border: 1px solid {T.hairline}; border-radius: {px(3)};
    background: {T.canvas};
}}
QCheckBox::indicator:hover {{ border-color: #888888; }}
QCheckBox::indicator:checked {{
    background: {T.primary}; border-color: {T.primary};
}}

/* ============================================================
   文字
   ============================================================ */
QLabel {{ background: transparent; color: {T.ink}; }}

QLabel#brand {{
    color: {T.ink};
    font-size: {px(13)}; font-weight: 600; letter-spacing: {px(1.4)};
}}
QLabel#fieldLabel {{
    color: {T.mute};
    font-size: {px(11)}; font-weight: 600; letter-spacing: {px(1.2)};
}}
QLabel#cardTitle {{
    color: {T.ink};
    font-size: {px(15)}; font-weight: 600; letter-spacing: -0.2px;
}}
QLabel#cardValue {{
    color: {T.body_mid};
    font-family: {T.MONO}; font-size: {px(12)};
}}
QLabel#connState {{
    color: {T.body_mid};
    font-family: {T.MONO}; font-size: {px(12)};
}}
QLabel#roundStat {{
    color: {T.ink};
    font-family: {T.MONO}; font-size: {px(13)};
    font-weight: 600; letter-spacing: 0.4px;
    padding: {px(5)} {px(10)};
    border: 1px solid {T.hairline};
    border-radius: {px(4)};
    background: {T.surface_soft};
}}

/* ============================================================
   容器
   ============================================================ */
QFrame#topBar {{
    background: {T.canvas};
    border: none;
    border-bottom: 1px solid {T.hairline};
}}
QFrame#card {{
    background: {T.canvas};
    border: 1px solid {T.hairline};
    border-radius: {px(8)};
}}
QFrame#vSep {{
    background: {T.hairline};
    max-width: 1px;
    border: none;
}}
QSplitter::handle {{ background: transparent; }}

/* ============================================================
   表格
   ============================================================ */
QTableWidget {{
    background: {T.canvas};
    color: {T.ink};
    border: 1px solid {T.hairline};
    border-radius: {px(6)};
    gridline-color: #eeeeee;
    font-family: {T.MONO};
    font-size: {px(12)};
}}
QTableWidget::item {{ padding: {px(3)} {px(6)}; }}
QTableWidget::item:selected {{ background: #eef5ff; color: {T.ink}; }}
QHeaderView::section {{
    background: {T.surface_soft};
    color: {T.body_mid};
    border: none;
    border-bottom: 1px solid {T.hairline};
    border-right: 1px solid #eeeeee;
    padding: {px(6)} {px(8)};
    font-family: {T.FONT};
    font-size: {px(11)};
    font-weight: 600;
    letter-spacing: {px(1.1)};
}}

/* ============================================================
   串口打印面板
   ============================================================ */
QPlainTextEdit#rawLog {{
    background: {T.surface_soft};
    color: {T.body};
    border: 1px solid #ececec;
    border-radius: {px(6)};
    font-family: {T.MONO};
    font-size: {px(11)};
    padding: {px(6)} {px(8)};
    selection-background-color: #dfeaff;
}}
QPlainTextEdit#rawLog:focus {{
    border: 1px solid #c8c8c8;
}}

/* ============================================================
   滚动条
   ============================================================ */
QScrollBar:vertical {{
    background: transparent;
    width: {px(10)};
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #d8d8d8;
    border-radius: {px(5)};
    min-height: {px(24)};
}}
QScrollBar::handle:vertical:hover {{ background: #b8b8b8; }}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: transparent;
    height: {px(10)};
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #d8d8d8;
    border-radius: {px(5)};
    min-width: {px(24)};
}}
QScrollBar::handle:horizontal:hover {{ background: #b8b8b8; }}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ============================================================
   状态栏
   ============================================================ */
QStatusBar {{
    background: {T.canvas};
    color: {T.body_mid};
    border-top: 1px solid {T.hairline};
    font-family: {T.MONO};
    font-size: {px(12)};
}}
QStatusBar::item {{ border: none; }}

/* ============================================================
   提示
   ============================================================ */
QToolTip {{
    background: {T.primary};
    color: {T.on_primary};
    border: none;
    padding: {px(4)} {px(8)};
    border-radius: {px(4)};
}}
"""
    make_qss._cache["key"] = key
    make_qss._cache["qss"] = qss
    return qss


# 模块级缓存
make_qss._cache = {}

# 兼容旧代码：如果你某处还 from theme import QSS，仍能用（默认 scale=1.0）
QSS = make_qss(1.0)