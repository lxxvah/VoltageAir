# plot_panel.py
"""双 Y 轴绘图面板 —— matplotlib + 定时器节流 + 按可视范围降采样 + 完整交互

关键设计：
  · set_pressure / set_voltage 只存引用，不触发绘制
  · 30ms 定时器驱动绘制（锁 33fps）
  · 全局点数 > 5 万时按可视范围降采样到 5000 点
  · QTimer 挂 parent，防止 create_plot_panel 返回后被 GC
  · set_ui_scale(scale)：以"未加缩放前"的原始布局参数为基准等比缩放
  · 断档用虚线：气压正常段实线 + 断档虚线；电压全程实心点 + 断档虚线
    阈值自动推断（采样周期中位数 × 5），用户无需配置
  · 电压实心点大小随可视 x 跨度自适应
  · 鼠标悬停提示框在左上角
  · 标题行：气压 → 电压 → 已测试 HH:MM:SS → 等待 HH:MM:SS
  · ★ 新增"去冷却"勾选框：把冷却时长从 X 轴删掉，各轮波形首尾相接
    对外提供 reset_x_max / refit_after_rebuild 两个 API 供切换时平滑刷新
"""
import io
import math
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

import matplotlib
matplotlib.use('Qt5Agg')
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei',
                                          'DejaVu Sans', 'Arial']
matplotlib.rcParams['axes.unicode_minus'] = False

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator
from matplotlib.patches import Rectangle

from theme import T


# ==========================================================
def _peak_downsample(xs, ys, max_pts=5000):
    n = len(xs)
    if n <= max_pts:
        return xs, ys
    bucket = max(1, n // (max_pts // 2))
    nb = n // bucket
    if nb == 0:
        return xs, ys
    trunc = nb * bucket
    xs_arr = np.asarray(xs)
    ys_arr = np.asarray(ys)
    xs2 = xs_arr[:trunc].reshape(nb, bucket)
    ys2 = ys_arr[:trunc].reshape(nb, bucket)
    imin = ys2.argmin(axis=1)
    imax = ys2.argmax(axis=1)
    i_first = np.minimum(imin, imax)
    i_second = np.maximum(imin, imax)
    out_x = np.empty(nb * 2)
    out_y = np.empty(nb * 2)
    cols = np.arange(nb)
    out_x[0::2] = xs2[cols, i_first]
    out_x[1::2] = xs2[cols, i_second]
    out_y[0::2] = ys2[cols, i_first]
    out_y[1::2] = ys2[cols, i_second]
    if trunc < n:
        out_x = np.concatenate([out_x, xs_arr[trunc:]])
        out_y = np.concatenate([out_y, ys_arr[trunc:]])
    return out_x, out_y


# ==========================================================
def _estimate_gap_threshold(xs):
    """根据数据推断"正常采样周期"，再乘 5 作为断档阈值。"""
    if xs is None or len(xs) < 4:
        return 2.0
    d = np.diff(np.asarray(xs, dtype=float))
    d = d[np.isfinite(d) & (d > 0)]
    if d.size == 0:
        return 2.0
    med = float(np.median(d))
    if med <= 0:
        return 2.0
    return max(med * 5.0, 1.0)


def _volt_dot_size(span_x):
    """电压实心点大小（s = 面积 pt²）随可视 x 跨度自适应。"""
    s_min, s_max = 6, 36
    x = math.log10(max(float(span_x), 1.0))
    t = min(1.0, max(0.0, x / 3.0))
    return int(round(s_max - (s_max - s_min) * t))


# ==========================================================
def _split_with_gaps(xs, ys, gap_threshold):
    """按相邻 x 间隔把数据切成若干连续段。"""
    n = len(xs)
    if n == 0:
        return (np.array([]), np.array([]),
                np.array([]), np.array([]))

    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)

    if n == 1:
        return xs, ys, np.array([]), np.array([])

    d = np.diff(xs)
    break_idx = np.where(d > gap_threshold)[0] + 1

    if len(break_idx) == 0:
        return xs, ys, np.array([]), np.array([])

    starts = [0] + list(break_idx)
    ends   = list(break_idx) + [n]

    solid_x = []
    solid_y = []
    for s, e in zip(starts, ends):
        solid_x.extend(xs[s:e])
        solid_y.extend(ys[s:e])
        solid_x.append(np.nan)
        solid_y.append(np.nan)
    solid_x = np.asarray(solid_x)
    solid_y = np.asarray(solid_y)

    gap_x = []
    gap_y = []
    for bi in break_idx:
        gap_x.extend([xs[bi - 1], xs[bi], np.nan])
        gap_y.extend([ys[bi - 1], ys[bi], np.nan])
    gap_x = np.asarray(gap_x)
    gap_y = np.asarray(gap_y)

    return solid_x, solid_y, gap_x, gap_y


# ==========================================================
def _fmt_hms(seconds):
    """把秒数格式化为 HH:MM:SS。"""
    try:
        secs = int(max(0, float(seconds)))
    except Exception:
        secs = 0
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return "%02d:%02d:%02d" % (h, m, s)


# ==========================================================
class _PanelSignals(QtCore.QObject):
    pauseToggle    = QtCore.pyqtSignal()
    compressToggle = QtCore.pyqtSignal(bool)    # ★ 去冷却开关


