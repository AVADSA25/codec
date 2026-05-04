"""CODEC metrics — lightweight Prometheus-compatible endpoint."""
import time
import threading

class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters = {}
        self._histograms = {}
        self._start_time = time.monotonic()

    def inc(self, name: str, labels: dict = None, value: int = 1):
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe(self, name: str, value: float, labels: dict = None):
        key = self._key(name, labels)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = {"count": 0, "sum": 0.0}
            self._histograms[key]["count"] += 1
            self._histograms[key]["sum"] += value

    def _key(self, name, labels):
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def render(self) -> str:
        lines = []
        lines.append("# HELP codec_uptime_seconds Time since process start")
        lines.append("# TYPE codec_uptime_seconds gauge")
        lines.append(f"codec_uptime_seconds {time.monotonic() - self._start_time:.1f}")
        with self._lock:
            for key, val in sorted(self._counters.items()):
                lines.append(f"{key} {val}")
            for key, data in sorted(self._histograms.items()):
                lines.append(f"{key}_count {data['count']}")
                lines.append(f"{key}_sum {data['sum']:.3f}")
        lines.append("")
        return "\n".join(lines)

metrics = Metrics()
