# log_writer.py
"""自动保存原始日志（只保存 .log，不再生成 data_*.csv）

说明：
  · 保存日志只写 raw_*.log，与串口原始数据一一对应
  · 结构化 CSV 由 app.py 的"保存统计"按钮手动触发（round_stats.csv）
  · 文件名带毫秒防重名：同一秒内多次连接不会互相覆盖
"""
import os
from datetime import datetime


class LogWriter:

    def __init__(self, base_dir="logs"):
        self.base_dir = base_dir
        self.raw_fp = None
        self.raw_path = None

    def is_open(self):
        return self.raw_fp is not None

    def open(self):
        """打开日志文件，返回 raw_path"""
        now = datetime.now()
        folder = os.path.join(self.base_dir, now.strftime("%Y-%m-%d"))
        os.makedirs(folder, exist_ok=True)
        stamp = now.strftime("%H%M%S")

        # 防重名：同一秒内多次连接时追加 _1、_2 ...
        base_raw = os.path.join(folder, "raw_%s" % stamp)
        raw_path = base_raw + ".log"
        n = 1
        while os.path.exists(raw_path):
            raw_path = "%s_%d.log" % (base_raw, n)
            n += 1

        self.raw_path = raw_path

        self.raw_fp = open(self.raw_path, "w", encoding="utf-8",
                           errors="replace", buffering=1)
        self.raw_fp.write("# session start %s\n"
                          % now.strftime("%Y-%m-%d %H:%M:%S"))

        return self.raw_path

    def close(self):
        if self.raw_fp is not None:
            try:
                self.raw_fp.flush()
                self.raw_fp.close()
            except Exception:
                pass
        self.raw_fp = None

    def write_raw(self, line):
        if self.raw_fp is None:
            return
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            self.raw_fp.write("[%s] %s\n" % (ts, line))
        except Exception:
            pass