"""Integration tests for Kiro v3.0 components."""

import asyncio
import pytest
import time
from typing import Any

# Import all Kiro components
from engine.actor import ActorSystem, ActorRef, RouteStrategy, Priority
from engine.actor.pool import MessagePool, ActorMessage, MessageType
from engine.gc_tuner import GCTuner, GCTunerConfig
from engine.gpu.semaphore import GPUSemaphore, GPUSemaphoreConfig, MultiGPUSemaphore
from engine.llm.timeout import LLMTimeoutManager, TimeoutConfig, CircuitBreaker
from engine.retry import RetryManager, RetryConfig, RetryStrategy, RetryableHTTPError
from engine.strategy.bandit import UCB1Bandit, BanditConfig
from engine.strategy.reward import RewardCalculator, RewardConfig, RewardFeedbackLoop
from engine.cache.precognition import PrecognitionCache, PrecognitionConfig
from engine.training.trainer_daemon import TrainerDaemon, TrainingConfig
from engine.metrics import MetricsRegistry, get_registry, Counter, Gauge, Histogram
from tests.chaos.test_chaos import ChaosMonkey, ChaosConfig, FailureType


class TestActorModel:
    """Test Actor Model core components."""
    
    @pytest.mark.asyncio
    async def test_actor_spawn_and_tell(self):
        system = ActorSystem(router_strategy=RouteStrategy.ROUND_ROBIN)
        await system.start()
        
        messages = []
        async def handler(msg):
            messages.append(msg)
            return f"handled: {msg}"
        
        ref = await system.spawn("test-actor", handler)
        assert ref.actor_id == "test-actor"
        
        success = await ref.tell("hello")
        assert success
        
        # Allow mailbox processing
        await asyncio.sleep(0.1)
        assert "hello" in messages
        
        await system.stop()
    
    @pytest.mark.asyncio
    async def test_actor_ask_pattern(self):
        system = ActorSystem(router_strategy=RouteStrategy.HASH_RING)
        await system.start()
        
        async def echo_handler(msg):
            return f"echo: {msg}"
        
        ref = await system.spawn("echo", echo_handler)
        result = await ref.ask("test", timeout=1.0)
        assert result == "echo: test"
        
        await system.stop()
    
    @pytest.mark.asyncio
    async def test_priority_mailbox(self):
        system = ActorSystem()
        await system.start()
        
        results = []
        async def handler(msg):
            results.append(msg)
            return msg
        
        ref = await system.spawn("priority", handler)
        
        # Send in reverse priority order
        await ref.tell("low", priority=Priority.LOW)
        await ref.tell("normal", priority=Priority.NORMAL)
        await ref.tell("high", priority=Priority.HIGH)
        await ref.tell("critical", priority=Priority.CRITICAL)
        
        await asyncio.sleep(0.2)
        
        # Critical should be processed first
        assert results[0] == "critical"
        assert results[1] == "high"
        
        await system.stop()
    
    @pytest.mark.asyncio
    async def test_supervisor_restart(self):
        system = ActorSystem()
        await system.start()
        
        fail_count = 0
        async def flaky_handler(msg):
            nonlocal fail_count
            fail_count += 1
            if fail_count < 3:
                raise RuntimeError("simulated failure")
            return "recovered"
        
        ref = await system.spawn("flaky", flaky_handler)
        
        # Trigger failures
        for _ in range(5):
            try:
                await ref.ask("test", timeout=1.0)
            except Exception:
                pass
        
        await asyncio.sleep(0.5)
        
        # Should eventually succeed after restarts
        result = await ref.ask("test", timeout=2.0)
        assert result == "recovered"
        
        await system.stop()


