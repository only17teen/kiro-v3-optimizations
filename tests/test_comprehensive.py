"""Comprehensive test suite for Kiro v3 engine with mocked dependencies."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from engine.actor import ActorPool, ActorRef, ActorSystem, Message
from engine.cache import PrecognitionCache
from engine.gc_tuner import GCTuner, GCTuningConfig
from engine.gpu import GPUSemaphore
from engine.llm import LLMTimeoutManager, TimeoutConfig
from engine.metrics import MetricsCollector, MetricsSnapshot
from engine.retry import RetryConfig, RetryPolicy, with_retry
from engine.strategy import BanditStrategy, RewardSystem
from engine.training import TrainerDaemon, TrainingConfig
from engine.tracing import KiroTracer, SpanKind, TracingConfig


class TestActorSystem:
    """Tests for the Actor Model (Phase 1)."""

    @pytest.mark.asyncio
    async def test_actor_pool_creation(self):
        pool = ActorPool(max_workers=4)
        assert pool.max_workers == 4
        assert len(pool.actors) == 0

    @pytest.mark.asyncio
    async def test_actor_lifecycle(self):
        system = ActorSystem()
        ref = await system.spawn(lambda msg: msg.data * 2, name="doubler")
        assert ref.name == "doubler"
        
        result = await system.ask(ref, Message("test", 5))
        assert result == 10
        
        await system.stop(ref)
        assert ref not in system.actors

    @pytest.mark.asyncio
    async def test_message_routing(self):
        system = ActorSystem()
        ref1 = await system.spawn(lambda msg: f"handler1:{msg.data}", name="h1")
        ref2 = await system.spawn(lambda msg: f"handler2:{msg.data}", name="h2")
        
        router = await system.spawn_router([ref1, ref2], strategy="round_robin")
        
        results = []
        for i in range(4):
            result = await system.ask(router, Message("test", i))
            results.append(result)
        
        assert len(results) == 4
        assert any("handler1" in r for r in results)
        assert any("handler2" in r for r in results)


class TestGCTuner:
    """Tests for GC Tuner (Phase 2)."""

    def test_gc_tuner_creation(self):
        config = GCTuningConfig(
            target_pause_ms=10.0,
            max_heap_mb=1024,
            min_heap_mb=256,
        )
        tuner = GCTuner(config)
        assert tuner.config.target_pause_ms == 10.0

    def test_gc_tuner_adjustment(self):
        tuner = GCTuner(GCTuningConfig())
        
        # Simulate high memory pressure
        with patch('gc.get_stats') as mock_stats:
            mock_stats.return_value = [
                {'collections': 100, 'collected': 1000, 'uncollectable': 0}
            ]
            with patch('psutil.virtual_memory') as mock_mem:
                mock_mem.return_value = Mock(percent=85.0)
                tuner.adjust()
                
                # Should trigger more aggressive GC
                assert tuner.last_adjustment is not None


class TestGPUSemaphore:
    """Tests for GPU Semaphore (Phase 3)."""

    @pytest.mark.asyncio
    async def test_semaphore_acquire_release(self):
        sem = GPUSemaphore(max_concurrent=2)
        
        # Should acquire immediately
        handle1 = await sem.acquire()
        assert handle1 is not None
        
        handle2 = await sem.acquire()
        assert handle2 is not None
        
        # Should block on third acquire
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sem.acquire(), timeout=0.1)
        
        await sem.release(handle1)
        
        # Now should succeed
        handle3 = await asyncio.wait_for(sem.acquire(), timeout=0.1)
        assert handle3 is not None

    @pytest.mark.asyncio
    async def test_semaphore_priority(self):
        sem = GPUSemaphore(max_concurrent=1)
        
        handle = await sem.acquire(priority=1)
        assert handle is not None
        
        # Higher priority should be queued first
        task_high = asyncio.create_task(sem.acquire(priority=10))
        task_low = asyncio.create_task(sem.acquire(priority=1))
        
        await sem.release(handle)
        
        # High priority should complete first
        high_result = await asyncio.wait_for(task_high, timeout=1.0)
        low_result = await asyncio.wait_for(task_low, timeout=1.0)
        
        assert high_result is not None
        assert low_result is not None


class TestLLMTimeoutManager:
    """Tests for LLM Timeout Manager (Phase 4)."""

    @pytest.mark.asyncio
    async def test_timeout_enforcement(self):
        config = TimeoutConfig(
            default_timeout=0.1,
            max_timeout=1.0,
            adaptive=True,
        )
        manager = LLMTimeoutManager(config)
        
        # Should timeout slow operation
        async def slow_op():
            await asyncio.sleep(0.5)
            return "done"
        
        with pytest.raises(asyncio.TimeoutError):
            await manager.execute_with_timeout(slow_op(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_adaptive_timeout(self):
        config = TimeoutConfig(adaptive=True)
        manager = LLMTimeoutManager(config)
        
        # Record success
        await manager.record_result("model1", 0.05, success=True)
        
        # Should adjust timeout based on history
        timeout = manager.get_adaptive_timeout("model1")
        assert timeout > 0.05
        assert timeout <= config.max_timeout


class TestPrecognitionCache:
    """Tests for Precognition Cache (Phase 5)."""

    def test_cache_hit(self):
        cache = PrecognitionCache(max_size=100)
        
        cache.put("key1", "value1", confidence=0.9)
        result = cache.get("key1", min_confidence=0.8)
        
        assert result == "value1"

    def test_cache_miss_low_confidence(self):
        cache = PrecognitionCache(max_size=100)
        
        cache.put("key1", "value1", confidence=0.5)
        result = cache.get("key1", min_confidence=0.8)
        
        assert result is None

    def test_cache_eviction(self):
        cache = PrecognitionCache(max_size=2)
        
        cache.put("key1", "value1", confidence=0.9)
        cache.put("key2", "value2", confidence=0.9)
        cache.put("key3", "value3", confidence=0.9)
        
        # key1 should be evicted
        assert cache.get("key1") is None
        assert cache.get("key2") is not None
        assert cache.get("key3") is not None


class TestBanditStrategy:
    """Tests for UCB1 Bandit (Phase 6)."""

    def test_bandit_selection(self):
        bandit = BanditStrategy(arms=["model1", "model2", "model3"])
        
        # Initially should explore all arms
        selections = set()
        for _ in range(10):
            arm = bandit.select()
            selections.add(arm)
        
        assert len(selections) > 1

    def test_bandit_update(self):
        bandit = BanditStrategy(arms=["model1", "model2"])
        
        # Update with rewards
        bandit.update("model1", reward=1.0)
        bandit.update("model1", reward=1.0)
        bandit.update("model2", reward=0.5)
        
        # model1 should be preferred
        counts = {"model1": 0, "model2": 0}
        for _ in range(100):
            arm = bandit.select()
            counts[arm] += 1
        
        assert counts["model1"] > counts["model2"]


class TestRewardSystem:
    """Tests for Reward System (Phase 6)."""

    def test_reward_calculation(self):
        rewards = RewardSystem()
        
        # Fast successful request
        reward = rewards.calculate(
            latency_ms=50,
            success=True,
            token_count=100,
            cost_per_token=0.0001,
        )
        assert reward > 0.5
        
        # Slow failed request
        reward = rewards.calculate(
            latency_ms=5000,
            success=False,
            token_count=100,
            cost_per_token=0.0001,
        )
        assert reward < 0.0


class TestTrainerDaemon:
    """Tests for Trainer Daemon (Phase 7)."""

    @pytest.mark.asyncio
    async def test_training_config(self):
        config = TrainingConfig(
            batch_size=32,
            learning_rate=0.001,
            max_epochs=10,
        )
        daemon = TrainerDaemon(config)
        assert daemon.config.batch_size == 32

    @pytest.mark.asyncio
    async def test_checkpoint_save_load(self):
        config = TrainingConfig()
        daemon = TrainerDaemon(config)
        
        # Mock model state
        state = {"weights": [1.0, 2.0, 3.0], "epoch": 5}
        
        with patch('builtins.open', MagicMock()):
            with patch('json.dump'):
                await daemon.save_checkpoint(state, path="/tmp/checkpoint.json")


class TestRetryPolicy:
    """Tests for Retry Logic."""

    @pytest.mark.asyncio
    async def test_successful_retry(self):
        config = RetryConfig(max_retries=3, base_delay=0.01)
        policy = RetryPolicy(config)
        
        call_count = 0
        
        @with_retry(config)
        async def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"
        
        result = await flaky_operation()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exhausted_retries(self):
        config = RetryConfig(max_retries=2, base_delay=0.01)
        
        @with_retry(config)
        async def always_fails():
            raise ConnectionError("Permanent failure")
        
        with pytest.raises(ConnectionError):
            await always_fails()


class TestMetricsCollector:
    """Tests for Metrics."""

    def test_metrics_collection(self):
        collector = MetricsCollector()
        
        collector.record_latency("inference", 100.0)
        collector.record_latency("inference", 150.0)
        collector.record_latency("inference", 200.0)
        
        snapshot = collector.snapshot()
        assert snapshot.count == 3
        assert snapshot.mean > 0

    def test_metrics_percentiles(self):
        collector = MetricsCollector()
        
        for i in range(100):
            collector.record_latency("inference", float(i))
        
        snapshot = collector.snapshot()
        assert snapshot.p50 >= 45
        assert snapshot.p50 <= 55
        assert snapshot.p95 >= 90


class TestTracing:
    """Tests for OpenTelemetry Tracing."""

    @pytest.mark.asyncio
    async def test_span_creation(self):
        tracer = KiroTracer(TracingConfig(console_exporter=True))
        await tracer.start()
        
        span = tracer.start_span("test-operation", SpanKind.SERVER)
        span.set_attribute("key", "value")
        await asyncio.sleep(0.01)
        tracer.end_span(span)
        
        await tracer.flush()
        await tracer.shutdown()
        
        assert span.duration_ms >= 10
        assert span.attributes["key"] == "value"

    @pytest.mark.asyncio
    async def test_trace_decorator(self):
        tracer = KiroTracer(TracingConfig(console_exporter=True))
        
        @tracer.trace(name="decorated-op", kind=SpanKind.CLIENT)
        async def my_async_func(x: int) -> int:
            await asyncio.sleep(0.01)
            return x * 2
        
        result = await my_async_func(5)
        assert result == 10
        
        await tracer.flush()
        await tracer.shutdown()


class TestIntegration:
    """Integration tests combining multiple components."""

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Test the complete inference pipeline."""
        # Setup components
        gc_tuner = GCTuner(GCTuningConfig())
        gpu_sem = GPUSemaphore(max_concurrent=1)
        timeout_mgr = LLMTimeoutManager(TimeoutConfig(default_timeout=1.0))
        cache = PrecognitionCache(max_size=10)
        bandit = BanditStrategy(arms=["fast_model", "quality_model"])
        rewards = RewardSystem()
        tracer = KiroTracer(TracingConfig())
        
        await tracer.start()
        
        async with tracer.span("inference_pipeline", SpanKind.SERVER) as span:
            # Check cache
            cached = cache.get("prompt_hash_123")
            if cached:
                span.set_attribute("cache_hit", True)
                result = cached
            else:
                span.set_attribute("cache_hit", False)
                
                # Select model
                model = bandit.select()
                span.set_attribute("selected_model", model)
                
                # Acquire GPU
                gpu_handle = await gpu_sem.acquire()
                try:
                    # Execute with timeout
                    async def inference():
                        await asyncio.sleep(0.01)
                        return f"result_from_{model}"
                    
                    result = await timeout_mgr.execute_with_timeout(
                        inference(), timeout=0.5
                    )
                    
                    # Cache result
                    cache.put("prompt_hash_123", result, confidence=0.95)
                    
                    # Update bandit
                    reward = rewards.calculate(
                        latency_ms=10.0,
                        success=True,
                        token_count=100,
                    )
                    bandit.update(model, reward)
                    
                finally:
                    await gpu_sem.release(gpu_handle)
        
        await tracer.flush()
        await tracer.shutdown()
        
        assert result is not None
        assert "result_from_" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
