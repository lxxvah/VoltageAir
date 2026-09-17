# app.py
"""主窗口

列序：轮次 / 电压 / 峰值 / 充气时间 / 泄气时间 / 等待 / 状态
充气时间 = stop inflate 时刻 − inflate START 时刻
泄气时间 = slow deflate END 时刻 − slow deflate START 时刻
等待时间 = 下一轮 round_start 时刻 − 本轮 cool down 起点

★ 时间基准：串口模式下 X 轴 0 点对齐到本轮第一次 round_start / inflate_start
★ 已测试 / 等待标签：串口模式下实时刷新，日志回放模式隐藏
★ 去冷却：勾选后把冷却时长从 X 轴删掉，各轮波形首尾相接；
           保留原始 x（raw）数组，显示 x 用「冷却前缀和 + 二分」映射，
           复杂度 O(log N)；轮次分隔线也随开关重画。
"""
import os
import sys
import time
import csv
import re
import bisect
import subprocess
from datetime import datetime

import numpy as np
from serial.tools import list_ports
from PyQt5 import QtCore, QtGui, QtWidgets

from theme import T, make_qss
from parser import DataParser, ParsedLine
from stats import StatsCollector
from plot_panel import create_plot_panel
from serial_worker import SerialWorker
from log_loader import LogLoader
from log_writer import LogWriter


# ==========================================================
# 串口打印行清洗
# ==========================================================
_KEEP_RE = re.compile(
    r'[\x20-\x7e\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]+'
)
_TS_ONLY_RE = re.compile(r'^\[\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?\]\s*$')


def _clean_line(s: str) -> str:
    if not s:
        return s
    cleaned = "".join(_KEEP_RE.findall(s))
    if _TS_ONLY_RE.match(cleaned):
        return ""
    return cleaned


def _load_icon_pixmap(size: int):
    path = os.environ.get("SERIAL_SCOPE_ICON")
    if not path or not os.path.isfile(path):
        return None
    pm = QtGui.QPixmap(path)
    if pm.isNull():
        return None
    return pm.scaled(size, size,
                     QtCore.Qt.KeepAspectRatio,
                     QtCore.Qt.SmoothTransformation)


def _screen_available_size(default=(1440, 900)):
    try:
        app = QtWidgets.QApplication.instance()
        if app is None:
            return default
        scr = app.primaryScreen()
        if scr is None:
            return default
        g = scr.availableGeometry()
        w = int(g.width())
        h = int(g.height())
        if w <= 0 or h <= 0:
            return default
        return (w, h)
    except Exception:
        return default


# ==========================================================
class GrowArray:
    __slots__ = ("_data", "_n")

    def __init__(self, initial=8192):
        self._data = np.empty(initial, dtype=np.float64)
        self._n = 0

    def append(self, v):
        if self._n >= len(self._data):
            cap = len(self._data) * 2
            new = np.empty(cap, dtype=np.float64)
            new[:self._n] = self._data
            self._data = new
        self._data[self._n] = v
        self._n += 1

    def array(self):
        return self._data[:self._n]

    def __len__(self):
        return self._n

    def clear(self):
        self._n = 0


