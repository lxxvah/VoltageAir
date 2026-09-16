# stats.py
"""统计模块 —— 统计循环次数、电压值、气压峰值、充气时间、泄气时间、状态

as_row 顺序：轮次 / 电压 / 峰值 / 充气时间 / 泄气时间 / 状态
"""
from dataclasses import dataclass
from typing import List, Optional

from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal

from parser import ParsedLine


@dataclass
class RoundRecord:
    no:            int
    status:        Optional[int]   = None
    voltage:       Optional[float] = None
    peak_pressure: Optional[float] = None

    x_start:       Optional[float] = None
    x_end:         Optional[float] = None

    x_inflate_start: Optional[float] = None
    x_inflate_stop:  Optional[float] = None

    x_deflate_start: Optional[float] = None
    x_deflate_stop:  Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.status == 1

    @property
    def span(self) -> Optional[float]:
        if self.x_start is None or self.x_end is None:
            return None
        return self.x_end - self.x_start

    @property
    def inflate_time(self) -> Optional[float]:
        """充气时间 = stop inflate 时刻 − inflate START 时刻"""
        if self.x_inflate_start is None or self.x_inflate_stop is None:
            return None
        return self.x_inflate_stop - self.x_inflate_start

    @property
    def deflate_time(self) -> Optional[float]:
        """泄气时间 = slow deflate END 时刻 − slow deflate START 时刻"""
        if self.x_deflate_start is None or self.x_deflate_stop is None:
            return None
        return self.x_deflate_stop - self.x_deflate_start

    def as_row(self) -> list:
        # ★ 状态挪到最后一列
        return [
            "R#%d" % self.no,
            "—" if self.voltage is None else "%.4f" % self.voltage,
            "—" if self.peak_pressure is None else "%.0f" % self.peak_pressure,
            "—" if self.inflate_time is None else "%.2f" % self.inflate_time,
            "—" if self.deflate_time is None else "%.2f" % self.deflate_time,
            "—" if self.status is None
                else ("OK" if self.ok else "FAIL(%d)" % self.status),
        ]


class StatsCollector(QtCore.QObject):

    roundAdded   = pyqtSignal(int)
    roundUpdated = pyqtSignal(int)
    changed      = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rounds: List[RoundRecord] = []
        self._current: Optional[RoundRecord] = None
        self._pending_voltage: Optional[float] = None

    def clear(self):
        self.rounds.clear()
        self._current = None
        self._pending_voltage = None
        self.changed.emit()

    def feed(self, p: ParsedLine, x: float = 0.0):
        if p is None:
            return

        if p.event == 'round_start':
            self._start_round(x)

        if p.pressure is not None:
            self._update_peak(p.pressure)

        if p.voltage is not None:
            self._set_voltage(p.voltage)

        if p.event == 'inflate_start':
            self._update_inflate_start(x)
        if p.event == 'peak':
            self._update_inflate_stop(x)

        if p.event == 'deflate_start':
            self._update_deflate_start(x)
        if p.event == 'deflate_end':
            self._update_deflate_stop(x)

        if p.event == 'round_done' and p.round:
            self._finish_round(x, p.round)

    # ------------------------------------------------------
    def _start_round(self, x):
        if self._current is not None and self._current.x_end is None:
            self._current.x_end = x
            self.roundUpdated.emit(len(self.rounds) - 1)

        next_no = max((r.no for r in self.rounds), default=0) + 1
        rec = RoundRecord(no=next_no, x_start=x)
        self.rounds.append(rec)
        self._current = rec
        self.roundAdded.emit(len(self.rounds) - 1)
        self.changed.emit()

    def _update_inflate_start(self, x):
        """★ 只认第一次，防止重复 START 行覆盖起点"""
        if self._current is None:
            return
        if self._current.x_inflate_start is not None:
            return
        self._current.x_inflate_start = x
        self.roundUpdated.emit(len(self.rounds) - 1)
        self.changed.emit()

    def _update_inflate_stop(self, x):
        if self._current is None:
            return
        self._current.x_inflate_stop = x
        self.roundUpdated.emit(len(self.rounds) - 1)
        self.changed.emit()

    def _update_deflate_start(self, x):
        """★ 只认第一次，防止重复 START 行覆盖起点"""
        if self._current is None:
            return
        if self._current.x_deflate_start is not None:
            return
        self._current.x_deflate_start = x
        self.roundUpdated.emit(len(self.rounds) - 1)
        self.changed.emit()

    def _update_deflate_stop(self, x):
        if self._current is None:
            return
        self._current.x_deflate_stop = x
        self.roundUpdated.emit(len(self.rounds) - 1)
        self.changed.emit()

    def _update_peak(self, pressure):
        if self._current is None:
            return
        if (self._current.peak_pressure is None
                or pressure > self._current.peak_pressure):
            self._current.peak_pressure = pressure
            self.roundUpdated.emit(len(self.rounds) - 1)
            self.changed.emit()

    def _set_voltage(self, v):
        if self.rounds:
            last = self.rounds[-1]
            last.voltage = v
            self.roundUpdated.emit(len(self.rounds) - 1)
            self.changed.emit()
        else:
            self._pending_voltage = v

    def _finish_round(self, x, rd):
        no     = rd['no']
        status = rd['status']

        target = None
        for r in self.rounds:
            if r.no == no:
                target = r
                break
        if target is None:
            target = RoundRecord(no=no)
            self.rounds.append(target)
            self.roundAdded.emit(len(self.rounds) - 1)

        target.status = status
        target.x_end  = x
        self._current = target

        if self._pending_voltage is not None and target.voltage is None:
            target.voltage = self._pending_voltage
            self._pending_voltage = None

        self.roundUpdated.emit(self.rounds.index(target))
        self.changed.emit()

    def summary(self) -> dict:
        ok    = sum(1 for r in self.rounds if r.ok)
        fail  = sum(1 for r in self.rounds
                    if r.status is not None and not r.ok)
        total = self.rounds[-1].no if self.rounds else 0
        return {'done': len(self.rounds), 'total': total,
                'ok': ok, 'fail': fail}