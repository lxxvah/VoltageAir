# parser.py
"""解析模块 —— 输入一行文本，输出结构化数据（底层，随串口格式变）

新增：
  · cooldown 字段 —— 识别 [AutoTest] cool down 60s...
  · inflate_start / deflate_start —— 识别 [Test] inflate START 和
    [Test] slow deflate START，供 stats.py 计算充气/泄气时间
"""
import re
from dataclasses import dataclass
from typing import Optional


# ==========================================================
# 数据结构
# ==========================================================
@dataclass
class ParsedLine:
    raw:      str = ""
    ts:       Optional[float] = None   # [HH:MM:SS.mmm] 解析出的秒数（可能为 None）
    voltage:  Optional[float] = None   # V
    pressure: Optional[float] = None   # mmHg
    phase:    Optional[str]   = None   # 'inflating' / 'deflating'
    event:    Optional[str]   = None   # round_start / round_done / peak
                                        # / inflate_start / deflate_start
                                        # / deflate_end / cooldown
    round:    Optional[dict]  = None   # {'no':N, 'status':S, 'total':T}
    cooldown: Optional[float] = None   # 冷却秒数

    def has_data(self) -> bool:
        return (self.voltage is not None
                or self.pressure is not None
                or self.event is not None)


# ==========================================================
# 解析器
# ==========================================================
class DataParser:

    ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
    TS_RE   = re.compile(r'\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]')

    # [Test] inflating: 57 mmHg   /   [Test] deflating: 214 mmHg
    PRESS_RE = re.compile(
        r'\[Test\]\s*(inflating|deflating)\s*:\s*(-?\d+(?:\.\d+)?)\s*mmHg',
        re.IGNORECASE)

    # [Round DONE] bat=0.424 V level=0 | Date=...
    VOLT_RE = re.compile(
        r'\[Round\s+DONE\]\s*bat\s*=\s*(-?\d+(?:\.\d+)?)\s*V',
        re.IGNORECASE)

    # [Test] === Test #1 DONE, status=1, total=1 ===
    ROUND_DONE_RE = re.compile(
        r'Test\s+#(\d+)\s+DONE,\s*status=(\d+),\s*total=(\d+)',
        re.IGNORECASE)

    # ===== Auto BP Test START =====
    ROUND_START_RE = re.compile(
        r'={3,}\s*Auto\s+BP\s+Test\s+START', re.IGNORECASE)

    # ★ [Test] inflate START, target=220 mmHg
    INFLATE_START_RE = re.compile(
        r'\[Test\]\s*inflate\s+START\b', re.IGNORECASE)

    # ★ [Test] slow deflate START
    DEFLATE_START_RE = re.compile(
        r'\[Test\]\s*slow\s+deflate\s+START\b', re.IGNORECASE)

    # [Test] reached 220 mmHg, stop inflate
    PEAK_RE = re.compile(
        r'\[Test\]\s*reached\s+(\d+)\s+mmHg,\s*stop\s+inflate',
        re.IGNORECASE)

    # [Test] reached 40 mmHg, slow deflate END
    DEFL_END_RE = re.compile(
        r'\[Test\]\s*reached\s+(\d+)\s+mmHg,\s*slow\s+deflate\s+END',
        re.IGNORECASE)

    # ★ [AutoTest] cool down 60s...
    COOLDOWN_RE = re.compile(
        r'cool\s+down\s+(\d+)\s*s', re.IGNORECASE)

    # ------------------------------------------------------
    def __init__(self):
        self._day_offset = 0.0
        self._last_ts_sec = None

    def reset(self):
        self._day_offset = 0.0
        self._last_ts_sec = None

    # ------------------------------------------------------
    @classmethod
    def strip_ansi(cls, s: str) -> str:
        return cls.ANSI_RE.sub('', s)

    def _parse_ts(self, line: str) -> Optional[float]:
        """提取 [HH:MM:SS.mmm] → 绝对秒；跨天自动 +86400

        ★ 跨天判定阈值从 3600s 放宽到 43200s（12 小时）：
          设备休眠一小时再打一行日志时，不会被误判为跨天。
        """
        m = self.TS_RE.search(line)
        if not m:
            return None
        h, mi, s, ms = (int(x) for x in m.groups())
        sec = h * 3600 + mi * 60 + s + ms / 1000.0
        if (self._last_ts_sec is not None
                and sec < self._last_ts_sec - 43200):
            self._day_offset += 86400.0
        self._last_ts_sec = sec
        return sec + self._day_offset

    # ------------------------------------------------------
    def parse(self, line: str) -> Optional[ParsedLine]:
        if not line:
            return None

        clean = self.strip_ansi(line).strip()
        if not clean:
            return None

        p = ParsedLine(raw=clean, ts=self._parse_ts(line))

        # --- 压力 ---
        m = self.PRESS_RE.search(clean)
        if m:
            p.phase    = m.group(1).lower()
            p.pressure = float(m.group(2))

        # --- 电压 ---
        m = self.VOLT_RE.search(clean)
        if m:
            p.voltage = float(m.group(1))

        # --- 事件 ---
        m = self.ROUND_DONE_RE.search(clean)
        if m:
            p.round = {'no':     int(m.group(1)),
                       'status': int(m.group(2)),
                       'total':  int(m.group(3))}
            p.event = 'round_done'
        elif self.ROUND_START_RE.search(clean):
            p.event = 'round_start'
        elif self.PEAK_RE.search(clean):
            p.event = 'peak'
        elif self.DEFL_END_RE.search(clean):
            p.event = 'deflate_end'
        elif self.INFLATE_START_RE.search(clean):
            p.event = 'inflate_start'
        elif self.DEFLATE_START_RE.search(clean):
            p.event = 'deflate_start'
        else:
            m = self.COOLDOWN_RE.search(clean)
            if m:
                p.cooldown = float(m.group(1))
                p.event    = 'cooldown'

        return p if p.has_data() else None