# ==========================================================
class MainWindow(QtWidgets.QMainWindow):

    REFRESH_MS = 30
    MAX_POINTS = 10_000_000
    MAX_RAW_LINES = 100000
    RAW_FLUSH_MS = 50
    RAW_BUF_HARD_MAX = 2000

    # ---------------- 缩放 ----------------
    UI_STARTUP_SCALE = 0.92
    UI_SCALE_MIN  = 0.70
    UI_SCALE_MAX  = 2.0
    UI_SCALE_STEP = 0.05
    TOPBAR_EXTRA  = 0.85

    INIT_WIN_FRAC = 0.92
    MIN_WIN_W = 1080
    MIN_WIN_H = 700

    # ---------------- 布局基准 ----------------
    B_TOPBAR_MARGINS   = (20, 12, 20, 12)
    B_TOPBAR_SPACING   = 8
    B_CMB_PORT_MIN_W   = 180
    B_CMB_BAUD_W       = 100
    B_BTN_CONNECT_MIN_W = 80
    B_VSEP_H           = 22

    B_TOPBAR_FONT_BASE      = 14
    B_TOPBAR_BRAND_BASE     = 13
    B_TOPBAR_FIELD_BASE     = 11
    B_TOPBAR_CONN_BASE      = 12
    B_TOPBAR_BTN_PAD_X      = 12
    B_TOPBAR_BTN_PAD_Y      = 6
    B_TOPBAR_CMB_PAD_X      = 10
    B_TOPBAR_CMB_PAD_Y      = 5
    B_TOPBAR_CMB_DROP_W     = 22
    B_TOPBAR_RADIUS         = 4

    B_BODY_MARGINS     = (20, 18, 20, 18)
    B_BODY_SPACING     = 12
    B_BOTTOM_SPACING   = 12

    B_RAW_CARD_MARGINS = (20, 14, 16, 12)
    B_RAW_CARD_SPACING = 8
    B_RAW_HDR_SPACING  = 8
    B_RAW_SPACER       = 8

    B_STATS_CARD_MARGINS = (20, 14, 16, 12)
    B_STATS_CARD_SPACING = 8
    B_STATS_HDR_SPACING  = 8
    B_BTN_SAVE_H         = 32
    B_STATS_SPACER       = 6

    TABLE_FONT_MIN = 8
    TABLE_FONT_MAX = 14

    STATUS_COL = 6

    def __init__(self):
        super().__init__()
        self.setWindowTitle("串口实时监测 · 气压 / 电压")

        scr_w, scr_h = _screen_available_size((1440, 900))
        init_w = max(self.MIN_WIN_W, int(scr_w * self.INIT_WIN_FRAC))
        init_h = max(self.MIN_WIN_H, int(scr_h * self.INIT_WIN_FRAC))
        self.resize(init_w, init_h)
        self.setMinimumSize(self.MIN_WIN_W, self.MIN_WIN_H)

        self.UI_BASE_WIDTH = float(init_w) / max(0.1, self.UI_STARTUP_SCALE)

        self._ui_scale = self.UI_STARTUP_SCALE
        self.setStyleSheet(make_qss(self._ui_scale))

        self._qss_resize_timer = QtCore.QTimer(self)
        self._qss_resize_timer.setSingleShot(True)
        self._qss_resize_timer.setInterval(180)
        self._qss_resize_timer.timeout.connect(self._on_resize_settle)

        self._fixed_spacers = []
        self._fixed_vseps   = []

        self._last_table_qss = None

        try:
            _win_pm = _load_icon_pixmap(64)
            if _win_pm is not None:
                self.setWindowIcon(QtGui.QIcon(_win_pm))
        except Exception:
            pass

        self.parser = DataParser()
        self.stats  = StatsCollector()
        self.logger = LogWriter(base_dir="logs")

        self.worker = None
        self.loader = None

        self.arr_xp = GrowArray(); self.arr_p = GrowArray()
        self.arr_xv = GrowArray(); self.arr_v = GrowArray()
        # ★ 保留原始 x（未压缩）
        self.arr_raw_xp = GrowArray()
        self.arr_raw_xv = GrowArray()
        self._sample_index = 0

        self.paused      = False
        self.loading     = False
        self.frame_count = 0
        self.bad_count   = 0
        self._last_v     = None
        self._last_p     = None
        self._mem_last   = 0.0

        self._x_unit = None
        self._serial_t0 = None
        self._log_ts0 = None
        self._last_ts = None

        self._test_aligned = False

        self._waiting_active = False
        self._wait_start_t   = None

        # ★ 去冷却状态（前缀和 + 二分）
        self._x_compress       = False
        self._cooldowns        = []       # [(start_raw, end_raw), ...]
        self._cooldown_starts  = []       # 冷却起点数组（递增，供 bisect）
        self._cooldown_prefix  = [0.0]    # 前缀和：prefix[i+1]=prefix[i]+(e-s)
        self._cooldown_start_x = None     # 正在冷却中的起点（raw）

        # ★ 轮次分隔线 (raw_x, color)，切换时重画
        self._round_markers    = []

        self._raw_buf = []
        self._raw_flush_timer = QtCore.QTimer(self)
        self._raw_flush_timer.setInterval(self.RAW_FLUSH_MS)
        self._raw_flush_timer.timeout.connect(self._flush_raw_buffer)
        self._raw_flush_timer.start()

        self._build_ui()

        self.plot["signals"].pauseToggle.connect(self._toggle_pause_key)
        self.plot["signals"].compressToggle.connect(self._on_compress_toggle)
        self.stats.roundAdded.connect(self._on_round_added)
        self.stats.roundUpdated.connect(self._on_round_updated)
        self.stats.changed.connect(self._on_stats_changed)

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(self.REFRESH_MS)
        self.timer.timeout.connect(self._on_refresh)
        self.timer.start()

        self.refresh_ports()

        QtCore.QTimer.singleShot(0, self._fit_stats_table)

    # ======================================================
    # 串口打印缓冲
    # ======================================================
    def _flush_raw_buffer(self):
        if not self._raw_buf:
            return
        lines = self._raw_buf
        self._raw_buf = []
        self.txt_raw.setUpdatesEnabled(False)
        try:
            self.txt_raw.appendPlainText("\n".join(lines))
        finally:
            self.txt_raw.setUpdatesEnabled(True)
        if self.chk_autoscroll.isChecked():
            sb = self.txt_raw.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _push_raw_line(self, line):
        cleaned = _clean_line(line)
        if not cleaned:
            return
        if self.worker is not None:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            cleaned = "[%s] %s" % (ts, cleaned)
        self._raw_buf.append(cleaned)
        if len(self._raw_buf) >= self.RAW_BUF_HARD_MAX:
            self._flush_raw_buffer()

    # ======================================================
    # spacer / vsep 工厂
    # ======================================================
    def _mk_spacer(self, base_w, in_topbar=False):
        s = QtWidgets.QWidget()
        s.setFixedWidth(base_w)
        self._fixed_spacers.append((s, base_w, in_topbar))
        return s

    def _mk_vsep(self, base_h=None, in_topbar=True):
        if base_h is None:
            base_h = self.B_VSEP_H
        f = QtWidgets.QFrame()
        f.setObjectName("vSep")
        f.setFixedWidth(1)
        f.setFixedHeight(base_h)
        self._fixed_vseps.append((f, base_h, in_topbar))
        return f

    # ======================================================
    # UI 构建
    # ======================================================
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        body = QtWidgets.QWidget()
        body.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(*self.B_BODY_MARGINS)
        bl.setSpacing(self.B_BODY_SPACING)
        self._body_layout = bl

        self.plot = create_plot_panel()
        bl.addWidget(self.plot["widget"], 1)

        bottom_wrap = QtWidgets.QWidget()
        bottom_wrap.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Expanding)
        bot = QtWidgets.QHBoxLayout(bottom_wrap)
        bot.setContentsMargins(0, 0, 0, 0)
        bot.setSpacing(self.B_BOTTOM_SPACING)
        self._bottom_layout = bot

        bot.addWidget(self._build_raw_card(), 1)
        bot.addWidget(self._build_stats_card(), 1)

        bl.addWidget(bottom_wrap, 1)

        root.addWidget(body, 1)

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.showMessage("就绪 — 可连接串口或加载日志")

        self.lbl_mem = QtWidgets.QLabel("")
        self.lbl_mem.setObjectName("connState")
        sb.addPermanentWidget(self.lbl_mem)

        try:
            self.plot["set_ui_scale"](getattr(self, "_ui_scale", 1.0))
        except Exception:
            pass

    def _build_raw_card(self):
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(*self.B_RAW_CARD_MARGINS)
        lay.setSpacing(self.B_RAW_CARD_SPACING)
        self._raw_card_layout = lay

        hdr = QtWidgets.QHBoxLayout()
        hdr.setSpacing(self.B_RAW_HDR_SPACING)
        self._raw_hdr_layout = hdr

        lbl = QtWidgets.QLabel("串口打印")
        lbl.setObjectName("cardTitle")
        hdr.addWidget(lbl)

        sub = QtWidgets.QLabel("RAW LOG")
        sub.setObjectName("fieldLabel")
        hdr.addWidget(sub)

        hdr.addStretch(1)

        self.chk_log = QtWidgets.QCheckBox("保存日志")
        self.chk_log.setChecked(True)
        hdr.addWidget(self.chk_log)

        self.btn_open_log = QtWidgets.QPushButton("打开目录")
        self.btn_open_log.clicked.connect(self.open_log_dir)
        hdr.addWidget(self.btn_open_log)

        hdr.addWidget(self._mk_spacer(self.B_RAW_SPACER))

        self.chk_autoscroll = QtWidgets.QCheckBox("自动滚动")
        self.chk_autoscroll.setChecked(True)
        hdr.addWidget(self.chk_autoscroll)

        btn_clear_raw = QtWidgets.QPushButton("清空打印")
        btn_clear_raw.clicked.connect(self.clear_raw_log)
        hdr.addWidget(btn_clear_raw)

        lay.addLayout(hdr)

        self.txt_raw = QtWidgets.QPlainTextEdit()
        self.txt_raw.setObjectName("rawLog")
        self.txt_raw.setReadOnly(True)
        self.txt_raw.setMaximumBlockCount(self.MAX_RAW_LINES)
        self.txt_raw.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        lay.addWidget(self.txt_raw, 1)

        return card

    def _build_stats_card(self):
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        lay = QtWidgets.QVBoxLayout(card)
        lay.setContentsMargins(*self.B_STATS_CARD_MARGINS)
        lay.setSpacing(self.B_STATS_CARD_SPACING)
        self._stats_card_layout = lay

        hdr = QtWidgets.QHBoxLayout()
        hdr.setSpacing(self.B_STATS_HDR_SPACING)
        self._stats_hdr_layout = hdr

        lbl = QtWidgets.QLabel("循环统计")
        lbl.setObjectName("cardTitle")
        hdr.addWidget(lbl)

        sub = QtWidgets.QLabel("ROUND STATISTICS")
        sub.setObjectName("fieldLabel")
        hdr.addWidget(sub)

        hdr.addStretch(1)

        self.btn_save_stats = QtWidgets.QPushButton("保存统计")
        self.btn_save_stats.setFixedHeight(self.B_BTN_SAVE_H)
        self.btn_save_stats.clicked.connect(self.save_stats_csv)
        hdr.addWidget(self.btn_save_stats)

        hdr.addWidget(self._mk_spacer(self.B_STATS_SPACER))

        self.lbl_round = QtWidgets.QLabel()
        self.lbl_round.setObjectName("roundStat")
        hdr.addWidget(self.lbl_round)
        self._update_round_label()

        lay.addLayout(hdr)

        self.table = QtWidgets.QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["轮次", "电压", "峰值", "充气时间", "泄气时间", "等待", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)

        _h = self.table.horizontalHeader()
        _h.setStretchLastSection(False)
        _h.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        _h.setDefaultAlignment(QtCore.Qt.AlignCenter)

        lay.addWidget(self.table, 1)

        return card

    def _build_topbar(self):
        top = QtWidgets.QFrame()
        top.setObjectName("topBar")
        self._topbar = top
        tl = QtWidgets.QHBoxLayout(top)
        tl.setContentsMargins(*self.B_TOPBAR_MARGINS)
        tl.setSpacing(self.B_TOPBAR_SPACING)
        self._topbar_layout = tl

        brand = QtWidgets.QLabel("SERIAL · SCOPE")
        brand.setObjectName("brand")
        tl.addWidget(brand)

        tl.addWidget(self._mk_spacer(4, in_topbar=True))
        tl.addWidget(self._mk_vsep(in_topbar=True))
        tl.addWidget(self._mk_spacer(4, in_topbar=True))

        tl.addWidget(self._field("PORT"))
        self.cmb_port = QtWidgets.QComboBox()
        self.cmb_port.setMinimumWidth(self.B_CMB_PORT_MIN_W)
        tl.addWidget(self.cmb_port)

        btn_refresh = QtWidgets.QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_ports)
        tl.addWidget(btn_refresh)

        tl.addWidget(self._mk_spacer(4, in_topbar=True))
        tl.addWidget(self._field("BAUD"))
        self.cmb_baud = QtWidgets.QComboBox()
        self.cmb_baud.addItems(["9600", "19200", "38400", "57600",
                                "115200", "230400", "460800", "921600"])
        self.cmb_baud.setCurrentText("115200")
        self.cmb_baud.setFixedWidth(self.B_CMB_BAUD_W)
        tl.addWidget(self.cmb_baud)

        tl.addWidget(self._mk_spacer(4, in_topbar=True))
        self.btn_connect = QtWidgets.QPushButton("连接")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.setMinimumWidth(self.B_BTN_CONNECT_MIN_W)
        self.btn_connect.clicked.connect(self.toggle_connect)
        tl.addWidget(self.btn_connect)

        self.btn_pause = QtWidgets.QPushButton("暂停")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self.on_pause)
        tl.addWidget(self.btn_pause)

        self.btn_load = QtWidgets.QPushButton("加载日志")
        self.btn_load.clicked.connect(self.load_log_file)
        tl.addWidget(self.btn_load)

        btn_clear = QtWidgets.QPushButton("清空")
        btn_clear.clicked.connect(self.clear_data)
        tl.addWidget(btn_clear)

        tl.addStretch(1)

        self.lbl_conn = QtWidgets.QLabel("○ 未连接")
        self.lbl_conn.setObjectName("connState")
        tl.addWidget(self.lbl_conn)

        return top

    def _field(self, text):
        l = QtWidgets.QLabel(text)
        l.setObjectName("fieldLabel")
        return l

    # ======================================================
    # 表格字号自适应
    # ======================================================
    def _fit_stats_table(self):
        n = self.table.columnCount()
        if n == 0:
            return
        vp_w = self.table.viewport().width()
        if vp_w <= 0:
            QtCore.QTimer.singleShot(0, self._fit_stats_table)
            return

        col_w = vp_w / float(n)

        def em_width(s: str) -> float:
            w = 0.0
            for ch in s:
                w += 1.0 if ord(ch) > 0x7f else 0.55
            return w

        max_em = 0.0
        for c in range(n):
            item = self.table.horizontalHeaderItem(c)
            if item is None:
                continue
            for line in item.text().split("\n"):
                max_em = max(max_em, em_width(line))

        for r in range(min(self.table.rowCount(), 200)):
            for c in range(n):
                it = self.table.item(r, c)
                if it is None:
                    continue
                max_em = max(max_em, em_width(it.text()))

        pad = 22
        fs = int((col_w - pad) / max(1.0, max_em))
        fs = max(self.TABLE_FONT_MIN, min(self.TABLE_FONT_MAX, fs))

        qss = f"""
            QTableWidget {{
                background: {T.canvas};
                color: {T.ink};
                border: 1px solid {T.hairline};
                border-radius: 6px;
                gridline-color: #eeeeee;
                font-family: {T.MONO};
                font-size: {fs}px;
            }}
            QTableWidget::item {{
                padding: 2px 3px;
            }}
            QTableWidget::item:selected {{
                background: #eef5ff; color: {T.ink};
            }}
            QHeaderView::section {{
                background: {T.surface_soft};
                color: {T.body_mid};
                border: none;
                border-bottom: 1px solid {T.hairline};
                border-right: 1px solid #eeeeee;
                padding: 4px 2px;
                font-family: {T.FONT};
                font-size: {fs}px;
                font-weight: 600;
            }}
        """
        if qss != self._last_table_qss:
            self._last_table_qss = qss
            self.table.setStyleSheet(qss)

    # ======================================================
    # UI 自适应缩放
    # ======================================================
    def _apply_ui_scale(self, scale: float):
        self._ui_scale = scale
        self.setStyleSheet(make_qss(scale))
        self._apply_layout_scale(scale)
        try:
            self.plot["set_ui_scale"](scale)
        except Exception:
            pass

    def _apply_layout_scale(self, scale: float):
        def S(v):
            return max(1, int(round(v * scale)))

        ts = scale * self.TOPBAR_EXTRA
        def ST(v):
            return max(1, int(round(v * ts)))

        self._topbar_layout.setContentsMargins(
            *[ST(v) for v in self.B_TOPBAR_MARGINS])
        self._topbar_layout.setSpacing(ST(self.B_TOPBAR_SPACING))
        self.cmb_port.setMinimumWidth(ST(self.B_CMB_PORT_MIN_W))
        self.cmb_baud.setFixedWidth(ST(self.B_CMB_BAUD_W))
        self.btn_connect.setMinimumWidth(ST(self.B_BTN_CONNECT_MIN_W))

        fs_btn   = max(9, int(round(self.B_TOPBAR_FONT_BASE  * ts)))
        fs_brand = max(9, int(round(self.B_TOPBAR_BRAND_BASE * ts)))
        fs_field = max(8, int(round(self.B_TOPBAR_FIELD_BASE * ts)))
        fs_conn  = max(9, int(round(self.B_TOPBAR_CONN_BASE  * ts)))
        pad_x  = ST(self.B_TOPBAR_BTN_PAD_X)
        pad_y  = ST(self.B_TOPBAR_BTN_PAD_Y)
        cmb_px = ST(self.B_TOPBAR_CMB_PAD_X)
        cmb_py = ST(self.B_TOPBAR_CMB_PAD_Y)
        cmb_dw = ST(self.B_TOPBAR_CMB_DROP_W)
        radius = ST(self.B_TOPBAR_RADIUS)

        self._topbar.setStyleSheet(f"""
            QWidget {{
                font-size: {fs_btn}px;
            }}
            QPushButton {{
                font-size: {fs_btn}px;
                padding: {pad_y}px {pad_x}px;
                border-radius: {radius}px;
            }}
            QComboBox {{
                font-size: {fs_btn}px;
                padding: {cmb_py}px {cmb_px}px;
                border-radius: {radius}px;
            }}
            QComboBox::drop-down {{
                width: {cmb_dw}px;
            }}
            QLabel#brand {{
                font-size: {fs_brand}px;
            }}
            QLabel#fieldLabel {{
                font-size: {fs_field}px;
            }}
            QLabel#connState {{
                font-size: {fs_conn}px;
            }}
        """)

        self._body_layout.setContentsMargins(
            *[S(v) for v in self.B_BODY_MARGINS])
        self._body_layout.setSpacing(S(self.B_BODY_SPACING))
        self._bottom_layout.setSpacing(S(self.B_BOTTOM_SPACING))

        self._raw_card_layout.setContentsMargins(
            *[S(v) for v in self.B_RAW_CARD_MARGINS])
        self._raw_card_layout.setSpacing(S(self.B_RAW_CARD_SPACING))
        self._raw_hdr_layout.setSpacing(S(self.B_RAW_HDR_SPACING))

        self._stats_card_layout.setContentsMargins(
            *[S(v) for v in self.B_STATS_CARD_MARGINS])
        self._stats_card_layout.setSpacing(S(self.B_STATS_CARD_SPACING))
        self._stats_hdr_layout.setSpacing(S(self.B_STATS_HDR_SPACING))
        self.btn_save_stats.setFixedHeight(S(self.B_BTN_SAVE_H))

        self._fit_stats_table()

        for w, base, top in self._fixed_spacers:
            w.setFixedWidth(ST(base) if top else S(base))
        for f, base, top in self._fixed_vseps:
            f.setFixedHeight(ST(base) if top else S(base))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_qss_resize_timer"):
            self._qss_resize_timer.start()

    def _on_resize_settle(self):
        w = self.width()
        new_scale = max(self.UI_SCALE_MIN,
                        min(self.UI_SCALE_MAX,
                            w / self.UI_BASE_WIDTH))
        if abs(new_scale - self._ui_scale) >= self.UI_SCALE_STEP:
            self._apply_ui_scale(new_scale)
        else:
            self._fit_stats_table()

    # ======================================================
    # 交互
    # ======================================================
    def _toggle_pause_key(self):
        self.btn_pause.setChecked(not self.btn_pause.isChecked())

    def clear_raw_log(self):
        self._raw_buf.clear()
        self.txt_raw.clear()

    # ======================================================
    # ★ 去冷却：raw_x → display_x 映射（O(log N)）
    # ======================================================
    def _map_x(self, raw_x):
        """把原始 x 映射为显示 x（去掉冷却时长）。

        使用「冷却起点数组 + 前缀和 + 二分」：
          offset = 该点之前所有完整冷却区间的总时长
                 + 若落在某个区间内，还要加上该区间已过去的时长
        """
        if not self._x_compress:
            return raw_x

        offset = 0.0
        starts = self._cooldown_starts
        if starts:
            i = bisect.bisect_right(starts, raw_x) - 1
            if i >= 0:
                s_i, e_i = self._cooldowns[i]
                if raw_x >= e_i:
                    offset = self._cooldown_prefix[i + 1]
                else:
                    offset = self._cooldown_prefix[i] + (raw_x - s_i)

        # 正在冷却中（区间尚未收尾）
        if (self._cooldown_start_x is not None
                and raw_x > self._cooldown_start_x):
            offset += raw_x - self._cooldown_start_x

        return max(0.0, raw_x - offset)

    def _rebuild_display_x(self):
        """开关切换后重算全部显示 x，并重画轮次分隔线"""
        n = len(self.arr_raw_xp)
        if n:
            data = self.arr_xp._data
            raw  = self.arr_raw_xp._data
            for i in range(n):
                data[i] = self._map_x(raw[i])

        n = len(self.arr_raw_xv)
        if n:
            data = self.arr_xv._data
            raw  = self.arr_raw_xv._data
            for i in range(n):
                data[i] = self._map_x(raw[i])

        # ★ 重画轮次分隔线（保持 raw_x，按当前开关映射）
        try:
            self.plot["clear_round_markers"]()
        except Exception:
            pass
        for rx, c in self._round_markers:
            try:
                self.plot["add_round_marker"](self._map_x(rx), color=c)
            except Exception:
                pass

    def _on_compress_toggle(self, checked):
        """勾选框切换：重算显示 x，重画 marker，平滑刷新视图"""
        self._x_compress = bool(checked)
        self._rebuild_display_x()

        # 把 x_max 归零，让 set_pressure / set_voltage 重新推导
        try:
            self.plot["reset_x_max"](0.0)
        except Exception:
            pass

        # 重新喂数据（会顺带把 x_max 更新到实际值）
        if len(self.arr_xp):
            self.plot["set_pressure"](self.arr_xp.array(),
                                      self.arr_p.array())
        if len(self.arr_xv):
            self.plot["set_voltage"](self.arr_xv.array(),
                                     self.arr_v.array())

        # 平滑刷新视图
        try:
            self.plot["refit_after_rebuild"]()
        except Exception:
            pass

        mode_txt = "去冷却：开" if self._x_compress else "去冷却：关"
        self.statusBar().showMessage(mode_txt)

    # ======================================================
    # 串口
    # ======================================================
    def refresh_ports(self):
        cur = self.cmb_port.currentData()
        self.cmb_port.clear()
        ports = list(list_ports.comports())
        for p in ports:
            text = "%s  (%s)" % (p.device, p.description) if p.description else p.device
            self.cmb_port.addItem(text, p.device)
        if not ports:
            self.cmb_port.addItem("未发现串口", None)
        if cur:
            idx = self.cmb_port.findData(cur)
            if idx >= 0:
                self.cmb_port.setCurrentIndex(idx)

    def toggle_connect(self):
        if self.worker is not None:
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self):
        if self.loading:
            QtWidgets.QMessageBox.information(self, "提示", "日志正在加载，请稍候")
            return
        port = self.cmb_port.currentData()
        if not port:
            QtWidgets.QMessageBox.warning(self, "提示", "没有可用串口")
            return
        baud = int(self.cmb_baud.currentText())

        self.clear_data()

        self._x_unit = "time"
        try:
            self.plot["set_x_label"]("时间 (s)")
        except Exception:
            pass

        hint = ""
        if self.chk_log.isChecked():
            try:
                raw_p = self.logger.open()
                hint = "  |  log: %s" % os.path.basename(raw_p)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "日志",
                                              "无法创建日志: %s" % e)

        self.worker = SerialWorker(port, baud)
        self.worker.lineReceived.connect(self.on_line)
        self.worker.errorOccurred.connect(self.on_serial_error)
        self.worker.stateChanged.connect(self.on_serial_state)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

        self.btn_connect.setText("断开")
        self.cmb_port.setEnabled(False)
        self.cmb_baud.setEnabled(False)
        self.chk_log.setEnabled(False)
        self.lbl_conn.setText("● 已连接 %s @ %d%s" % (port, baud, hint))

    def disconnect_serial(self):
        if self.worker is None:
            return
        w = self.worker
        self.worker = None
        self._disconnect_worker_signals(w)
        w.stop()
        w.wait(2000)
        if self.logger.is_open():
            self.logger.close()
        self._flush_raw_buffer()
        self.btn_connect.setText("连接")
        self.cmb_port.setEnabled(True)
        self.cmb_baud.setEnabled(True)
        self.chk_log.setEnabled(True)
        self.lbl_conn.setText("○ 未连接")
        self._serial_t0 = None
        self._test_aligned = False
        self._waiting_active = False
        self._wait_start_t = None
        try:
            self.plot["set_elapsed"](None)
            self.plot["set_waiting"](False, 0.0)
        except Exception:
            pass

    @staticmethod
    def _disconnect_worker_signals(w):
        for sig_name in ("lineReceived", "errorOccurred",
                         "stateChanged", "finished"):
            sig = getattr(w, sig_name, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except Exception:
                pass

    def on_serial_state(self, opened):
        if opened:
            self.lbl_conn.setText("● 串口已打开，等待数据")

    def on_serial_error(self, msg):
        self.statusBar().showMessage(msg)
        QtWidgets.QMessageBox.critical(self, "串口错误", msg)
        self.disconnect_serial()

    def on_worker_finished(self):
        if self.worker is not None:
            self.worker = None
            if self.logger.is_open():
                self.logger.close()
            self._flush_raw_buffer()
            self.btn_connect.setText("连接")
            self.cmb_port.setEnabled(True)
            self.cmb_baud.setEnabled(True)
            self.chk_log.setEnabled(True)
            self.lbl_conn.setText("○ 串口已关闭")
            self._serial_t0 = None
            self._test_aligned = False
            self._waiting_active = False
            self._wait_start_t = None
            try:
                self.plot["set_elapsed"](None)
                self.plot["set_waiting"](False, 0.0)
            except Exception:
                pass

    # ======================================================
    # 加载日志
    # ======================================================
    def load_log_file(self):
        if self.worker is not None:
            QtWidgets.QMessageBox.information(self, "提示", "请先断开串口")
            return
        if self.loading:
            QtWidgets.QMessageBox.information(self, "提示", "正在加载中")
            return

        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择日志文件", "",
            "所有支持 (*.txt *.TXT *.log *.LOG *.dat *.DAT);;"
            "所有文件 (*)")
        if not path:
            return

        self.clear_data()
        self.loading = True
        self.btn_load.setEnabled(False)
        self.btn_connect.setEnabled(False)

        self.loader = LogLoader(path)
        self.loader.lineReceived.connect(self.on_line)
        self.loader.errorOccurred.connect(self.on_load_error)
        self.loader.stateChanged.connect(self.on_load_state)
        self.loader.progress.connect(self.on_load_progress)
        self.loader.allLinesSent.connect(self.on_load_finished)
        self.loader.start()

        self.lbl_conn.setText("◐ 加载中… %s" % os.path.basename(path))
        self.statusBar().showMessage("开始加载: %s" % path)

    def on_load_error(self, msg):
        QtWidgets.QMessageBox.critical(self, "加载失败", msg)
        self.statusBar().showMessage("加载失败: %s" % msg)

    def on_load_state(self, opened):
        if opened:
            self.statusBar().showMessage("正在加载…")

    def on_load_progress(self, done, total):
        self.statusBar().showMessage(
            "加载中… %d / %d (%.0f%%)"
            % (done, total, (done / total * 100.0) if total else 100.0))

    def on_load_finished(self):
        if self.loader is not None:
            l = self.loader
            self.loader = None
            self._disconnect_loader_signals(l)
            l.wait(2000)

        self.loading = False
        self.btn_load.setEnabled(True)
        self.btn_connect.setEnabled(True)

        # 日志加载完成后如果"去冷却"开着，需要重排一遍显示 x
        if self._x_compress:
            self._rebuild_display_x()
            try:
                self.plot["reset_x_max"](0.0)
            except Exception:
                pass

        self._force_refresh_plot()
        self._rebuild_stats_table()

        self._fit_stats_table()

        self._waiting_active = False
        self._wait_start_t = None
        try:
            self.plot["set_elapsed"](None)
            self.plot["set_waiting"](False, 0.0)
        except Exception:
            pass

        if self._x_compress:
            try:
                self.plot["refit_after_rebuild"]()
            except Exception:
                pass

        s = self.stats.summary()
        self.statusBar().showMessage(
            "加载完成 | 识别 %d 帧 | 轮次 %d | ✓%d ✗%d"
            % (self.frame_count, s['done'], s['ok'], s['fail']))
        self.lbl_conn.setText("◐ 日志回放")

        self._flush_raw_buffer()

    @staticmethod
    def _disconnect_loader_signals(l):
        for sig_name in ("lineReceived", "errorOccurred",
                         "stateChanged", "progress", "allLinesSent"):
            sig = getattr(l, sig_name, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except Exception:
                pass

    # ======================================================
    # 数据入口
    # ======================================================
    def on_line(self, line):
        if self.logger.is_open():
            self.logger.write_raw(line)

        self._push_raw_line(line)

        p = self.parser.parse(line)
        if p is None:
            self.bad_count += 1
            return

        if p.ts is not None:
            self._last_ts = p.ts
        ts_eff = p.ts if p.ts is not None else self._last_ts

        # 串口模式：第一轮测试起点对齐 X 轴 0 点
        if (self.worker is not None
                and not self._test_aligned
                and p.event in ('round_start', 'inflate_start')):
            self._serial_t0 = time.monotonic()
            self._test_aligned = True

        # 串口模式：等待计时
        if self.worker is not None:
            if p.event == 'cooldown':
                self._wait_start_t = time.monotonic()
                self._waiting_active = True
            elif p.event == 'round_start' and self._waiting_active:
                self._waiting_active = False
                self._wait_start_t = None
                try:
                    self.plot["set_waiting"](False, 0.0)
                except Exception:
                    pass

        if self._x_unit is None:
            if self.worker is not None:
                self._x_unit = "time"
            elif ts_eff is not None:
                self._x_unit = "time"
                self._log_ts0 = ts_eff
            elif p.pressure is not None or p.voltage is not None:
                self._x_unit = "sample"
            if self._x_unit is not None:
                try:
                    label = "时间 (s)" if self._x_unit == "time" else "采样点"
                    self.plot["set_x_label"](label)
                except Exception:
                    pass

        if p.pressure is not None or p.voltage is not None:
            self._sample_index += 1

        # ---------- 计算原始 X（未压缩） ----------
        if self._x_unit == "time":
            if self.worker is not None and self._serial_t0 is not None:
                raw_x = time.monotonic() - self._serial_t0
            elif self.worker is not None:
                raw_x = 0.0
            elif ts_eff is not None:
                if self._log_ts0 is None:
                    self._log_ts0 = ts_eff
                raw_x = max(0.0, ts_eff - self._log_ts0)
            else:
                raw_x = float(self._sample_index)
        else:
            raw_x = float(self._sample_index)

        # ★ 冷却区间记录（前缀和版）：cooldown → 起点；round_start → 收尾
        if self._x_unit == "time":
            if p.event == 'cooldown':
                self._cooldown_start_x = raw_x
            elif (p.event == 'round_start'
                    and self._cooldown_start_x is not None):
                if raw_x > self._cooldown_start_x:
                    s = self._cooldown_start_x
                    e = raw_x
                    self._cooldowns.append((s, e))
                    self._cooldown_starts.append(s)
                    self._cooldown_prefix.append(
                        self._cooldown_prefix[-1] + (e - s))
                self._cooldown_start_x = None

        # ★ 显示 X（按当前开关映射）
        x = self._map_x(raw_x)

        if p.pressure is not None:
            self.arr_raw_xp.append(raw_x)
            self.arr_xp.append(x)
            self.arr_p.append(p.pressure)
            self._last_p = p.pressure
        if p.voltage is not None:
            self.arr_raw_xv.append(raw_x)
            self.arr_xv.append(x)
            self.arr_v.append(p.voltage)
            self._last_v = p.voltage

        # ★ 记录轮次分隔线（保存 raw_x，切换时重画）
        if p.event == 'round_start':
            self.plot["add_round_marker"](x, color=T.accent_green)
            self._round_markers.append((raw_x, T.accent_green))
        elif p.event == 'round_done':
            self.plot["add_round_marker"](x, color=T.accent_red)
            self._round_markers.append((raw_x, T.accent_red))

        self.stats.feed(p, x)

        self.frame_count += 1

        n = len(self.arr_p)
        if n > self.MAX_POINTS:
            d = n // 2
            self.arr_raw_xp._data[:n-d] = self.arr_raw_xp._data[d:n]
            self.arr_xp._data[:n-d] = self.arr_xp._data[d:n]
            self.arr_p._data[:n-d] = self.arr_p._data[d:n]
            self.arr_raw_xp._n = n - d
            self.arr_xp._n = n - d
            self.arr_p._n = n - d
        n = len(self.arr_v)
        if n > self.MAX_POINTS:
            d = n // 2
            self.arr_raw_xv._data[:n-d] = self.arr_raw_xv._data[d:n]
            self.arr_xv._data[:n-d] = self.arr_xv._data[d:n]
            self.arr_v._data[:n-d] = self.arr_v._data[d:n]
            self.arr_raw_xv._n = n - d
            self.arr_xv._n = n - d
            self.arr_v._n = n - d

        if not self.loading:
            rt = ""
            if p.round:
                st = p.round['status']
                rt = " | R#%d %s" % (p.round['no'],
                                     "OK" if st == 1 else "FAIL(%d)" % st)
            if self._x_unit == "time":
                x_label = "t=%.2fs" % x
            else:
                x_label = "sample=%d" % self._sample_index
            self.statusBar().showMessage(
                "帧 %d | %s | V=%s | P=%s%s"
                % (self.frame_count, x_label,
                   "-" if p.voltage is None else "%.3f V" % p.voltage,
                   "-" if p.pressure is None else "%.0f mmHg" % p.pressure,
                   rt))

    def _force_refresh_plot(self):
        if len(self.arr_p):
            self.plot["set_pressure"](self.arr_xp.array(), self.arr_p.array())
        if len(self.arr_v):
            self.plot["set_voltage"](self.arr_xv.array(), self.arr_v.array())

        p_text = "— mmHg" if self._last_p is None else "%.0f mmHg" % self._last_p
        v_text = "— V" if self._last_v is None else "%.4f V" % self._last_v
        self.plot["set_values"](p_text, v_text)
        self.plot["apply_view_force"]()

    # ======================================================
    # 统计表
    # ======================================================
    def _rebuild_stats_table(self):
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.stats.rounds))
        for i, rec in enumerate(self.stats.rounds):
            for c, v in enumerate(rec.as_row()):
                item = QtWidgets.QTableWidgetItem(str(v))
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                if c == self.STATUS_COL and rec.status is not None:
                    item.setForeground(
                        QtCore.Qt.black if rec.ok else QtCore.Qt.red)
                self.table.setItem(i, c, item)
        self._update_round_label()

    def _on_round_added(self, idx):
        while self.table.rowCount() < len(self.stats.rounds):
            self.table.insertRow(self.table.rowCount())
        rec = self.stats.rounds[idx]
        for c, v in enumerate(rec.as_row()):
            item = self.table.item(idx, c)
            if item is None:
                item = QtWidgets.QTableWidgetItem(str(v))
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(idx, c, item)
            else:
                item.setText(str(v))
                item.setTextAlignment(QtCore.Qt.AlignCenter)
        self.table.scrollToBottom()
        self._fit_stats_table()

    def _on_round_updated(self, idx):
        if 0 <= idx < self.table.rowCount():
            rec = self.stats.rounds[idx]
            for c, v in enumerate(rec.as_row()):
                item = self.table.item(idx, c)
                if item is None:
                    item = QtWidgets.QTableWidgetItem(str(v))
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
                    self.table.setItem(idx, c, item)
                else:
                    item.setText(str(v))
                    item.setTextAlignment(QtCore.Qt.AlignCenter)
            status_item = self.table.item(idx, self.STATUS_COL)
            if status_item and rec.status is not None:
                status_item.setForeground(
                    QtCore.Qt.black if rec.ok else QtCore.Qt.red)

    def _on_stats_changed(self):
        self._update_round_label()

    def _update_round_label(self):
        s = self.stats.summary()
        total_txt = " / %d" % s['total'] if s['total'] else ""
        self.lbl_round.setText(
            "ROUND %d%s   ✓ %d   ✗ %d"
            % (s['done'], total_txt, s['ok'], s['fail']))

    # ======================================================
    # 保存统计表
    # ======================================================
    def save_stats_csv(self):
        if not self.stats.rounds:
            QtWidgets.QMessageBox.information(self, "提示",
                                              "没有统计数据可保存")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "保存统计表", "round_stats.csv",
            "CSV 文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["轮次", "电压", "峰值",
                                 "充气时间", "泄气时间", "等待", "状态"])
                for rec in self.stats.rounds:
                    writer.writerow(rec.as_row())
            self.statusBar().showMessage(
                "已保存统计表: %s（共 %d 轮）"
                % (path, len(self.stats.rounds)))
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "保存失败", "无法写入文件:\n%s" % e)

    # ======================================================
    # 定时刷新
    # ======================================================
    def _on_refresh(self):
        if self.paused or self.loading:
            return
        now = time.time()
        if now - self._mem_last > 2.0:
            self._mem_last = now
            mb = (len(self.arr_p) + len(self.arr_v)) * 8 / 1024 / 1024
            self.lbl_mem.setText(
                "气压 %d 点 | 电压 %d 点 | 内存 %.1f MB"
                % (len(self.arr_p), len(self.arr_v), mb))
        if len(self.arr_p):
            self.plot["set_pressure"](self.arr_xp.array(), self.arr_p.array())
        if len(self.arr_v):
            self.plot["set_voltage"](self.arr_xv.array(), self.arr_v.array())
        if self._last_p is not None or self._last_v is not None:
            p_text = "— mmHg" if self._last_p is None else "%.0f mmHg" % self._last_p
            v_text = "— V" if self._last_v is None else "%.4f V" % self._last_v
            self.plot["set_values"](p_text, v_text)

        # 已测试时长：串口模式下从 _serial_t0 起算，按当前开关映射
        if self.worker is not None and self._serial_t0 is not None:
            raw_elapsed = time.monotonic() - self._serial_t0
            elapsed = self._map_x(raw_elapsed)
            try:
                self.plot["set_elapsed"](elapsed)
            except Exception:
                pass

        # 等待下一轮：cool down 到达后开始计时
        if self._waiting_active and self._wait_start_t is not None:
            dt = time.monotonic() - self._wait_start_t
            try:
                self.plot["set_waiting"](True, dt)
            except Exception:
                pass

    # ======================================================
    # 其它
    # ======================================================
    def on_pause(self, checked):
        self.paused = checked
        self.btn_pause.setText("继续" if checked else "暂停")

    def open_log_dir(self):
        path = os.path.abspath(self.logger.base_dir)
        if not os.path.isdir(path):
            QtWidgets.QMessageBox.information(self, "日志", "日志目录还未创建")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "日志", "打开失败: %s" % e)

    def clear_data(self):
        self.arr_xp.clear(); self.arr_p.clear()
        self.arr_xv.clear(); self.arr_v.clear()
        self.arr_raw_xp.clear()
        self.arr_raw_xv.clear()
        self._sample_index = 0
        self.frame_count   = 0
        self.bad_count     = 0
        self._last_v       = None
        self._last_p       = None
        self._x_unit = None
        self._log_ts0 = None
        self._last_ts = None
        self._raw_buf.clear()
        self.plot["clear"]()
        self.txt_raw.clear()
        self.parser.reset()
        self.stats.clear()
        self.table.setRowCount(0)
        self._update_round_label()

        self._serial_t0 = None
        self._test_aligned = False

        self._waiting_active = False
        self._wait_start_t = None

        # 去冷却：清空冷却区间和 raw 数组；保留用户当前开关状态
        self._cooldowns = []
        self._cooldown_starts = []
        self._cooldown_prefix = [0.0]
        self._cooldown_start_x = None

        # 轮次分隔线记录也清空
        self._round_markers = []

        try:
            self.plot["set_elapsed"](None)
            self.plot["set_waiting"](False, 0.0)
        except Exception:
            pass

    def closeEvent(self, event):
        self.timer.stop()
        self._raw_flush_timer.stop()
        self._flush_raw_buffer()
        self.plot["stop_timer"]()

        if self.worker is not None:
            w = self.worker
            self.worker = None
            self._disconnect_worker_signals(w)
            w.stop()
            w.wait(2000)

        if self.loader is not None:
            l = self.loader
            self.loader = None
            self._disconnect_loader_signals(l)
            l.stop()
            l.wait(2000)

        QtWidgets.QApplication.processEvents(
            QtCore.QEventLoop.ExcludeUserInputEvents)
        self._flush_raw_buffer()

        if self.logger.is_open():
            self.logger.close()
        event.accept()