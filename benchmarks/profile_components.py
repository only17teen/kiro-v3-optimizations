#!/usr/bin/env python3
"""Profiling script for Kiro v3.0 components.

Usage:
    python profile_components.py --component actor --duration 30
    python profile_components.py --component cache --duration 60
    python profile_components.py --component all --duration 120
"""

import argparse
import asyncio
import cProfile
import pstats
import sys
import time
from io import StringIO
from typing import Optional

from engine.actor import ActorSystem, RouteStrategy, Priority
from engine.actor.pool import MessagePool
from engine.gpu.semaphore import GPUSemaphore
from engine.cache.precognition import PrecognitionCache
from engine.metrics import MetricsRegistry
from engine.strategy.bandit import UCB1Bandit


def profile_actor_model(duration: int = 30) -> str:
    """Profile Actor Model performance."""
    profiler = cProfile.Profile()
    
    async def run():
        system = ActorSystem(router_strategy=RouteStrategy.HASH_RING)
        await system.start()
        
        async def handler(msg):
            return msg
        
        ref = await system.spawn("profile-actor", handler)
        
        start = time.time()
        count = 0
        while time.time() - start < duration:
            await ref.tell(f"msg-{count}")
            count += 1
            if count % 1000 == 0:
                await asyncio.sleep(0)  # Yield
        
        await system.stop()
        return count
    
    profiler.enable()
    total_messages = asyncio.run(run())
    profiler.disable()
    
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    
    return f"""Actor Model Profile ({duration}s)
{'=' * 50}
Total messages: {total_messages}
Throughput: {total_messages / duration:.0f} msg/s

Top 20 functions by cumulative time:
{s.getvalue()}
"""


def profile_cache(duration: int = 30) -> str:
    """Profile Precognition Cache performance."""
    profiler = cProfile.Profile()
    
    async def run():
        cache = PrecognitionCache()
        
        # Pre-populate
        for i in range(10000):
            await cache.put(f"prompt-{i}", f"result-{i}")
        
        start = time.time()
        hits = 0
        misses = 0
        count = 0
        
        while time.time() - start < duration:
            result = await cache.get(f"prompt-{count % 10000}")
            if result:
                hits += 1
            else:
                misses += 1
            count += 1
            if count % 1000 == 0:
                await asyncio.sleep(0)
        
        return hits, misses, count
    
    profiler.enable()
    hits, misses, total = asyncio.run(run())
    profiler.disable()
    
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    
    return f"""Cache Profile ({duration}s)
{'=' * 50}
Total lookups: {total}
Hits: {hits} ({hits/total*100:.1f}%)
Misses: {misses} ({misses/total*100:.1f}%)
Throughput: {total / duration:.0f} lookups/s

Top 20 functions by cumulative time:
{s.getvalue()}
"""


def profile_metrics(duration: int = 30) -> str:
    """Profile Metrics performance."""
    profiler = cProfile.Profile()
    
    def run():
        registry = MetricsRegistry()
        counter = registry.counter("requests", "Total requests")
        hist = registry.histogram("latency", "Request latency")
        
        start = time.time()
        count = 0
        
        while time.time() - start < duration:
            counter.inc({"method": "GET", "endpoint": f"/api/{count % 100}"})
            hist.observe(0.05)
            count += 1
            if count % 10000 == 0:
                time.sleep(0)
        
        return count
    
    profiler.enable()
    total = run()
    profiler.disable()
    
    s = StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
    ps.print_stats(20)
    
    return f"""Metrics Profile ({duration}s)
{'=' * 50}
Total operations: {total}
Throughput: {total / duration:.0f} ops/s

Top 20 functions by cumulative time:
{s.getvalue()}
"""


def profile_all(duration: int = 60) -> str:
    """Profile all components."""
    results = []
    
    for component, func in [
        ("actor", profile_actor_model),
        ("cache", profile_cache),
        ("metrics", profile_metrics),
    ]:
        print(f"Profiling {component}...", file=sys.stderr)
        results.append(func(duration // 3))
    
    return "\n\n".join(results)


def main():
    parser = argparse.ArgumentParser(description="Profile Kiro v3.0 components")
    parser.add_argument(
        "--component",
        choices=["actor", "cache", "metrics", "all"],
        default="all",
        help="Component to profile"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=30,
        help="Profiling duration in seconds"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file (default: stdout)"
    )
    
    args = parser.parse_args()
    
    profilers = {
        "actor": profile_actor_model,
        "cache": profile_cache,
        "metrics": profile_metrics,
        "all": profile_all,
    }
    
    result = profilers[args.component](args.duration)
    
    if args.output:
        with open(args.output, "w") as f:
            f.write(result)
        print(f"Profile written to {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()