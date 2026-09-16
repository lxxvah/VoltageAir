# main.py
"""启动入口

关键设计：
  · 开启 Qt 高 DPI 支持（必须在 QApplication 之前）
  · 每次启动用 icon_gen 生成 PNG 图标，路径通过环境变量传给 MainWindow
  · 延迟导入 MainWindow，确保 QApplication 先建好
"""
import os
import sys
import traceback

from PyQt5 import QtCore, QtWidgets


def _excepthook(t, v, tb):
    traceback.print_exception(t, v, tb)


def _app_dir() -> str:
    """脚本运行 → main.py 所在目录；打包运行 → exe 所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _icon_dir() -> str:
    """图标写入目录。

    打包后 exe 可能位于受保护目录（如 Program Files），
    优先写用户可写的 AppData / XDG 目录；失败再退回 exe 目录。
    """
    try:
        base = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.AppDataLocation)
        if base:
            os.makedirs(base, exist_ok=True)
            return base
    except Exception:
        pass
    return _app_dir()


def main():
    sys.excepthook = _excepthook

    # ★ 高 DPI 支持 —— 必须在 QApplication 创建之前设置
    #   AA_EnableHighDpiScaling：系统 150% 缩放时按逻辑像素渲染
    #   AA_UseHighDpiPixmaps：   QPixmap 加载时使用高分辨率版本
    try:
        QtWidgets.QApplication.setAttribute(
            QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtWidgets.QApplication.setAttribute(
            QtCore.Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    # ★ 每次启动生成图标（PNG）；路径通过环境变量传给 MainWindow
    png_path = None
    try:
        from icon_gen import generate_icon
        png_path = os.path.join(_icon_dir(), "serial_monitor_icon.png")
        generate_icon(png_path, size=64)
        os.environ["SERIAL_SCOPE_ICON"] = png_path
    except Exception as e:
        # 图标生成失败不影响主程序
        print("[icon] 生成失败:", e)
        png_path = None

    # 延迟导入，确保 QApplication 先建好
    from app import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()