# ==========================================================
def create_plot_panel(parent=None):

    # ---------------- 常量 ----------------
    PRESS_YMAX = 250.0
    PRESS_STEP = 50.0
    VOLT_STEP = 0.5
    VOLT_YMAX_DEFAULT = 2.5
    ZOOM_STEP = 0.85
    DRAW_MS = 30
    VIEW_MASK_THRESHOLD = 50000
    DOWNSAMPLE_TARGET   = 5000

    COLOR_PRESS = T.accent_orange
    COLOR_VOLT = T.accent_blue

    GAP_LS    = (0, (3, 6))
    GAP_ALPHA = 0.45
    GAP_W_P   = 1.2
    GAP_W_V   = 1.0

    # ---------------- 布局基准 ----------------
    BASE_CARD_MARGINS = (12, 12, 12, 12)
    BASE_CARD_SPACING = 8
    BASE_HDR_SPACING  = 8
    BASE_DOT_SIZE     = 8
    BASE_DOT_RADIUS   = 4
    BASE_HDR_SPACER     = 18
    BASE_ELAPSED_SPACER = 18
    BASE_WAIT_SPACER    = 15
    BASE_CMB_VIEW_W     = 80

    # ---------------- 状态 ----------------
    state = {
        "press_ymax": PRESS_YMAX,
        "volt_ymax": VOLT_YMAX_DEFAULT,
        "x_max": 100.0,
        "follow": True,
        "locked": False,
        "mode": "all",
        "window_pts": 500,
        "grid": True,
        "drag_mode": None,
        "drag_start": None,
        "drag_start_disp": None,
        "lim_start": None,
        "rect_patch": None,
        "xs_p": None, "ys_p": None,
        "xs_v": None, "ys_v": None,
        "gap_p": 2.0,
        "gap_v": 2.0,
        "need_redraw": False,
        "ui_scale": 1.0,
    }

    signals = _PanelSignals()

    # ---------------- 顶层卡片 ----------------
    card = QtWidgets.QFrame(parent)
    card.setObjectName("card")
    card.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                       QtWidgets.QSizePolicy.Expanding)

    vbox = QtWidgets.QVBoxLayout(card)
    vbox.setContentsMargins(*BASE_CARD_MARGINS)
    vbox.setSpacing(BASE_CARD_SPACING)

    # ---------------- 标题行 ----------------
    hdr = QtWidgets.QHBoxLayout()
    hdr.setSpacing(BASE_HDR_SPACING)

    def _dot(color):
        w = QtWidgets.QLabel()
        w.setFixedSize(BASE_DOT_SIZE, BASE_DOT_SIZE)
        w.setStyleSheet(f"background:{color}; "
                        f"border-radius:{BASE_DOT_RADIUS}px;")
        return w

    def _title(text):
        w = QtWidgets.QLabel(text)
        w.setObjectName("cardTitle")
        return w

    def _value(text):
        w = QtWidgets.QLabel(text)
        w.setObjectName("cardValue")
        return w

    def _field(text):
        w = QtWidgets.QLabel(text)
        w.setObjectName("fieldLabel")
        return w

    dot_p = _dot(COLOR_PRESS)
    dot_v = _dot(COLOR_VOLT)

    hdr.addWidget(dot_p)
    hdr.addWidget(_title("气压"))
    val_p = _value("— mmHg")
    hdr.addWidget(val_p)

    spacer_pv = QtWidgets.QWidget()
    spacer_pv.setFixedWidth(BASE_HDR_SPACER)
    hdr.addWidget(spacer_pv)

    hdr.addWidget(dot_v)
    hdr.addWidget(_title("电压"))
    val_v = _value("— V")
    hdr.addWidget(val_v)

    spacer_el = QtWidgets.QWidget()
    spacer_el.setFixedWidth(BASE_ELAPSED_SPACER)
    hdr.addWidget(spacer_el)

    lbl_elapsed = QtWidgets.QLabel("已测试 00:00:00")
    lbl_elapsed.setObjectName("cardTitle")
    lbl_elapsed.hide()
    hdr.addWidget(lbl_elapsed)

    spacer_w = QtWidgets.QWidget()
    spacer_w.setFixedWidth(BASE_WAIT_SPACER)
    hdr.addWidget(spacer_w)

    lbl_wait = QtWidgets.QLabel("等待 00:00:00")
    lbl_wait.setObjectName("cardTitle")
    lbl_wait.hide()
    hdr.addWidget(lbl_wait)

    hdr.addStretch(1)

    # ★ 去冷却勾选框（放在 VIEW 前）
    chk_compress = QtWidgets.QCheckBox("去冷却")
    chk_compress.setToolTip("勾选后，把冷却/等待时间从 X 轴上删掉，\n"
                            "各轮波形首尾相接显示")
    chk_compress.toggled.connect(signals.compressToggle.emit)
    hdr.addWidget(chk_compress)

    hdr.addWidget(_field("VIEW"))
    cmb_view = QtWidgets.QComboBox()
    for text, data in [("全局", "all"), ("最近 200", "200"),
                       ("最近 500", "500"), ("最近 1000", "1000"),
                       ("最近 2000", "2000")]:
        cmb_view.addItem(text, data)
    cmb_view.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
    cmb_view.setMinimumWidth(60)
    cmb_view.setMaximumWidth(100)
    hdr.addWidget(cmb_view)

    btn_latest = QtWidgets.QPushButton("回到最新")
    hdr.addWidget(btn_latest)

    btn_lock = QtWidgets.QPushButton("锁定视图")
    btn_lock.setCheckable(True)
    hdr.addWidget(btn_lock)

    btn_fit = QtWidgets.QPushButton("自适应")
    hdr.addWidget(btn_fit)

    lbl_follow = QtWidgets.QLabel("● 跟随中")
    lbl_follow.setObjectName("connState")
    lbl_follow.setStyleSheet("color:%s;" % T.accent_green)
    hdr.addWidget(lbl_follow)

    vbox.addLayout(hdr)

    # ---------------- matplotlib 画布 ----------------
    fig = Figure(figsize=(8, 4), dpi=100, facecolor=T.canvas)
    fig.subplots_adjust(left=0.045, right=0.955,
                        top=0.975, bottom=0.14)

    canvas = FigureCanvas(fig)
    canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                         QtWidgets.QSizePolicy.Expanding)
    canvas.setFocusPolicy(QtCore.Qt.StrongFocus)
    vbox.addWidget(canvas, stretch=1)

    ax1 = fig.add_subplot(111)
    ax2 = ax1.twinx()

    ax1.set_facecolor(T.canvas)
    ax2.set_facecolor('none')
    ax1.grid(True, which='major', alpha=0.14, linewidth=0.6)

    ax1.set_xlim(0, 100)
    ax1.set_ylim(0, PRESS_YMAX)
    ax1.margins(0, 0)
    ax2.set_ylim(0, VOLT_YMAX_DEFAULT)

    ax1.set_autoscale_on(False)
    ax2.set_autoscale_on(False)

    ax1.tick_params(axis='y', colors=COLOR_PRESS, labelsize=8,
                    length=3, pad=2)
    ax1.tick_params(axis='x', colors=T.body_mid, labelsize=8,
                    length=3, pad=2)
    ax1.set_ylabel('气压 (mmHg)', color=COLOR_PRESS, fontsize=8,
                   labelpad=2)
    ax1.set_xlabel('时间 (s)', color=T.body_mid, fontsize=8, labelpad=2)

    ax2.tick_params(axis='y', colors=COLOR_VOLT, labelsize=8,
                    length=3, pad=2)
    ax2.set_ylabel('电压 (V)', color=COLOR_VOLT, fontsize=8,
                   labelpad=2)

    ax2.tick_params(axis='x', bottom=False, labelbottom=False)
    ax2.spines['bottom'].set_visible(False)
    ax2.spines['top'].set_visible(False)
    ax2.spines['left'].set_visible(False)

    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax1.spines['left'].set_color(COLOR_PRESS)
    ax1.spines['left'].set_linewidth(1)
    ax1.spines['bottom'].set_color(T.ink)
    ax1.spines['bottom'].set_linewidth(1.2)
    ax2.spines['right'].set_color(COLOR_VOLT)
    ax2.spines['right'].set_linewidth(1)

    ax1.yaxis.set_major_locator(MultipleLocator(PRESS_STEP))
    ax2.yaxis.set_major_locator(MultipleLocator(VOLT_STEP))

    line_p, = ax1.plot([], [], color=COLOR_PRESS, linewidth=1.8,
                       solid_capstyle='round', zorder=3)
    line_p_gap, = ax1.plot([], [], color=COLOR_PRESS, linewidth=GAP_W_P,
                           linestyle=GAP_LS, alpha=GAP_ALPHA, zorder=2)

    scatter_v = ax2.scatter([], [], s=10, color=COLOR_VOLT,
                            marker='o', linewidths=0, zorder=4)
    line_v_gap, = ax2.plot([], [], color=COLOR_VOLT, linewidth=GAP_W_V,
                           linestyle=GAP_LS, alpha=GAP_ALPHA, zorder=2)

    vline = ax1.axvline(0, color='#888888', lw=0.7, alpha=0.7,
                        visible=False, zorder=10)
    hline_p = ax1.axhline(0, color=COLOR_PRESS, lw=0.7, alpha=0.7,
                          visible=False, zorder=10)
    hline_v = ax2.axhline(0, color=COLOR_VOLT, lw=0.7, alpha=0.7,
                          visible=False, zorder=10)

    cursor_text = ax1.text(
        0.01, 0.98, "",
        transform=ax1.transAxes,
        ha='left', va='top',
        fontsize=8, color=T.ink,
        bbox=dict(boxstyle='round,pad=0.35',
                  facecolor='white',
                  edgecolor='#cccccc',
                  alpha=0.92),
        visible=False, zorder=13)

    round_markers = []

    # ==========================================================
    # 辅助
    # ==========================================================
    def _mods(event):
        if not event.key or '+' not in event.key:
            return set()
        return set(event.key.split('+')[:-1])

    def _base_key(event):
        if not event.key:
            return None
        return event.key.split('+')[-1]

    def _span(v):
        return v[1] - v[0]

    def _update_follow_label():
        if state['follow']:
            lbl_follow.setText("● 跟随中")
            lbl_follow.setStyleSheet("color:%s;" % T.accent_green)
        else:
            lbl_follow.setText("○ 已暂停")
            lbl_follow.setStyleSheet("color:%s;" % T.mute)

    def _refresh_x_ticks(x0, x1):
        span = x1 - x0
        if span <= 0:
            return
        raw = span / 10.0
        mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
        step = mag
        for m in (1, 2, 5, 10):
            if span / (m * mag) <= 12:
                step = m * mag
                break
        ax1.xaxis.set_major_locator(MultipleLocator(step))

    def _apply_view(force=False):
        if not state['follow'] or state['locked']:
            return
        x_max = state['x_max']
        if state['mode'] == 'all':
            x0, x1 = 0.0, max(50.0, x_max * 1.02)
        else:
            x0 = max(0.0, x_max - state['window_pts'])
            x1 = x_max
        old = ax1.get_xlim()
        if force or abs(old[0] - x0) / max(1.0, x1 - x0) > 0.01 \
                 or abs(old[1] - x1) / max(1.0, x1 - x0) > 0.01:
            ax1.set_xlim(x0, x1)
            _refresh_x_ticks(x0, x1)

    def _reset_view():
        ax1.set_xlim(0, max(50.0, state['x_max']))
        ax1.set_ylim(0, state['press_ymax'])
        ax2.set_ylim(0, state['volt_ymax'])
        _refresh_x_ticks(0, max(50.0, state['x_max']))
        ax1.yaxis.set_major_locator(MultipleLocator(PRESS_STEP))
        ax2.yaxis.set_major_locator(MultipleLocator(VOLT_STEP))

    def _fit_all():
        state['follow'] = True
        state['locked'] = False
        btn_lock.setChecked(False)
        btn_lock.setText("锁定视图")
        btn_lock.setStyleSheet("")
        _update_follow_label()
        _apply_view(force=True)
        canvas.draw_idle()

    def _fit_to_data():
        state['follow'] = False
        state['locked'] = True
        btn_lock.setChecked(True)
        btn_lock.setText("解锁视图")
        btn_lock.setStyleSheet(
            "QPushButton { background:%s; color:%s; "
            "border:1px solid %s; border-radius:4px; "
            "padding:6px 12px; font-weight:600; }"
            % (T.primary, T.on_primary, T.primary))
        _update_follow_label()

        xs_p = state['xs_p']; ys_p = state['ys_p']
        xs_v = state['xs_v']; ys_v = state['ys_v']

        xs = []
        if xs_p is not None and len(xs_p):
            xs.extend([float(min(xs_p)), float(max(xs_p))])
        if xs_v is not None and len(xs_v):
            xs.extend([float(min(xs_v)), float(max(xs_v))])
        if xs:
            xmin, xmax = min(xs), max(xs)
            if xmax - xmin < 1.0:
                xmin, xmax = xmin - 1, xmax + 1
            margin = (xmax - xmin) * 0.02
            ax1.set_xlim(xmin - margin, xmax + margin)
            _refresh_x_ticks(xmin - margin, xmax + margin)

        if ys_p is not None and len(ys_p):
            pmin = float(min(ys_p)); pmax = float(max(ys_p))
            if pmax - pmin < 1.0:
                pmin, pmax = pmin - 1, pmax + 1
            margin = (pmax - pmin) * 0.10
            ax1.set_ylim(max(0, pmin - margin), pmax + margin)
            span_p = pmax - pmin
            step_p = (5 if span_p <= 30 else
                      20 if span_p <= 100 else
                      50 if span_p <= 300 else 100)
            ax1.yaxis.set_major_locator(MultipleLocator(step_p))

        if ys_v is not None and len(ys_v):
            vmin = float(min(ys_v)); vmax = float(max(ys_v))
            if vmax - vmin < 0.2:
                vmin, vmax = vmin - 0.1, vmax + 0.1
            margin = (vmax - vmin) * 0.10
            ax2.set_ylim(max(0, vmin - margin), vmax + margin)
            span_v = vmax - vmin
            step_v = (0.1 if span_v <= 1.0 else
                      0.5 if span_v <= 3.0 else 1.0)
            ax2.yaxis.set_major_locator(MultipleLocator(step_v))

    def _zoom_center(s):
        xc = sum(ax1.get_xlim()) / 2
        ypc = sum(ax1.get_ylim()) / 2
        yvc = sum(ax2.get_ylim()) / 2
        x0, x1 = ax1.get_xlim()
        ax1.set_xlim(xc - (xc - x0) * s, xc + (x1 - xc) * s)
        y0, y1 = ax1.get_ylim()
        ax1.set_ylim(ypc - (ypc - y0) * s, ypc + (y1 - ypc) * s)
        y0, y1 = ax2.get_ylim()
        ax2.set_ylim(yvc - (yvc - y0) * s, yvc + (y1 - yvc) * s)

    def _axis_pan(x_frac=0.0, y_press_frac=0.0, y_volt_frac=0.0):
        if x_frac:
            x0, x1 = ax1.get_xlim()
            dx = _span((x0, x1)) * x_frac
            ax1.set_xlim(x0 + dx, x1 + dx)
        if y_press_frac:
            y0, y1 = ax1.get_ylim()
            dy = _span((y0, y1)) * y_press_frac
            ax1.set_ylim(y0 + dy, y1 + dy)
        if y_volt_frac:
            y0, y1 = ax2.get_ylim()
            dy = _span((y0, y1)) * y_volt_frac
            ax2.set_ylim(y0 + dy, y1 + dy)

    def _axis_zoom(x_px, y_px, sx, sy_p, sy_v):
        if sx != 1.0:
            cx = ax1.transData.inverted().transform((x_px, y_px))[0]
            x0, x1 = ax1.get_xlim()
            ax1.set_xlim(cx - (cx - x0) * sx, cx + (x1 - cx) * sx)
        if sy_p != 1.0:
            cy = ax1.transData.inverted().transform((x_px, y_px))[1]
            y0, y1 = ax1.get_ylim()
            ax1.set_ylim(cy - (cy - y0) * sy_p, cy + (y1 - cy) * sy_p)
        if sy_v != 1.0:
            cy = ax2.transData.inverted().transform((x_px, y_px))[1]
            y0, y1 = ax2.get_ylim()
            ax2.set_ylim(cy - (cy - y0) * sy_v, cy + (y1 - cy) * sy_v)

    # ==========================================================
    def _on_draw_tick():
        if not state['need_redraw']:
            return
        state['need_redraw'] = False
        x0, x1 = ax1.get_xlim()
        span_x = max(1e-6, x1 - x0)

        # ---------- 气压 ----------
        if state['xs_p'] is not None and len(state['xs_p']):
            xs = state['xs_p']; ys = state['ys_p']
            if len(xs) > VIEW_MASK_THRESHOLD:
                mask = (xs >= x0) & (xs <= x1)
                xs_v = xs[mask]; ys_v = ys[mask]
                if len(xs_v) > DOWNSAMPLE_TARGET:
                    xs_v, ys_v = _peak_downsample(xs_v, ys_v,
                                                  DOWNSAMPLE_TARGET)
            else:
                xs_v, ys_v = xs, ys

            gap_thr = _estimate_gap_threshold(xs)
            sx, sy, gx, gy = _split_with_gaps(xs_v, ys_v, gap_thr)
            line_p.set_data(sx, sy)
            line_p_gap.set_data(gx, gy)
        else:
            line_p.set_data([], [])
            line_p_gap.set_data([], [])

        # ---------- 电压 ----------
        if state['xs_v'] is not None and len(state['xs_v']):
            xs = state['xs_v']; ys = state['ys_v']
            if len(xs) > VIEW_MASK_THRESHOLD:
                mask = (xs >= x0) & (xs <= x1)
                xs_v = xs[mask]; ys_v = ys[mask]
                if len(xs_v) > DOWNSAMPLE_TARGET:
                    xs_v, ys_v = _peak_downsample(xs_v, ys_v,
                                                  DOWNSAMPLE_TARGET)
            else:
                xs_v, ys_v = xs, ys

            gap_thr = _estimate_gap_threshold(xs)
            _, _, gx, gy = _split_with_gaps(xs_v, ys_v, gap_thr)
            line_v_gap.set_data(gx, gy)

            if len(xs_v):
                scatter_v.set_offsets(np.column_stack([xs_v, ys_v]))
                dot_s = _volt_dot_size(span_x)
                scatter_v.set_sizes([dot_s])
            else:
                scatter_v.set_offsets(np.empty((0, 2)))
        else:
            line_v_gap.set_data([], [])
            scatter_v.set_offsets(np.empty((0, 2)))

        _apply_view()
        canvas.draw_idle()

    draw_timer = QtCore.QTimer(card)
    draw_timer.setInterval(DRAW_MS)
    draw_timer.timeout.connect(_on_draw_tick)
    draw_timer.start()

    # ==========================================================
    # 鼠标
    # ==========================================================
    def _on_press(event):
        canvas.setFocus()
        if event.inaxes is None:
            return
        if event.dblclick:
            _fit_all()
            return
        if event.button == 3:
            state['drag_mode'] = 'rect'
            state['drag_start'] = (event.xdata, event.ydata)
            state['drag_start_disp'] = (event.x, event.y)
            if state['rect_patch'] is not None:
                try:
                    state['rect_patch'].remove()
                except Exception:
                    pass
            r = Rectangle((event.xdata, event.ydata), 0, 0,
                          fill=False, edgecolor=COLOR_VOLT,
                          lw=1.0, alpha=0.8, zorder=15)
            ax1.add_patch(r)
            state['rect_patch'] = r
            canvas.draw_idle()
            return
        if event.button == 1:
            mods = _mods(event)
            mode = 'hpan' if 'ctrl' in mods else \
                   'vpan' if 'shift' in mods else 'pan'
            state['drag_mode'] = mode
            state['drag_start'] = (event.xdata, event.ydata)
            state['drag_start_disp'] = (event.x, event.y)
            state['lim_start'] = (ax1.get_xlim(), ax1.get_ylim(),
                                  ax2.get_ylim())

    def _on_motion(event):
        if event.inaxes is not None and event.x is not None:
            x_data = ax1.transData.inverted().transform((event.x, event.y))[0]
            y_p = ax1.transData.inverted().transform((event.x, event.y))[1]
            y_v = ax2.transData.inverted().transform((event.x, event.y))[1]
            vline.set_visible(True); hline_p.set_visible(True)
            hline_v.set_visible(True); cursor_text.set_visible(True)
            vline.set_xdata([x_data, x_data])
            hline_p.set_ydata([y_p, y_p])
            hline_v.set_ydata([y_v, y_v])
            cursor_text.set_text(
                "x = %.2f\n气压 = %.1f mmHg\n电压 = %.3f V"
                % (x_data, y_p, y_v))
            canvas.draw_idle()
        else:
            if not state['drag_mode']:
                vline.set_visible(False); hline_p.set_visible(False)
                hline_v.set_visible(False); cursor_text.set_visible(False)
                canvas.draw_idle()

        dm = state['drag_mode']

        if dm == 'rect' and event.xdata is not None and event.ydata is not None:
            x0, y0 = state['drag_start']
            x1, y1 = event.xdata, event.ydata
            r = state['rect_patch']
            if r is not None:
                r.set_bounds(min(x0, x1), min(y0, y1),
                             abs(x1 - x0), abs(y1 - y0))
                canvas.draw_idle()
            return

        if dm in ('pan', 'hpan', 'vpan'):
            if event.xdata is None or event.ydata is None:
                return
            x0d, y0d = state['drag_start']
            dx = event.xdata - x0d
            dy = event.ydata - y0d
            x0, x1 = state['lim_start'][0]
            y0p, y1p = state['lim_start'][1]
            y0v, y1v = state['lim_start'][2]
            if dm in ('pan', 'hpan'):
                ax1.set_xlim(x0 - dx, x1 - dx)
            if dm in ('pan', 'vpan'):
                ax1.set_ylim(y0p - dy, y1p - dy)
                span_p = y1p - y0p
                span_v = y1v - y0v
                dy_v = dy * (span_v / span_p) if span_p else 0
                ax2.set_ylim(y0v - dy_v, y1v - dy_v)
            if state['follow'] and not state['locked']:
                state['follow'] = False
                _update_follow_label()
            canvas.draw_idle()

    def _on_release(event):
        if state['drag_mode'] == 'rect':
            r = state['rect_patch']
            is_click = True
            if event.x is not None and state['drag_start_disp']:
                dxp = abs(event.x - state['drag_start_disp'][0])
                dyp = abs(event.y - state['drag_start_disp'][1])
                is_click = (dxp < 5 and dyp < 5)
            if is_click:
                if r is not None:
                    try:
                        r.remove()
                    except Exception:
                        pass
                    state['rect_patch'] = None
                _show_context_menu()
            else:
                if r is not None:
                    x0, y0 = r.get_xy()
                    x1 = x0 + r.get_width()
                    ax1.set_xlim(min(x0, x1), max(x0, x1))
                    _refresh_x_ticks(min(x0, x1), max(x0, x1))
                    try:
                        r.remove()
                    except Exception:
                        pass
                    state['rect_patch'] = None
                    if state['follow'] and not state['locked']:
                        state['follow'] = False
                        _update_follow_label()
                canvas.draw_idle()
        state['drag_mode'] = None
        state['drag_start'] = None
        state['drag_start_disp'] = None
        state['lim_start'] = None

    def _on_scroll(event):
        if event.inaxes is None or event.x is None:
            return
        if event.button == 'up':
            s = ZOOM_STEP
        elif event.button == 'down':
            s = 1.0 / ZOOM_STEP
        else:
            return
        mods = _mods(event)
        if mods == {'ctrl'}:
            sx, sy_p, sy_v = s, 1.0, 1.0
        elif mods == {'shift'}:
            sx, sy_p, sy_v = 1.0, s, 1.0
        elif mods == {'alt'}:
            sx, sy_p, sy_v = 1.0, 1.0, s
        elif mods == {'ctrl', 'shift'}:
            sx, sy_p, sy_v = 1.0, s, s
        else:
            sx, sy_p, sy_v = s, s, s
        _axis_zoom(event.x, event.y, sx, sy_p, sy_v)
        if state['follow'] and not state['locked']:
            state['follow'] = False
            _update_follow_label()
        canvas.draw_idle()

    def _on_key(event):
        base = _base_key(event)
        if base is None:
            return
        mods = _mods(event)

        if base in ('left', 'right', 'up', 'down'):
            if base == 'left':
                _axis_pan(x_frac=-0.1)
            elif base == 'right':
                _axis_pan(x_frac=+0.1)
            elif base == 'up':
                if 'shift' in mods:
                    _axis_pan(y_press_frac=-0.1)
                elif 'alt' in mods:
                    _axis_pan(y_volt_frac=-0.1)
                else:
                    _axis_pan(y_press_frac=-0.1, y_volt_frac=-0.1)
            else:
                if 'shift' in mods:
                    _axis_pan(y_press_frac=+0.1)
                elif 'alt' in mods:
                    _axis_pan(y_volt_frac=+0.1)
                else:
                    _axis_pan(y_press_frac=+0.1, y_volt_frac=+0.1)
            if state['follow'] and not state['locked']:
                state['follow'] = False
                _update_follow_label()
            canvas.draw_idle()
            return

        if base in ('+', '=', 'add'):
            _zoom_center(ZOOM_STEP); canvas.draw_idle(); return
        if base in ('-', '_', 'subtract'):
            _zoom_center(1.0 / ZOOM_STEP); canvas.draw_idle(); return
        if base == 'home':
            x1 = max(50.0, state['x_max'])
            ax1.set_xlim(0, x1); _refresh_x_ticks(0, x1)
            if state['follow'] and not state['locked']:
                state['follow'] = False; _update_follow_label()
            canvas.draw_idle(); return
        if base == 'end':
            x_max = state['x_max']
            span = ax1.get_xlim()[1] - ax1.get_xlim()[0]
            x0 = max(0.0, x_max - span)
            ax1.set_xlim(x0, x_max); _refresh_x_ticks(x0, x_max)
            if state['follow'] and not state['locked']:
                state['follow'] = False; _update_follow_label()
            canvas.draw_idle(); return
        if base == '0':
            _reset_view(); canvas.draw_idle(); return
        if base == 'f':
            _fit_to_data(); canvas.draw_idle(); return
        if base == 'g':
            state['grid'] = not state['grid']
            ax1.grid(state['grid'], which='major',
                     alpha=0.14, linewidth=0.6)
            canvas.draw_idle(); return
        if base in (' ', 'space'):
            signals.pauseToggle.emit(); return
        if base == 'l':
            btn_lock.setChecked(not btn_lock.isChecked()); return
        if base in ('1', '2', '3', '4', '5'):
            idx = int(base) - 1
            if 0 <= idx < cmb_view.count():
                cmb_view.setCurrentIndex(idx)
            return

    def _show_context_menu():
        menu = QtWidgets.QMenu(canvas)
        act_copy = menu.addAction("复制图片到剪贴板")
        act_save = menu.addAction("保存图片为 PNG...")
        menu.addSeparator()
        act_reset = menu.addAction("重置视图 (0)")
        act_grid = menu.addAction(
            "关闭网格 (G)" if state['grid'] else "开启网格 (G)")
        act_follow = menu.addAction("回到最新")
        act_fit = menu.addAction("贴合数据 (F)")

        chosen = menu.exec_(QtGui.QCursor.pos())
        if chosen is None:
            return

        if chosen == act_copy:
            _copy_to_clipboard()
        elif chosen == act_save:
            _save_png()
        elif chosen == act_reset:
            _reset_view(); canvas.draw_idle()
        elif chosen == act_grid:
            state['grid'] = not state['grid']
            ax1.grid(state['grid'], which='major',
                     alpha=0.14, linewidth=0.6)
            canvas.draw_idle()
        elif chosen == act_follow:
            state['follow'] = True
            _update_follow_label()
            _apply_view(force=True)
            canvas.draw_idle()
        elif chosen == act_fit:
            _fit_to_data(); canvas.draw_idle()

    def _copy_to_clipboard():
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, facecolor=T.canvas)
        buf.seek(0)
        img = QtGui.QImage.fromData(buf.getvalue(), 'PNG')
        QtWidgets.QApplication.clipboard().setImage(img)

    def _save_png():
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            card, "保存图片", "plot.png", "PNG (*.png)")
        if not path:
            return
        if not path.lower().endswith('.png'):
            path += '.png'
        fig.savefig(path, dpi=150, facecolor=T.canvas)

    canvas.mpl_connect('button_press_event', _on_press)
    canvas.mpl_connect('button_release_event', _on_release)
    canvas.mpl_connect('motion_notify_event', _on_motion)
    canvas.mpl_connect('scroll_event', _on_scroll)
    canvas.mpl_connect('key_press_event', _on_key)

    # ==========================================================
    def _on_view_changed(idx):
        mode = cmb_view.currentData()
        if mode == 'all':
            state['mode'] = 'all'
        else:
            state['mode'] = 'window'
            state['window_pts'] = float(mode)
        state['follow'] = True
        _update_follow_label()
        _apply_view(force=True)
        canvas.draw_idle()

    def _on_latest():
        state['follow'] = True
        _update_follow_label()
        _apply_view(force=True)
        canvas.draw_idle()

    def _on_lock(checked):
        state['locked'] = checked
        btn_lock.setText("解锁视图" if checked else "锁定视图")
        if checked:
            btn_lock.setStyleSheet(
                "QPushButton { background:%s; color:%s; "
                "border:1px solid %s; border-radius:4px; "
                "padding:6px 12px; font-weight:600; }"
                % (T.primary, T.on_primary, T.primary))
        else:
            btn_lock.setStyleSheet("")
            state['follow'] = True
            _update_follow_label()
            _apply_view(force=True)
            canvas.draw_idle()

    def _on_fit():
        _fit_to_data()
        canvas.draw_idle()

    cmb_view.currentIndexChanged.connect(_on_view_changed)
    btn_latest.clicked.connect(_on_latest)
    btn_lock.toggled.connect(_on_lock)
    btn_fit.clicked.connect(_on_fit)

    # ==========================================================
    # 对外 API
    # ==========================================================
    def set_pressure(xs, ys):
        state['xs_p'] = xs
        state['ys_p'] = ys
        if len(xs):
            x_max = float(xs[-1])
            if x_max > state['x_max']:
                state['x_max'] = x_max
            state['gap_p'] = _estimate_gap_threshold(xs)
        state['need_redraw'] = True

    def set_voltage(xs, ys):
        if not len(xs):
            return
        state['xs_v'] = xs
        state['ys_v'] = ys
        x_max = float(xs[-1])
        if x_max > state['x_max']:
            state['x_max'] = x_max
        state['gap_v'] = _estimate_gap_threshold(xs)

        need = float(max(ys)) * 1.02
        if need > state['volt_ymax']:
            state['volt_ymax'] = math.ceil(need / 0.5) * 0.5
            ax2.set_ylim(0, state['volt_ymax'])
            ax2.yaxis.set_major_locator(MultipleLocator(VOLT_STEP))
        state['need_redraw'] = True

    def set_x_range(x0, x1):
        ax1.set_xlim(x0, x1)
        _refresh_x_ticks(x0, x1)
        canvas.draw_idle()

    def set_x_label(text):
        fs = max(7, round(8 * state['ui_scale']))
        ax1.set_xlabel(text, color=T.body_mid, fontsize=fs, labelpad=2)
        canvas.draw_idle()

    def set_elapsed(seconds):
        """已测试时长：None / 负数 → 隐藏；否则显示 '已测试 HH:MM:SS'"""
        if seconds is None:
            lbl_elapsed.hide()
            return
        try:
            secs = float(seconds)
        except Exception:
            lbl_elapsed.hide()
            return
        if secs < 0:
            lbl_elapsed.hide()
            return
        lbl_elapsed.setText("已测试 " + _fmt_hms(secs))
        lbl_elapsed.show()

    def set_waiting(active, seconds=0.0):
        """等待下一轮：active=True 显示 '等待 HH:MM:SS'，False 隐藏"""
        if active:
            try:
                secs = float(seconds)
            except Exception:
                secs = 0.0
            lbl_wait.setText("等待 " + _fmt_hms(secs))
            lbl_wait.show()
        else:
            lbl_wait.hide()

    def reset_x_max(v=100.0):
        """★ 切换去冷却后重置内部 x_max（不触发视图刷新）"""
        try:
            state['x_max'] = max(0.0, float(v))
        except Exception:
            state['x_max'] = 100.0

    def refit_after_rebuild():
        """★ 数据重排后平滑刷新视图：
             跟随中 → 跳到最新
             锁定中 → 保持锁定但重置到 [0, x_max]，避免停留在旧范围
        """
        try:
            if state['follow'] and not state['locked']:
                _apply_view(force=True)
            else:
                x_max = max(50.0, state['x_max'])
                ax1.set_xlim(0, x_max)
                _refresh_x_ticks(0, x_max)
        except Exception:
            pass
        canvas.draw_idle()

    def set_ui_scale(scale):
        try:
            scale = float(scale)
        except Exception:
            return
        if scale <= 0:
            return
        state['ui_scale'] = scale

        def S(v):
            return max(1, int(round(v * scale)))

        fs      = max(7, min(18, round(8 * scale)))
        fs_tick = max(6, fs - 1)

        ax1.tick_params(axis='y', colors=COLOR_PRESS,
                        labelsize=fs_tick, length=3, pad=2)
        ax1.tick_params(axis='x', colors=T.body_mid,
                        labelsize=fs_tick, length=3, pad=2)
        ax2.tick_params(axis='y', colors=COLOR_VOLT,
                        labelsize=fs_tick, length=3, pad=2)

        ax1.set_ylabel('气压 (mmHg)', color=COLOR_PRESS,
                       fontsize=fs, labelpad=2)
        ax2.set_ylabel('电压 (V)', color=COLOR_VOLT,
                       fontsize=fs, labelpad=2)
        xlabel = ax1.get_xlabel() or '时间 (s)'
        ax1.set_xlabel(xlabel, color=T.body_mid,
                       fontsize=fs, labelpad=2)

        cursor_text.set_fontsize(fs)

        left   = min(0.10, 0.045 * scale)
        right  = 1.0 - left
        bottom = max(0.14, min(0.28, 0.14 * scale))
        top    = 0.975
        fig.subplots_adjust(left=left, right=right, top=top, bottom=bottom)

        vbox.setContentsMargins(*[S(v) for v in BASE_CARD_MARGINS])
        vbox.setSpacing(S(BASE_CARD_SPACING))
        hdr.setSpacing(S(BASE_HDR_SPACING))

        dot_p.setFixedSize(S(BASE_DOT_SIZE), S(BASE_DOT_SIZE))
        dot_p.setStyleSheet("background:%s; border-radius:%dpx;"
                            % (COLOR_PRESS, max(2, S(BASE_DOT_RADIUS))))
        dot_v.setFixedSize(S(BASE_DOT_SIZE), S(BASE_DOT_SIZE))
        dot_v.setStyleSheet("background:%s; border-radius:%dpx;"
                            % (COLOR_VOLT, max(2, S(BASE_DOT_RADIUS))))

        spacer_pv.setFixedWidth(S(BASE_HDR_SPACER))
        spacer_el.setFixedWidth(S(BASE_ELAPSED_SPACER))
        spacer_w.setFixedWidth(S(BASE_WAIT_SPACER))

        # ★ 勾选框字号同步
        try:
            f = chk_compress.font()
            f.setPixelSize(max(8, int(round(12 * scale))))
            chk_compress.setFont(f)
        except Exception:
            pass

        canvas.draw_idle()

    def apply_view_force():
        _apply_view(force=True)
        canvas.draw_idle()

    def set_values(p_text, v_text):
        val_p.setText(p_text)
        val_v.setText(v_text)

    def add_round_marker(x, color=None):
        c = color or T.accent_green
        line = ax1.axvline(x=x, color=c, linestyle='--',
                           linewidth=1, alpha=0.7)
        round_markers.append(line)

    def clear_round_markers():
        """只清空轮次分隔线（用于去冷却开关切换后重画）"""
        for m in round_markers:
            try:
                m.remove()
            except Exception:
                pass
        round_markers.clear()
        canvas.draw_idle()

    def clear():
        line_p.set_data([], [])
        line_p_gap.set_data([], [])
        line_v_gap.set_data([], [])
        scatter_v.set_offsets(np.empty((0, 2)))

        state['xs_p'] = None; state['ys_p'] = None
        state['xs_v'] = None; state['ys_v'] = None
        state['gap_p'] = 2.0; state['gap_v'] = 2.0
        state['need_redraw'] = False
        for m in round_markers:
            try:
                m.remove()
            except Exception:
                pass
        round_markers.clear()

        state['volt_ymax'] = VOLT_YMAX_DEFAULT
        state['press_ymax'] = PRESS_YMAX
        state['x_max'] = 100.0
        state['follow'] = True
        state['locked'] = False
        state['mode'] = 'all'
        state['window_pts'] = 500
        state['grid'] = True

        btn_lock.setChecked(False)
        btn_lock.setText("锁定视图")
        btn_lock.setStyleSheet("")

        cmb_view.blockSignals(True)
        cmb_view.setCurrentIndex(0)
        cmb_view.blockSignals(False)

        ax1.set_xlim(0, 100)
        ax1.set_ylim(0, PRESS_YMAX)
        ax2.set_ylim(0, VOLT_YMAX_DEFAULT)
        ax1.grid(True, which='major', alpha=0.14, linewidth=0.6)
        _refresh_x_ticks(0, 100)
        ax1.yaxis.set_major_locator(MultipleLocator(PRESS_STEP))
        ax2.yaxis.set_major_locator(MultipleLocator(VOLT_STEP))

        _update_follow_label()
        val_p.setText("— mmHg")
        val_v.setText("— V")

        lbl_elapsed.hide()
        lbl_wait.hide()

        canvas.draw_idle()

    def toggle_lock():
        btn_lock.setChecked(not btn_lock.isChecked())

    def stop_timer():
        draw_timer.stop()

    _refresh_x_ticks(0, 100)
    _update_follow_label()
    canvas.draw_idle()

    return {
        "widget":               card,
        "signals":              signals,
        "set_pressure":         set_pressure,
        "set_voltage":          set_voltage,
        "set_x_range":          set_x_range,
        "set_x_label":          set_x_label,
        "set_ui_scale":         set_ui_scale,
        "set_elapsed":          set_elapsed,
        "set_waiting":          set_waiting,
        "reset_x_max":          reset_x_max,
        "refit_after_rebuild":  refit_after_rebuild,
        "apply_view_force":     apply_view_force,
        "set_values":           set_values,
        "add_round_marker":     add_round_marker,
        "clear":                clear,
        "toggle_lock":          toggle_lock,
        "save_png":             _save_png,
        "copy_to_clipboard":    _copy_to_clipboard,
        "stop_timer":           stop_timer,
        "_draw_timer":          draw_timer,
        "clear_round_markers":  clear_round_markers,
    }