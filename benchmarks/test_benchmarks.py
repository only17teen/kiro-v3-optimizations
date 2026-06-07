"""Performance benchmarks for Kiro v3.0 components.

Run with: pytest benchmarks/ --benchmark-only -v
"""

import asyncio
import pytest
import time
from typing import List

from engine.actor import ActorSystem, RouteStrategy, Priority
from engine.actor.pool import MessagePool, ActorMessage, MessageType
from engine.gpu.semaphore import GPUSemaphore, GPUSemaphoreConfig
from engine.retry import RetryManager, RetryConfig, RetryStrategy
from engine.strategy.bandit import UCB1Bandit, BanditConfig
from engine.cache.precognition import PrecognitionCache, PrecognitionConfig
from engine.metrics import MetricsRegistry, Counter, Gauge, Histogram


class TestActorBenchmarks:
    """Benchmark Actor Model performance."""
    
    @pytest.mark.benchmark
    def test_actor_spawn_throughput(self, benchmark):
        """Benchmark actor spawn rate."""
        async def spawn_actors():
            system = ActorSystem(router_strategy=RouteStrategy.ROUND_ROBIN)
            await system.start()
            
            async def handler(msg):
                return msg
            
            # Spawn 100 actors
            for i in range(100):
                await system.spawn(f"actor-{i}", handler)
            
            await system.stop()
        
        benchmark(asyncio.run, spawn_actors())
    
    @pytest.mark.benchmark
    def test_message_routing_throughput(self, benchmark):
        """Benchmark message routing throughput."""
        async def route_messages():
            system = ActorSystem(router_strategy=RouteStrategy.HASH_RING)
            await system.start()
            
            messages = []
            async def handler(msg):
                messages.append(msg)
                return msg
            
            ref = await system.spawn("router-test", handler)
            
            # Send 1000 messages
            for i in range(1000):
                await ref.tell(f"msg-{i}")
            
            await asyncio.sleep(0.5)
            await system.stop()
            return len(messages)
        
        result = benchmark(asyncio.run, route_messages())
        assert result == 1000
    
    @pytest.mark.benchmark
    def test_priority_mailbox_sorting(self, benchmark):
        """Benchmark priority mailbox sorting."""
        from engine.actor.mailbox import Mailbox, MailboxConfig
        
        async def sort_messages():
            mailbox = Mailbox(MailboxConfig(max_size=10000))
            
            # Insert 1000 messages in random priority order
            import random
            priorities = [Priority.CRITICAL, Priority.HIGH, Priority.NORMAL, 
                         Priority.LOW, Priority.BACKGROUND]
            
            for i in range(1000):
                priority = random.choice(priorities)
                await mailbox.put(f"msg-{i}", priority=priority)
            
            # Retrieve all
            retrieved = []
            for _ in range(1000):
                msg = await mailbox.get(timeout=0.1)
                if msg:
                    retrieved.append(msg)
            
            return len(retrieved)
        
        result = benchmark(asyncio.run, sort_messages())
        assert result == 1000


class TestPoolBenchmarks:
    """Benchmark Message Pool performance."""
    
    @pytest.mark.benchmark
    def test_pool_borrow_return(self, benchmark):
        """Benchmark pool borrow/return cycle."""
        async def pool_cycle():
            pool = MessagePool(initial_size=10000, max_size=100000)
            await pool.initialize()
            
            # Borrow and return 10000 messages
            for _ in range(10000):
                msg = await pool.borrow()
                if msg:
                    await pool.return_message(msg)
            
            stats = pool.get_stats()
            return stats["total_borrowed"]
        
        result = benchmark(asyncio.run, pool_cycle())
        assert result == 10000
    
    @pytest.mark.benchmark
    def test_pool_growth_under_load(self, benchmark):
        """Benchmark pool growth under load."""
        async def pool_growth():
            pool = MessagePool(initial_size=100, max_size=10000)
            await pool.initialize()
            
            # Borrow more than initial
            messages = []
            for _ in range(5000):
                msg = await pool.borrow(timeout=1.0)
                if msg:
                    messages.append(msg)
            
            stats = pool.get_stats()
            return stats["total_created"]
        
        result = benchmark(asyncio.run, pool_growth())
        assert result > 100  # Should have grown


