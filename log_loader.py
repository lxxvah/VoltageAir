# log_loader.py
"""日志加载模块 —— 把磁盘文件当作"虚拟串口"逐行喂给主窗口

对外接口与 SerialWorker 完全一致：
    lineReceived(str)   每读一行就 emit
    errorOccurred(str)  出错
    stateChanged(bool)  True=开始发送，False=发送结束
    progress(int, int)  已发送行数 / 总行数
    allLinesSent()      ★ 所有行已发送完（在 run() 末尾 emit，
                           保证排在所有 lineReceived 之后）
"""
import os
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal


class LogLoader(QtCore.QThread):

    lineReceived  = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)
    stateChanged  = pyqtSignal(bool)
    progress      = pyqtSignal(int, int)
    allLinesSent  = pyqtSignal()       # ★ 新增

    BATCH_SIZE     = 500
    BATCH_SLEEP_MS = 1

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self._running = False
        self._pause   = False

    def stop(self):
        self._running = False

    def pause(self):
        self._pause = True

    def resume(self):
        self._pause = False

    def run(self):
        try:
            with open(self.path, "rb") as f:
                raw = f.read()
        except Exception as e:
            self.errorOccurred.emit("读取文件失败: %s" % e)
            return

        text = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if text is None:
            self.errorOccurred.emit("无法解码文件")
            return

        lines = text.splitlines()
        total = len(lines)

        self._running = True
        self.stateChanged.emit(True)

        count = 0
        try:
            for raw_line in lines:
                if not self._running:
                    break

                while self._pause and self._running:
                    self.msleep(50)
                if not self._running:
                    break

                s = raw_line.strip("\r\n")
                if s:
                    self.lineReceived.emit(s)

                count += 1
                if count % self.BATCH_SIZE == 0:
                    self.progress.emit(count, total)
                    self.msleep(self.BATCH_SLEEP_MS)
        finally:
            self.progress.emit(count, total)
            # ★ 关键：在 run() 末尾 emit，保证排在所有 lineReceived 之后
            self.allLinesSent.emit()
            self.stateChanged.emit(False)