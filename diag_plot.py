# diag_full.py
import sys
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import QSize
import pyqtgraph as pg


class _PW(pg.GraphicsLayoutWidget):
    def minimumSizeHint(self):
        return QSize(100, 100)


class Panel(QtWidgets.QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(20, 16, 16, 12)
        v.setSpacing(10)

        # header
        h = QtWidgets.QHBoxLayout()
        h.addWidget(QtWidgets.QLabel("气压"))
        h.addStretch(1)
        h.addWidget(QtWidgets.QPushButton("锁定视图"))
        h.addWidget(QtWidgets.QPushButton("自适应"))
        h.addWidget(QtWidgets.QLabel("● 跟随中"))
        v.addLayout(h)

        self.plot = _PW()
        self.plot.setBackground('#ffffff')
        v.addWidget(self.plot, stretch=1)

        self.p1 = self.plot.addPlot(row=0, col=0)
        self.p1.showGrid(x=True, y=True, alpha=0.3)
        self.p1.showAxis('right')
        self.p1.setXRange(0, 50)
        self.p1.setYRange(0, 250)
        self.p1.plot([0, 10, 20, 30, 40, 50],
                     [0, 100, 200, 150, 50, 0], pen='r')


class Win(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1440, 900)
        c = QtWidgets.QWidget()
        self.setCentralWidget(c)
        root = QtWidgets.QVBoxLayout(c)
        root.setContentsMargins(0, 0, 0, 0)

        # 顶部工具栏
        top = QtWidgets.QFrame()
        top.setFixedHeight(50)
        top.setStyleSheet("background:#fafafa; border-bottom:1px solid #ddd;")
        root.addWidget(top)

        body = QtWidgets.QWidget()
        body.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                           QtWidgets.QSizePolicy.Expanding)
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(20, 18, 20, 18)
        bl.setSpacing(12)

        self.panel = Panel()
        bl.addWidget(self.panel, 1)

        bot = QtWidgets.QWidget()
        bot.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                          QtWidgets.QSizePolicy.Expanding)
        bh = QtWidgets.QHBoxLayout(bot)
        bh.setContentsMargins(0, 0, 0, 0)
        l = QtWidgets.QFrame(); l.setStyleSheet("background:#f8f8f8; border:1px solid #ddd;")
        r = QtWidgets.QFrame(); r.setStyleSheet("background:#f8f8f8; border:1px solid #ddd;")
        bh.addWidget(l, 1); bh.addWidget(r, 1)
        bl.addWidget(bot, 1)

        root.addWidget(body, 1)

        QtCore.QTimer.singleShot(2500, self.dump)

    def dump(self):
        print("=" * 72)
        print("Window     :", self.width(), "x", self.height())
        c = self.centralWidget()
        print("central    :", c.width(), "x", c.height())
        print("panel      :", self.panel.width(), "x", self.panel.height())
        plot = self.panel.plot
        print("glw        :", plot.width(), "x", plot.height())
        print("viewport   :", plot.viewport().width(), "x",
              plot.viewport().height())
        p1 = self.panel.p1
        g = p1.geometry()
        print("PlotItem   : x=%.0f y=%.0f w=%.0f h=%.0f"
              % (g.x(), g.y(), g.width(), g.height()))
        vb = p1.getViewBox()
        gv = vb.geometry()
        print("ViewBox    : x=%.0f y=%.0f w=%.0f h=%.0f"
              % (gv.x(), gv.y(), gv.width(), gv.height()))
        for name in ('left', 'bottom', 'right'):
            ga = p1.getAxis(name).geometry()
            print("Axis(%-6s): w=%.0f h=%.0f" % (name, ga.width(), ga.height()))
        print("=" * 72)
        QtWidgets.QApplication.quit()


app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")
w = Win()
w.show()
sys.exit(app.exec_())