class TestGPUBenchmarks:
    """Benchmark GPU Semaphore performance."""
    
    @pytest.mark.benchmark
    def test_semaphore_acquire_release(self, benchmark):
        """Benchmark semaphore acquire/release cycle."""
        async def semaphore_cycle():
            sem = GPUSemaphore(GPUSemaphoreConfig(max_concurrent=100))
            
            # Acquire and release 1000 times
            for _ in range(1000):
                await sem.acquire()
                await sem.release()
            
            return sem._total_acquired
        
        result = benchmark(asyncio.run, semaphore_cycle())
        assert result == 1000
    
    @pytest.mark.benchmark
    def test_multi_gpu_load_balancing(self, benchmark):
        """Benchmark multi-GPU load balancing."""
        async def load_balance():
            multi = GPUSemaphore(device_count=4)
            
            # Acquire 100 times
            devices = []
            for _ in range(100):
                success, device = await multi.acquire_best()
                if success:
                    devices.append(device)
            
            # Release all
            for device in devices:
                await multi.release(device)
            
            return len(devices)
        
        result = benchmark(asyncio.run, load_balance())
        assert result == 100


class TestRetryBenchmarks:
    """Benchmark retry logic performance."""
    
    @pytest.mark.benchmark
    def test_retry_success_path(self, benchmark):
        """Benchmark retry success path (no retries needed)."""
        async def success_path():
            manager = RetryManager(RetryConfig(max_retries=3))
            
            async def always_succeed():
                return "success"
            
            # Execute 1000 times
            for _ in range(1000):
                await manager.execute(always_succeed)
            
            return manager._total_success
        
        result = benchmark(asyncio.run, success_path())
        assert result == 1000
    
    @pytest.mark.benchmark
    def test_retry_with_jitter(self, benchmark):
        """Benchmark retry with full jitter."""
        async def retry_with_jitter():
            manager = RetryManager(RetryConfig(
                max_retries=3,
                base_delay=0.001,
                strategy=RetryStrategy.FULL_JITTER
            ))
            
            attempts = 0
            async def succeed_on_third():
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise RuntimeError("fail")
                return "success"
            
            for _ in range(100):
                attempts = 0
                await manager.execute(succeed_on_third)
            
            return manager._total_success
        
        result = benchmark(asyncio.run, retry_with_jitter())
        assert result == 100


class TestBanditBenchmarks:
    """Benchmark UCB1 Bandit performance."""
    
    @pytest.mark.benchmark
    def test_bandit_selection(self, benchmark):
        """Benchmark bandit arm selection."""
        def bandit_select():
            bandit = UCB1Bandit(BanditConfig(warmup_pulls=5))
            
            for i in range(10):
                bandit.add_arm(f"arm-{i}")
            
            # Select 10000 times
            for _ in range(10000):
                arm = bandit.select_arm()
                bandit.update(arm, 1.0)
            
            return bandit._total_pulls
        
        result = benchmark(bandit_select)
        assert result == 10000
    
    @pytest.mark.benchmark
    def test_bandit_with_context(self, benchmark):
        """Benchmark contextual bandit selection."""
        from engine.strategy.bandit import ContextualBandit
        
        def contextual_select():
            bandit = ContextualBandit(BanditConfig(warmup_pulls=5))
            
            for i in range(10):
                bandit.add_arm(f"arm-{i}", context_features=["load", "latency"])
            
            # Select 10000 times with context
            for _ in range(10000):
                context = {"load": 0.5, "latency": 0.3}
                arm = bandit.select_arm_for_context(context)
                bandit.update(arm, 1.0)
            
            return bandit._total_pulls
        
        result = benchmark(contextual_select)
        assert result == 10000


