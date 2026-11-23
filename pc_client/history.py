from collections import deque
from csv import writer
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from tempfile import NamedTemporaryFile


@dataclass(frozen=True, slots=True)
class Point:
    time: float
    watts: float
    segment: int
    volts: float | None = None
    amps: float | None = None


@dataclass(frozen=True, slots=True)
class Average:
    watts: float | None
    seconds: float
    samples: int


class PowerHistory:
    retention = 2 * 60 * 60
    max_gap = 6.0

    def __init__(self):
        self.points = deque()
        self.clear()

    def pause(self):
        if self.active:
            self.segment += 1
        self.last_sample = None
        self.active = False

    def clear(self):
        self.points.clear()
        self.started_at = None
        self.segment = 0
        self.last_sample = None
        self.active = False
        self.reset_on_enable = False

    def window_end(self, now):
        if self.points and not self.active:
            return self.points[-1].time
        return now

    def record(self, state, now, streamed=True):
        measured = state.get("measured", {})
        enabled = state.get("applied", {}).get("enabled") if measured.get("fresh") else None
        if enabled is not True:
            if enabled is False:
                self.reset_on_enable = True
            self.pause()
            return
        if self.reset_on_enable:
            self.clear()
        # The first confirmed readback can supply the opening measurement.
        if not streamed and self.started_at is not None:
            return

        sample = measured.get("sampled_ms")
        power = measured.get("power_mW")
        if sample is None or power is None:
            self.pause()
            return
        age = ((state["uptime_ms"] - sample) & 0xFFFFFFFF) / 1000
        if age >= 1:
            self.pause()
            return
        if sample == self.last_sample:
            return
        timestamp = now - age
        if self.active and self.points and (
            timestamp - self.points[-1].time > self.max_gap
            or (self.last_sample is not None and (sample - self.last_sample) & 0x80000000)
        ):
            self.segment += 1
        if self.points:
            timestamp = max(timestamp, self.points[-1].time + 0.001)
        if self.started_at is None:
            self.started_at = timestamp
        self.active = True
        self.last_sample = sample
        millivolts = measured.get("voltage_mV")
        milliamps = measured.get("current_mA")
        self.points.append(Point(
            timestamp, power / 1000, self.segment,
            None if millivolts is None else millivolts / 1000,
            None if milliamps is None else milliamps / 1000,
        ))
        while self.points and timestamp - self.points[0].time > self.retention + self.max_gap:
            self.points.popleft()

    def window(self, start, end):
        visible = []
        for point in reversed(self.points):
            if point.time > end:
                continue
            if point.time < start:
                if visible:
                    visible.append(point)
                break
            visible.append(point)
        visible.reverse()
        return visible

    def sample_rate(self, end):
        if not self.active or len(self.points) < 2:
            return None
        newest = self.points[-1]
        oldest = newest
        count = 0
        for point in reversed(self.points):
            if point.time < end - 5 or point.segment != newest.segment:
                break
            oldest = point
            count += 1
        span = newest.time - oldest.time
        return (count - 1) / span if span >= 0.5 else None

    @staticmethod
    def average(points, start, end):
        integral = coverage = 0.0
        for left, right in pairwise(points):
            length = right.time - left.time
            if left.segment != right.segment or not 0 < length <= PowerHistory.max_gap:
                continue
            a, b = max(start, left.time), min(end, right.time)
            if a >= b:
                continue
            slope = (right.watts - left.watts) / length
            start_watts = left.watts + slope * (a - left.time)
            end_watts = left.watts + slope * (b - left.time)
            integral += (start_watts + end_watts) * (b - a) / 2
            coverage += b - a
        return Average(
            integral / coverage if coverage else None,
            coverage,
            sum(start <= p.time <= end for p in points),
        )


def save_csv(filename, points, wall_time_offset):
    path = Path(filename)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=path.parent,
                                suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            rows = writer(output)
            rows.writerow(("本地时间", "电压 (V)", "电流 (A)", "功率 (W)", "曲线段"))
            for point in points:
                timestamp = datetime.fromtimestamp(point.time + wall_time_offset)
                rows.writerow((timestamp.isoformat(sep=" ", timespec="milliseconds"),
                               point.volts, point.amps, point.watts, point.segment + 1))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def normalize_measurements(measured, info):
    if (info.get("sx_model_raw"), info.get("sx_version_raw")) != (4008, 100):
        return measured
    # The firmware reports IOUT and POWER using 10 mA and 100 mW per count.
    # This SX150C revision uses 1 mA and 10 mW per count for these readings.
    corrected = measured.copy()
    for field in ("current_mA", "power_mW"):
        value = measured.get(field)
        if value is not None:
            corrected[field] = value // 10
    return corrected