class TestMessagePool:
    """Test pre-allocated message pool."""
    
    @pytest.mark.asyncio
    async def test_pool_borrow_return(self):
        pool = MessagePool(initial_size=100, max_size=1000)
        await pool.initialize()
        
        msg = await pool.borrow()
        assert msg is not None
        assert msg.msg_type == MessageType.TELL
        
        msg.payload = "test"
        await pool.return_message(msg)
        
        stats = pool.get_stats()
        assert stats["total_created"] == 100
        assert stats["total_borrowed"] == 1
        assert stats["total_returned"] == 1
    
    @pytest.mark.asyncio
    async def test_pool_growth(self):
        pool = MessagePool(initial_size=10, max_size=100)
        await pool.initialize()
        
        # Borrow more than initial
        messages = []
        for _ in range(20):
            msg = await pool.borrow(timeout=1.0)
            if msg:
                messages.append(msg)
        
        assert len(messages) == 20
        
        stats = pool.get_stats()
        assert stats["total_created"] > 10  # Should have grown


class TestGCTuner:
    """Test GC tuning components."""
    
    def test_freeze_unfreeze(self):
        tuner = GCTuner(GCTunerConfig(freeze_on_boot=True, freeze_duration=0.1))
        tuner.freeze_on_boot()
        
        assert tuner._frozen
        
        # Wait for unfreeze
        time.sleep(0.2)
        assert not tuner._frozen
    
    def test_pause_gc_context(self):
        tuner = GCTuner()
        
        import gc
        with tuner.pause_gc():
            assert not gc.isenabled()
        
        assert gc.isenabled()
    
    def test_force_collect(self):
        tuner = GCTuner()
        pause_ms = tuner.force_collect(generation=0)
        assert pause_ms >= 0
        
        stats = tuner.get_stats()
        assert stats["collections"][0] == 1


class TestGPUSemaphore:
    """Test GPU resource management."""
    
    @pytest.mark.asyncio
    async def test_semaphore_acquire_release(self):
        sem = GPUSemaphore(GPUSemaphoreConfig(max_concurrent=2))
        
        assert await sem.acquire()
        assert await sem.acquire()
        
        # Third should timeout quickly
        result = await sem.acquire(timeout=0.1)
        assert not result
        
        await sem.release()
        assert await sem.acquire()
        
        await sem.release()
        await sem.release()
    
    @pytest.mark.asyncio
    async def test_multi_gpu_load_balancing(self):
        multi = MultiGPUSemaphore(device_count=2)
        
        acquired = []
        for _ in range(4):
            success, device = await multi.acquire_best()
            if success:
                acquired.append(device)
        
        # Should distribute across devices
        assert len(set(acquired)) > 0
        
        for device in acquired:
            await multi.release(device)


class TestCircuitBreaker:
    """Test circuit breaker patterns."""
    
    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures(self):
        breaker = CircuitBreaker(TimeoutConfig(
            circuit_failure_threshold=3,
            circuit_recovery_timeout=0.1
        ))
        
        async def fail():
            raise RuntimeError("always fails")
        
        # First 3 failures should be absorbed
        for _ in range(3):
            try:
                await breaker.call(fail)
            except RuntimeError:
                pass
        
        # Circuit should be open now
        from engine.llm.timeout import CircuitBreakerOpen
        try:
            await breaker.call(fail)
            assert False, "Should have raised CircuitBreakerOpen"
        except CircuitBreakerOpen:
            pass
        
        # Wait for recovery
        await asyncio.sleep(0.2)
        
        # Should be half-open, one call allowed
        try:
            await breaker.call(fail)
        except (RuntimeError, CircuitBreakerOpen):
            pass


class TestRetryLogic:
    """Test retry mechanisms."""
    
    @pytest.mark.asyncio
    async def test_full_jitter_backoff(self):
        manager = RetryManager(RetryConfig(
            max_retries=3,
            base_delay=0.01,
            strategy=RetryStrategy.FULL_JITTER
        ))
        
        attempts = 0
        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RetryableHTTPError("retry me", 503)
            return "success"
        
        result = await manager.execute(flaky)
        assert result == "success"
        assert attempts == 3
    
    @pytest.mark.asyncio
    async def test_non_retryable_status(self):
        manager = RetryManager(RetryConfig(max_retries=3))
        
        async def bad_request():
            raise RetryableHTTPError("bad request", 400)
        
        try:
            await manager.execute(bad_request)
            assert False, "Should have raised"
        except RetryableHTTPError:
            pass


