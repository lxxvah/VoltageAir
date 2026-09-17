# main.py
r"""启动入口

关键设计：
  · 开启 Qt 高 DPI 支持（必须在 QApplication 之前）
  · 每次启动用 icon_gen 生成 PNG 图标，路径通过环境变量传给 MainWindow
  · 延迟导入 MainWindow，确保 QApplication 先建好
  · 未捕获异常同时写 stderr 和 %APPDATA%\VoltageAir\crash.log
"""
import os
import sys
import traceback
from datetime import datetime

from PyQt5 import QtCore, QtWidgets


def _app_dir() -> str:
    """脚本运行 → main.py 所在目录；打包运行 → exe 所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _appdata_dir() -> str:
    """用户可写目录：优先 AppData，回退到 exe 目录"""
    try:
        base = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.AppDataLocation)
        if base:
            os.makedirs(base, exist_ok=True)
            return base
    except Exception:
        pass
    return _app_dir()


def _excepthook(t, v, tb):
    # 1) 打到 stderr（控制台跑时能看到）
    traceback.print_exception(t, v, tb)

    # 2) 追加到 crash.log（打包成 --windowed 后也能留证据）
    try:
        path = os.path.join(_appdata_dir(), "crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 60 + "\n")
            f.write("[%s]\n"
                    % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            traceback.print_exception(t, v, tb, file=f)
    except Exception:
        pass


def _icon_dir() -> str:
    """图标写入目录（和 crash.log 同目录，都是 AppData）"""
    return _appdata_dir()


def main():
    sys.excepthook = _excepthook

    # 高 DPI 支持 —— 必须在 QApplication 创建之前设置
    try:
        QtWidgets.QApplication.setAttribute(
            QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtWidgets.QApplication.setAttribute(
            QtCore.Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    # 每次启动生成图标（PNG）
    png_path = None
    try:
        from icon_gen import generate_icon
        png_path = os.path.join(_icon_dir(), "serial_monitor_icon.png")
        generate_icon(png_path, size=64)
        os.environ["SERIAL_SCOPE_ICON"] = png_path
    except Exception as e:
        print("[icon] 生成失败:", e)
        png_path = None

    # 延迟导入，确保 QApplication 先建好
    from app import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()