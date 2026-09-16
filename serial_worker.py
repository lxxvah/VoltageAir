# serial_worker.py
"""串口读取线程：后台阻塞读，逐行发信号"""
import serial
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal


class SerialWorker(QtCore.QThread):

    lineReceived  = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)
    stateChanged  = pyqtSignal(bool)

    def __init__(self, port, baudrate=921600, parent=None):
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=0.05)
        except Exception as e:
            self.errorOccurred.emit("打开串口失败: %s" % e)
            return

        self._running = True
        self.stateChanged.emit(True)

        buf = b""
        try:
            while self._running:
                try:
                    chunk = ser.read(max(1, ser.in_waiting))
                except Exception as e:
                    self.errorOccurred.emit("读取异常: %s" % e)
                    break
                if not chunk:
                    continue

                buf += chunk

                # ★ 缓冲上限从 16KB 提到 256KB，尾部保留 64KB。
                #   921600 波特率下不停打二进制时，原来的 16KB→4KB 截断
                #   会把中间未切出的行整段丢掉。
                if len(buf) > 262144:
                    buf = buf[-65536:]
                # 若长期没有 \n（对方没发文本），主动扔头部防 OOM
                elif len(buf) > 65536 and b"\n" not in buf[:32768]:
                    buf = buf[-32768:]

                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    text = line.decode("utf-8", errors="replace").strip("\r\n \t")
                    if text:
                        self.lineReceived.emit(text)
        finally:
            try:
                ser.close()
            except Exception:
                pass
            self.stateChanged.emit(False)