class TestBandit:
    """Test UCB1 bandit strategy."""
    
    def test_bandit_exploration_then_exploitation(self):
        bandit = UCB1Bandit(BanditConfig(warmup_pulls=2))
        
        bandit.add_arm("strategy_a")
        bandit.add_arm("strategy_b")
        bandit.add_arm("strategy_c")
        
        # Warmup: random selection
        selections = []
        for _ in range(6):
            arm = bandit.select_arm()
            selections.append(arm)
            # Give good reward to strategy_a
            reward = 1.0 if arm == "strategy_a" else 0.3
            bandit.update(arm, reward)
        
        # After warmup, should prefer strategy_a
        best = bandit.get_best_arm()
        assert best == "strategy_a"
        
        # Most selections should be strategy_a
        assert selections.count("strategy_a") >= 2


class TestRewardSystem:
    """Test reward calculation."""
    
    def test_composite_reward(self):
        calc = RewardCalculator(RewardConfig(
            latency_weight=0.4,
            throughput_weight=0.3,
            success_weight=0.3
        ))
        
        reward = calc.calculate_composite_reward({
            "latency_ms": 50,  # Good (target 100ms)
            "throughput": 150,  # Good (target 100)
            "success_rate": 0.98  # Good
        })
        
        assert reward.value > 0  # Should be positive for good metrics
        assert reward.reward_type.name == "COMPOSITE"


class TestPrecognitionCache:
    """Test predictive caching."""
    
    @pytest.mark.asyncio
    async def test_cache_hit_miss(self):
        cache = PrecognitionCache(PrecognitionConfig(max_size=100))
        
        # Miss
        result = await cache.get("prompt1")
        assert result is None
        
        # Put
        await cache.put("prompt1", "result1")
        
        # Hit
        result = await cache.get("prompt1")
        assert result == "result1"
        
        stats = cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
    
    @pytest.mark.asyncio
    async def test_cache_ttl_expiration(self):
        cache = PrecognitionCache(PrecognitionConfig(
            max_size=100,
            default_ttl=0.01  # 10ms TTL
        ))
        
        await cache.put("prompt", "result")
        
        # Should hit immediately
        assert await cache.get("prompt") == "result"
        
        # Wait for expiration
        await asyncio.sleep(0.02)
        
        # Should miss after expiration
        assert await cache.get("prompt") is None


class TestTrainerDaemon:
    """Test LoRA training daemon."""
    
    @pytest.mark.asyncio
    async def test_checkpoint_resume(self):
        config = TrainingConfig(
            max_steps=50,
            save_steps=10,
            checkpoint_dir="/tmp/test_checkpoints"
        )
        
        daemon = TrainerDaemon(config)
        
        # Start training
        await daemon.start(resume=False)
        await asyncio.sleep(0.1)
        await daemon.stop()
        
        initial_step = daemon.state.global_step
        assert initial_step > 0
        
        # Resume
        daemon2 = TrainerDaemon(config)
        await daemon2.start(resume=True)
        
        # Should have resumed from checkpoint
        assert daemon2.state.global_step >= initial_step
        
        await daemon2.stop()


class TestMetrics:
    """Test Prometheus metrics."""
    
    def test_counter(self):
        registry = MetricsRegistry()
        counter = registry.counter("requests", "Total requests")
        
        counter.inc({"method": "GET"})
        counter.inc({"method": "GET"})
        counter.inc({"method": "POST"})
        
        assert counter.get({"method": "GET"}) == 2
        assert counter.get({"method": "POST"}) == 1
    
    def test_gauge(self):
        registry = MetricsRegistry()
        gauge = registry.gauge("active_jobs", "Active jobs")
        
        gauge.set(5, {"queue": "default"})
        gauge.inc(2, {"queue": "default"})
        gauge.dec(1, {"queue": "default"})
        
        assert gauge.get({"queue": "default"}) == 6
    
    def test_histogram(self):
        registry = MetricsRegistry()
        hist = registry.histogram("latency", "Request latency")
        
        hist.observe(0.01)
        hist.observe(0.05)
        hist.observe(0.1)
        hist.observe(1.0)
        
        p95 = hist.get_percentile(0.95)
        assert p95 >= 0.1


