"""Prometheus metrics with low-cardinality labels."""

import time
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class MetricType(Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"


@dataclass
class MetricConfig:
    """Metric configuration with cardinality limits."""
    max_label_values: int = 100  # Prevent cardinality explosion
    max_label_length: int = 128
    bucket_count: int = 10
    bucket_min: float = 0.001
    bucket_max: float = 10.0


class LowCardinalityMetric:
    """Base metric with cardinality protection."""
    
    def __init__(self, name: str, metric_type: MetricType,
                 description: str, config: Optional[MetricConfig] = None):
        self.name = name
        self.metric_type = metric_type
        self.description = description
        self.config = config or MetricConfig()
        self._values: Dict[str, Any] = {}
        self._label_values: Dict[str, set] = {}
        
    def _sanitize_label(self, label: str, value: str) -> str:
        """Sanitize label value to prevent cardinality explosion."""
        # Truncate long values
        value = str(value)[:self.config.max_label_length]
        
        # Track label values
        if label not in self._label_values:
            self._label_values[label] = set()
        
        # If too many values, bucket into 'other'
        if len(self._label_values[label]) >= self.config.max_label_values:
            if value not in self._label_values[label]:
                value = "other"
        else:
            self._label_values[label].add(value)
        
        return value
    
    def _make_key(self, labels: Dict[str, str]) -> str:
        """Create key from labels with sanitization."""
        sanitized = {
            k: self._sanitize_label(k, v)
            for k, v in sorted(labels.items())
        }
        return ",".join(f"{k}={v}" for k, v in sanitized.items())

    def _parse_key(self, key: str) -> Dict[str, str]:
        """Parse label key back into dictionary."""
        if not key:
            return {}
        result = {}
        for part in key.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                result[k] = v
        return result


class Counter(LowCardinalityMetric):
    """Monotonically increasing counter."""
    
    def __init__(self, name: str, description: str, 
                 config: Optional[MetricConfig] = None):
        super().__init__(name, MetricType.COUNTER, description, config)
        
    def inc(self, labels: Optional[Dict[str, str]] = None, 
            value: float = 1.0) -> None:
        key = self._make_key(labels or {})
        self._values[key] = self._values.get(key, 0.0) + value
    
    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = self._make_key(labels or {})
        return self._values.get(key, 0.0)
    
    def collect(self) -> List[Dict[str, Any]]:
        return [
            {"labels": self._parse_key(k), "value": v}
            for k, v in self._values.items()
        ]


class Gauge(LowCardinalityMetric):
    """Gauge that can go up and down."""
    
    def __init__(self, name: str, description: str,
                 config: Optional[MetricConfig] = None):
        super().__init__(name, MetricType.GAUGE, description, config)
        
    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(labels or {})
        self._values[key] = value
    
    def inc(self, value: float = 1.0, 
            labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(labels or {})
        self._values[key] = self._values.get(key, 0.0) + value
    
    def dec(self, value: float = 1.0,
            labels: Optional[Dict[str, str]] = None) -> None:
        self.inc(-value, labels)
    
    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = self._make_key(labels or {})
        return self._values.get(key, 0.0)

    def collect(self) -> List[Dict[str, Any]]:
        return [
            {"labels": self._parse_key(k), "value": v}
            for k, v in self._values.items()
        ]


class Histogram(LowCardinalityMetric):
    """Histogram with exponential buckets."""
    
    def __init__(self, name: str, description: str,
                 config: Optional[MetricConfig] = None):
        super().__init__(name, MetricType.HISTOGRAM, description, config)
        self._buckets = self._create_buckets()
        self._sums: Dict[str, float] = {}
        self._counts: Dict[str, int] = {}
        
    def _create_buckets(self) -> List[float]:
        """Create exponential buckets."""
        buckets = []
        value = self.config.bucket_min
        for _ in range(self.config.bucket_count):
            buckets.append(value)
            value *= (self.config.bucket_max / self.config.bucket_min) ** (1.0 / self.config.bucket_count)
        buckets.append(float('inf'))
        return buckets
    
    def observe(self, value: float, 
                labels: Optional[Dict[str, str]] = None) -> None:
        key = self._make_key(labels or {})
        
        if key not in self._values:
            self._values[key] = [0] * len(self._buckets)
            self._sums[key] = 0.0
            self._counts[key] = 0
        
        # Prometheus histograms are CUMULATIVE — increment all matching buckets
        for i, bucket in enumerate(self._buckets):
            if value <= bucket:
                self._values[key][i] += 1
        
        self._sums[key] += value
        self._counts[key] += 1
    
    def get_percentile(self, percentile: float,
                       labels: Optional[Dict[str, str]] = None) -> float:
        key = self._make_key(labels or {})
        if key not in self._counts or self._counts[key] == 0:
            return 0.0
        
        target = int(self._counts[key] * percentile)
        cumulative = 0
        for i, count in enumerate(self._values[key]):
            cumulative += count
            if cumulative >= target:
                return self._buckets[i]
        return self._buckets[-1]


class MetricsRegistry:
    """Central registry for all metrics."""
    
    def __init__(self):
        self._metrics: Dict[str, LowCardinalityMetric] = {}
        self._prefix = "kiro_"
        
    def register(self, metric: LowCardinalityMetric) -> None:
        full_name = self._prefix + metric.name
        self._metrics[full_name] = metric
        logger.info(f"Registered metric: {full_name}")
    
    def counter(self, name: str, description: str,
                config: Optional[MetricConfig] = None) -> Counter:
        metric = Counter(name, description, config)
        self.register(metric)
        return metric
    
    def gauge(self, name: str, description: str,
              config: Optional[MetricConfig] = None) -> Gauge:
        metric = Gauge(name, description, config)
        self.register(metric)
        return metric
    
    def histogram(self, name: str, description: str,
                  config: Optional[MetricConfig] = None) -> Histogram:
        metric = Histogram(name, description, config)
        self.register(metric)
        return metric
    
    def collect(self) -> Dict[str, Any]:
        """Collect all metrics for Prometheus scraping."""
        result = {}
        for name, metric in self._metrics.items():
            if metric.metric_type == MetricType.COUNTER:
                result[name] = {
                    "type": "counter",
                    "help": metric.description,
                    "values": metric.collect()
                }
            elif metric.metric_type == MetricType.GAUGE:
                result[name] = {
                    "type": "gauge",
                    "help": metric.description,
                    "values": metric.collect()
                }
            elif metric.metric_type == MetricType.HISTOGRAM:
                result[name] = {
                    "type": "histogram",
                    "help": metric.description,
                    "buckets": metric._buckets,
                    "values": {
                        k: {"bucket_counts": v, "sum": metric._sums[k], "count": metric._counts[k]}
                        for k, v in metric._values.items()
                    }
                }
        return result
    
    def get_prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        for name, metric in self._metrics.items():
            lines.append(f"# HELP {name} {metric.description}")
            lines.append(f"# TYPE {name} {metric.metric_type.value}")
            
            if metric.metric_type in (MetricType.COUNTER, MetricType.GAUGE):
                for item in metric.collect() if hasattr(metric, 'collect') else []:
                    labels = ",".join(f'{k}="{v}"' for k, v in item.get("labels", {}).items())
                    if labels:
                        lines.append(f"{name}{{{labels}}} {item['value']}")
                    else:
                        lines.append(f"{name} {item['value']}")
            
            lines.append("")
        
        return "\n".join(lines)


# Global registry
_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _registry


__all__ = [
    "MetricsRegistry",
    "Counter",
    "Gauge", 
    "Histogram",
    "MetricConfig",
    "MetricType",
    "get_registry"
]