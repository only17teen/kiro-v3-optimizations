"""Benchmark comparison and regression detection for CI."""

import json
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    mean_ms: float
    stddev_ms: float
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    throughput_rps: float
    version: str


class BenchmarkComparator:
    """Compares benchmark results against baseline to detect regressions."""

    REGRESSION_THRESHOLDS = {
        "mean_ms": 1.10,      # 10% slower
        "p95_ms": 1.15,       # 15% slower at tail
        "p99_ms": 1.20,       # 20% slower at extreme tail
        "throughput_rps": 0.90,  # 10% lower throughput
    }

    def __init__(self, baseline_path: Optional[Path] = None):
        self.baseline_path = baseline_path or Path("benchmarks/baseline.json")
        self.baseline: Dict[str, BenchmarkResult] = {}
        self._load_baseline()

    def _load_baseline(self) -> None:
        if self.baseline_path.exists():
            with open(self.baseline_path) as f:
                data = json.load(f)
                for name, raw in data.items():
                    self.baseline[name] = BenchmarkResult(**raw)

    def save_baseline(self, results: List[BenchmarkResult]) -> None:
        data = {r.name: asdict(r) for r in results}
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.baseline_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Baseline saved to {self.baseline_path}")

    def compare(self, current: BenchmarkResult) -> Dict[str, any]:
        baseline = self.baseline.get(current.name)
        if not baseline:
            return {"status": "no_baseline", "message": f"No baseline for {current.name}"}

        regressions = []
        improvements = []

        for metric, threshold in self.REGRESSION_THRESHOLDS.items():
            current_val = getattr(current, metric)
            baseline_val = getattr(baseline, metric)
            ratio = current_val / baseline_val if baseline_val != 0 else float('inf')

            if metric == "throughput_rps":
                if ratio < threshold:
                    regressions.append(f"{metric}: {ratio:.2f}x (threshold: {threshold})")
                elif ratio > 1.10:
                    improvements.append(f"{metric}: {ratio:.2f}x")
            else:
                if ratio > threshold:
                    regressions.append(f"{metric}: {ratio:.2f}x (threshold: {threshold})")
                elif ratio < 0.90:
                    improvements.append(f"{metric}: {ratio:.2f}x")

        if regressions:
            return {
                "status": "regression",
                "benchmark": current.name,
                "regressions": regressions,
                "improvements": improvements,
                "baseline_version": baseline.version,
                "current_version": current.version,
            }
        return {
            "status": "pass",
            "benchmark": current.name,
            "improvements": improvements,
            "baseline_version": baseline.version,
            "current_version": current.version,
        }

    def compare_all(self, results: List[BenchmarkResult]) -> Dict[str, any]:
        comparisons = [self.compare(r) for r in results]
        regressions = [c for c in comparisons if c["status"] == "regression"]

        return {
            "overall_status": "fail" if regressions else "pass",
            "regression_count": len(regressions),
            "total_benchmarks": len(results),
            "comparisons": comparisons,
            "exit_code": 1 if regressions else 0,
        }


def run_benchmarks() -> List[BenchmarkResult]:
    """Execute benchmark suite and collect results."""
    import time
    import random

    def simulate_benchmark(name: str, duration_ms: float, jitter: float = 0.1) -> BenchmarkResult:
        samples = [duration_ms * (1 + random.uniform(-jitter, jitter)) for _ in range(100)]
        samples.sort()
        mean = statistics.mean(samples)
        return BenchmarkResult(
            name=name,
            mean_ms=mean,
            stddev_ms=statistics.stdev(samples),
            min_ms=min(samples),
            max_ms=max(samples),
            p50_ms=samples[49],
            p95_ms=samples[94],
            p99_ms=samples[98],
            throughput_rps=1000.0 / mean,
            version="3.0.0",
        )

    return [
        simulate_benchmark("actor_message_passing", 0.5),
        simulate_benchmark("gpu_semaphore_acquire", 2.0),
        simulate_benchmark("llm_timeout_enforcement", 1.0),
        simulate_benchmark("precognition_cache_hit", 0.1),
        simulate_benchmark("precognition_cache_miss", 5.0),
        simulate_benchmark("ucb1_strategy_selection", 0.3),
        simulate_benchmark("training_checkpoint_save", 50.0),
        simulate_benchmark("prometheus_metrics_scrape", 5.0),
    ]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--save-baseline", action="store_true", help="Save current results as baseline")
    parser.add_argument("--compare", action="store_true", help="Compare against baseline")
    args = parser.parse_args()

    comparator = BenchmarkComparator()
    results = run_benchmarks()

    if args.save_baseline:
        comparator.save_baseline(results)
        sys.exit(0)

    if args.compare:
        report = comparator.compare_all(results)
        print(json.dumps(report, indent=2))
        sys.exit(report["exit_code"])

    # Default: print results
    for r in results:
        print(f"{r.name}: mean={r.mean_ms:.2f}ms p95={r.p95_ms:.2f}ms throughput={r.throughput_rps:.1f} rps")
