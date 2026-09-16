# plot_panel.py
"""双 Y 轴绘图面板 —— matplotlib + 定时器节流 + 按可视范围降采样 + 完整交互

关键设计：
  · set_pressure / set_voltage 只存引用，不触发绘制
  · 30ms 定时器驱动绘制（锁 33fps）
  · 全局点数 > 5 万时按可视范围降采样到 5000 点
  · QTimer 挂 parent，防止 create_plot_panel 返回后被 GC
  · ★ set_ui_scale(scale)：以"未加缩放前"的原始布局参数为基准，
      所有尺寸按 scale 等比缩放（幂等，无累积误差）
      · X 轴 label 底部留白保底 0.14，避免缩放较小时压到刻度线
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
class _PanelSignals(QtCore.QObject):
    pauseToggle = QtCore.pyqtSignal()


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

    # ---------------- 布局基准（scale=1.0 时的值） ----------------
    BASE_CARD_MARGINS = (12, 12, 12, 12)   # vbox
    BASE_CARD_SPACING = 8                  # vbox
    BASE_HDR_SPACING  = 8                  # hdr
    BASE_DOT_SIZE     = 8                  # 色点边长
    BASE_DOT_RADIUS   = 4                  # 色点圆角
    BASE_HDR_SPACER   = 20                 # 气压/电压之间的间隔
    BASE_CMB_VIEW_W   = 120                # VIEW 下拉宽度

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

    # 可缩放 spacer（基准 20）
    spacer_pv = QtWidgets.QWidget()
    spacer_pv.setFixedWidth(BASE_HDR_SPACER)
    hdr.addWidget(spacer_pv)

    hdr.addWidget(dot_v)
    hdr.addWidget(_title("电压"))
    val_v = _value("— V")
    hdr.addWidget(val_v)

    hdr.addStretch(1)

    hdr.addWidget(_field("VIEW"))
    cmb_view = QtWidgets.QComboBox()
    for text, data in [("全局", "all"), ("最近 200", "200"),
                       ("最近 500", "500"), ("最近 1000", "1000"),
                       ("最近 2000", "2000")]:
        cmb_view.addItem(text, data)
    cmb_view.setFixedWidth(BASE_CMB_VIEW_W)
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
                       solid_capstyle='round')
    line_v, = ax2.plot([], [], color=COLOR_VOLT, linewidth=1.4,
                       solid_capstyle='round')

    vline = ax1.axvline(0, color='#888888', lw=0.7, alpha=0.7,
                        visible=False, zorder=10)
    hline_p = ax1.axhline(0, color=COLOR_PRESS, lw=0.7, alpha=0.7,
                          visible=False, zorder=10)
    hline_v = ax2.axhline(0, color=COLOR_VOLT, lw=0.7, alpha=0.7,
                          visible=False, zorder=10)

    cursor_text = ax1.text(
        0.99, 0.98, "",
        transform=ax1.transAxes,
        ha='right', va='top',
        fontsize=8, color=T.ink,
        bbox=dict(boxstyle='round,pad=0.35',
                  facecolor='white',
                  edgecolor='#cccccc',
                  alpha=0.92),
        visible=False, zorder=11)

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

        if state['xs_p'] is not None and len(state['xs_p']):
            xs = state['xs_p']; ys = state['ys_p']
            if len(xs) > VIEW_MASK_THRESHOLD:
                mask = (xs >= x0) & (xs <= x1)
                xs_v = xs[mask]; ys_v = ys[mask]
                if len(xs_v) > DOWNSAMPLE_TARGET:
                    xs_v, ys_v = _peak_downsample(xs_v, ys_v,
                                                  DOWNSAMPLE_TARGET)
                line_p.set_data(xs_v, ys_v)
            else:
                line_p.set_data(xs, ys)

        if state['xs_v'] is not None and len(state['xs_v']):
            xs = state['xs_v']; ys = state['ys_v']
            if len(xs) > VIEW_MASK_THRESHOLD:
                mask = (xs >= x0) & (xs <= x1)
                xs_v = xs[mask]; ys_v = ys[mask]
                if len(xs_v) > DOWNSAMPLE_TARGET:
                    xs_v, ys_v = _peak_downsample(xs_v, ys_v,
                                                  DOWNSAMPLE_TARGET)
                line_v.set_data(xs_v, ys_v)
            else:
                line_v.set_data(xs, ys)

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
        state['need_redraw'] = True

    def set_voltage(xs, ys):
        if not len(xs):
            return
        state['xs_v'] = xs
        state['ys_v'] = ys
        x_max = float(xs[-1])
        if x_max > state['x_max']:
            state['x_max'] = x_max
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

    def set_ui_scale(scale):
        """★ 按"未加缩放前"的原始布局参数为基准等比缩放。

        幂等：反复调用相同 scale 结果一致（无累积误差）。
        """
        try:
            scale = float(scale)
        except Exception:
            return
        if scale <= 0:
            return
        state['ui_scale'] = scale

        def S(v):
            return max(1, int(round(v * scale)))

        # ---------- matplotlib 字体 ----------
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
        # ★ X 轴 label 保底留白 0.14，避免缩放较小时压到刻度线
        bottom = max(0.14, min(0.28, 0.14 * scale))
        top    = 0.975
        fig.subplots_adjust(left=left, right=right, top=top, bottom=bottom)

        # ---------- Qt 布局尺寸（以基准值重算，不依赖当前值） ----------
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
        cmb_view.setFixedWidth(S(BASE_CMB_VIEW_W))

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

    def clear():
        line_p.set_data([], [])
        line_v.set_data([], [])
        state['xs_p'] = None; state['ys_p'] = None
        state['xs_v'] = None; state['ys_v'] = None
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
        canvas.draw_idle()

    def toggle_lock():
        btn_lock.setChecked(not btn_lock.isChecked())

    def stop_timer():
        draw_timer.stop()

    _refresh_x_ticks(0, 100)
    _update_follow_label()
    canvas.draw_idle()

    return {
        "widget":            card,
        "signals":           signals,
        "set_pressure":      set_pressure,
        "set_voltage":       set_voltage,
        "set_x_range":       set_x_range,
        "set_x_label":       set_x_label,
        "set_ui_scale":      set_ui_scale,
        "apply_view_force":  apply_view_force,
        "set_values":        set_values,
        "add_round_marker":  add_round_marker,
        "clear":             clear,
        "toggle_lock":       toggle_lock,
        "save_png":          _save_png,
        "copy_to_clipboard": _copy_to_clipboard,
        "stop_timer":        stop_timer,
        "_draw_timer":       draw_timer,
    }