class TestCacheBenchmarks:
    """Benchmark Precognition Cache performance."""
    
    @pytest.mark.benchmark
    def test_cache_hit_performance(self, benchmark):
        """Benchmark cache hit performance."""
        async def cache_hits():
            cache = PrecognitionCache(PrecognitionConfig(max_size=10000))
            
            # Pre-populate
            for i in range(1000):
                await cache.put(f"prompt-{i}", f"result-{i}")
            
            # Read 10000 times (10x reads per write)
            hits = 0
            for i in range(10000):
                result = await cache.get(f"prompt-{i % 1000}")
                if result:
                    hits += 1
            
            return hits
        
        result = benchmark(asyncio.run, cache_hits())
        assert result == 10000
    
    @pytest.mark.benchmark
    def test_cache_miss_performance(self, benchmark):
        """Benchmark cache miss performance."""
        async def cache_misses():
            cache = PrecognitionCache(PrecognitionConfig(max_size=10000))
            
            # 10000 misses
            misses = 0
            for i in range(10000):
                result = await cache.get(f"nonexistent-{i}")
                if result is None:
                    misses += 1
            
            return misses
        
        result = benchmark(asyncio.run, cache_misses())
        assert result == 10000


class TestMetricsBenchmarks:
    """Benchmark Prometheus metrics performance."""
    
    @pytest.mark.benchmark
    def test_counter_increment(self, benchmark):
        """Benchmark counter increment."""
        def counter_inc():
            registry = MetricsRegistry()
            counter = registry.counter("requests", "Total requests")
            
            for i in range(100000):
                counter.inc({"method": "GET", "endpoint": f"/api/{i % 10}"})
            
            return counter.get({"method": "GET", "endpoint": "/api/0"})
        
        result = benchmark(counter_inc)
        assert result == 10000  # 100000 / 10 endpoints
    
    @pytest.mark.benchmark
    def test_histogram_observe(self, benchmark):
        """Benchmark histogram observation."""
        def histogram_obs():
            registry = MetricsRegistry()
            hist = registry.histogram("latency", "Request latency")
            
            for _ in range(100000):
                hist.observe(0.05)
            
            return hist.get_percentile(0.95)
        
        result = benchmark(histogram_obs)
        assert result >= 0.05


class TestEndToEndBenchmarks:
    """End-to-end system benchmarks."""
    
    @pytest.mark.benchmark
    @pytest.mark.slow
    def test_full_inference_pipeline(self, benchmark):
        """Benchmark full inference pipeline."""
        async def inference_pipeline():
            system = ActorSystem()
            await system.start()
            
            cache = PrecognitionCache()
            gpu_sem = GPUSemaphore(GPUSemaphoreConfig(max_concurrent=10))
            
            async def handler(msg):
                prompt = msg.get("prompt")
                
                # Check cache
                cached = await cache.get(prompt)
                if cached:
                    return cached
                
                # Acquire GPU
                if await gpu_sem.acquire(timeout=1.0):
                    try:
                        # Simulate inference
                        await asyncio.sleep(0.001)
                        result = f"generated: {prompt}"
                        await cache.put(prompt, result)
                        return result
                    finally:
                        await gpu_sem.release()
                return None
            
            ref = await system.spawn("inference", handler)
            
            # Send 100 requests
            for i in range(100):
                await ref.tell({"prompt": f"test-{i % 20}"})  # 20 unique prompts
            
            await asyncio.sleep(0.5)
            
            stats = {
                "cache": cache.get_stats(),
                "gpu": gpu_sem.get_stats(),
            }
            
            await system.stop()
            return stats
        
        result = benchmark(asyncio.run, inference_pipeline())
        assert result["cache"]["hits"] > 0
        assert result["gpu"]["total_acquired"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "--benchmark-only", "-v"])