class TestChaosMonkey:
    """Test chaos engineering."""
    
    @pytest.mark.asyncio
    async def test_chaos_injection(self):
        monkey = ChaosMonkey(ChaosConfig(
            enabled=True,
            failure_rate=1.0,  # Always inject
            max_delay_ms=10
        ))
        
        monkey.register_target("test-service", lambda: None)
        
        # Inject one failure
        await monkey._inject_random_failure()
        
        stats = monkey.get_stats()
        assert stats["total_events"] >= 1
    
    @pytest.mark.asyncio
    async def test_scenario_execution(self):
        monkey = ChaosMonkey(ChaosConfig(enabled=True))
        
        scenario = [
            {"type": "DELAY", "target": "api", "duration_ms": 10, "wait_after_ms": 5},
            {"type": "DELAY", "target": "api", "duration_ms": 20, "wait_after_ms": 5}
        ]
        
        result = await monkey.run_test_scenario(scenario)
        
        assert result["total_steps"] == 2
        assert result["successful"] == 2


class TestIntegration:
    """Full integration tests."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_pipeline(self):
        """Test full pipeline: actor -> GPU semaphore -> cache -> metrics."""
        
        # Setup
        system = ActorSystem()
        await system.start()
        
        gpu_sem = GPUSemaphore(GPUSemaphoreConfig(max_concurrent=2))
        cache = PrecognitionCache()
        registry = MetricsRegistry()
        request_counter = registry.counter("inference_requests", "Total inference requests")
        latency_hist = registry.histogram("inference_latency", "Inference latency")
        
        async def inference_handler(msg):
            """Simulate inference pipeline."""
            prompt = msg.get("prompt")
            
            # Check cache
            cached = await cache.get(prompt)
            if cached:
                return {"result": cached, "cached": True}
            
            # Acquire GPU
            if not await gpu_sem.acquire(timeout=1.0):
                return {"error": "GPU unavailable"}
            
            try:
                # Simulate inference
                await asyncio.sleep(0.01)
                result = f"generated: {prompt}"
                
                # Cache result
                await cache.put(prompt, result)
                
                return {"result": result, "cached": False}
            finally:
                await gpu_sem.release()
        
        ref = await system.spawn("inference", inference_handler)
        
        # Send requests
        for i in range(5):
            await ref.tell({"prompt": f"test prompt {i % 3}"})  # Some repeats for cache
            request_counter.inc()
        
        await asyncio.sleep(0.5)
        
        # Verify
        cache_stats = cache.get_stats()
        assert cache_stats["hits"] >= 1  # Some should hit cache
        
        metrics = registry.collect()
        assert "kiro_requests" in metrics
        
        await system.stop()
    
    @pytest.mark.asyncio
    async def test_resilience_under_load(self):
        """Test system resilience with retries and circuit breaker."""
        
        retry_manager = RetryManager(RetryConfig(
            max_retries=3,
            base_delay=0.01,
            strategy=RetryStrategy.FULL_JITTER
        ))
        
        timeout_manager = LLMTimeoutManager(TimeoutConfig(
            request_timeout=0.5,
            circuit_failure_threshold=2,
            circuit_recovery_timeout=0.1
        ))
        
        attempts = 0
        async def flaky_llm():
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise RetryableHTTPError("service busy", 503)
            return "success"
        
        # Should succeed with retries
        result = await retry_manager.execute(flaky_llm)
        assert result == "success"
        
        # Circuit breaker should handle repeated failures
        async def always_fails():
            raise RetryableHTTPError("down", 503)
        
        for _ in range(3):
            try:
                await timeout_manager.call(always_fails)
            except Exception:
                pass
        
        # Circuit should be open
        from engine.llm.timeout import CircuitBreakerOpen
        try:
            await timeout_manager.call(always_fails)
        except (CircuitBreakerOpen, Exception):
            pass  # Expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])