"""Full system integration example demonstrating all Kiro v3.0 components."""

import asyncio
import logging
from typing import Dict, Any, Optional

from engine.actor import ActorSystem, RouteStrategy, Priority, ActorRef
from engine.actor.pool import MessagePool
from engine.gc_tuner import GCTuner, freeze_on_boot
from engine.gpu.semaphore import GPUSemaphore, MultiGPUSemaphore
from engine.llm.timeout import LLMTimeoutManager, TimeoutConfig
from engine.retry import RetryManager, RetryConfig, RetryStrategy
from engine.strategy.bandit import UCB1Bandit, ContextualBandit
from engine.strategy.reward import RewardCalculator, RewardFeedbackLoop, RewardConfig
from engine.cache.precognition import PrecognitionCache
from engine.training.trainer_daemon import TrainerDaemon, TrainingConfig
from engine.metrics import MetricsRegistry, get_registry
from tests.chaos.test_chaos import ChaosMonkey, ChaosConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ComfyUIEngineKiro:
    """Production-ready ComfyUI engine with Kiro v3.0 optimizations."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        
        # Phase 1: Actor Model
        self.system = ActorSystem(router_strategy=RouteStrategy.HASH_RING)
        self.message_pool = MessagePool(initial_size=10000)
        
        # Phase 2: Resource Management
        self.gpu_semaphore = MultiGPUSemaphore(
            device_count=self.config.get("gpu_count", 1)
        )
        self.llm_timeout = LLMTimeoutManager(TimeoutConfig(
            request_timeout=self.config.get("llm_timeout", 30.0),
            adaptive_timeout=True
        ))
        
        # Phase 3: Resilience
        self.retry_manager = RetryManager(RetryConfig(
            max_retries=self.config.get("max_retries", 3),
            strategy=RetryStrategy.FULL_JITTER
        ))
        
        # Phase 4: Strategy Optimization
        self.bandit = UCB1Bandit()
        self.reward_calculator = RewardCalculator(RewardConfig(
            latency_target_ms=self.config.get("latency_target", 100.0)
        ))
        self.reward_loop = RewardFeedbackLoop(self.reward_calculator, self.bandit)
        
        # Phase 5: Learning
        self.cache = PrecognitionCache()
        self.trainer = TrainerDaemon(TrainingConfig(
            output_dir=self.config.get("lora_output", "./lora_output"),
            checkpoint_dir=self.config.get("checkpoint_dir", "./checkpoints")
        ))
        
        # Phase 6: Observability
        self.metrics = MetricsRegistry()
        self.chaos = ChaosMonkey(ChaosConfig(
            enabled=self.config.get("chaos_enabled", False)
        ))
        
        # Component references
        self.inference_actor: Optional[ActorRef] = None
        self.gc_tuner: Optional[GCTuner] = None
        
    async def initialize(self) -> None:
        """Initialize all subsystems."""
        logger.info("Initializing Kiro v3.0 Engine...")
        
        # Freeze GC during boot
        self.gc_tuner = GCTuner()
        self.gc_tuner.freeze_on_boot()
        
        # Initialize message pool
        await self.message_pool.initialize()
        
        # Start actor system
        await self.system.start()
        
        # Setup metrics
        self._setup_metrics()
        
        # Register actors
        await self._register_actors()
        
        # Start trainer (if enabled)
        if self.config.get("training_enabled", False):
            await self.trainer.start(resume=True)
        
        # Start chaos monkey (if enabled)
        if self.config.get("chaos_enabled", False):
            asyncio.create_task(self.chaos.start(interval_seconds=60))
        
        logger.info("Kiro v3.0 Engine initialized successfully")
    
    def _setup_metrics(self) -> None:
        """Setup Prometheus metrics."""
        self.request_counter = self.metrics.counter(
            "inference_requests", "Total inference requests"
        )
        self.latency_hist = self.metrics.histogram(
            "inference_latency", "Inference latency in seconds"
        )
        self.gpu_util_gauge = self.metrics.gauge(
            "gpu_utilization", "GPU utilization ratio"
        )
        self.cache_hit_counter = self.metrics.counter(
            "cache_hits", "Cache hit count"
        )
        self.cache_miss_counter = self.metrics.counter(
            "cache_misses", "Cache miss count"
        )
    
    async def _register_actors(self) -> None:
        """Register all system actors."""
        
        # Inference actor - main LLM inference pipeline
        async def inference_handler(msg: Dict[str, Any]) -> Dict[str, Any]:
            prompt = msg.get("prompt", "")
            params = msg.get("params", {})
            
            # Check cache
            cached = await self.cache.get(prompt, params)
            if cached:
                self.cache_hit_counter.inc({"status": "hit"})
                return {"result": cached, "cached": True, "latency_ms": 0}
            
            self.cache_miss_counter.inc({"status": "miss"})
            
            # Select GPU strategy using bandit
            strategy = self.bandit.select_arm()
            
            # Acquire GPU with timeout
            gpu_timeout = self.llm_timeout.adaptive.get_timeout()
            success, device_id = await self.gpu_semaphore.acquire_best(
                priority=msg.get("priority", 1.0)
            )
            
            if not success:
                return {"error": "GPU unavailable", "retry_after": 30}
            
            try:
                # Execute inference with retry
                start_time = asyncio.get_event_loop().time()
                
                result = await self.retry_manager.execute(
                    self._run_inference,
                    prompt,
                    params,
                    device_id
                )
                
                latency_ms = (asyncio.get_event_loop().time() - start_time) * 1000
                
                # Record metrics
                self.latency_hist.observe(latency_ms / 1000)
                self.request_counter.inc({"status": "success"})
                
                # Cache result
                await self.cache.put(prompt, result, params)
                
                # Update reward for strategy
                await self.reward_loop.record_outcome(strategy, {
                    "latency_ms": latency_ms,
                    "throughput": 1.0,
                    "success_rate": 1.0,
                    "cost": 1.0,
                    "quality": 0.95
                })
                
                return {
                    "result": result,
                    "cached": False,
                    "latency_ms": latency_ms,
                    "device": device_id,
                    "strategy": strategy
                }
                
            except Exception as e:
                self.request_counter.inc({"status": "error"})
                return {"error": str(e), "retry_after": 5}
                
            finally:
                await self.gpu_semaphore.release(device_id)
        
        self.inference_actor = await self.system.spawn(
            "inference", inference_handler
        )
        
        # Bandit strategy arms
        self.bandit.add_arm("greedy", {"description": "Always use GPU 0"})
        self.bandit.add_arm("round_robin", {"description": "Rotate GPUs"})
        self.bandit.add_arm("least_loaded", {"description": "Pick least loaded GPU"})
        
        logger.info("Registered inference actor with 3 GPU strategies")
    
    async def _run_inference(self, prompt: str, params: Dict, device_id: int) -> str:
        """Simulate LLM inference (replace with actual model call)."""
        # Simulate processing time
        await asyncio.sleep(0.05)
        return f"Generated output for: {prompt[:50]}..."
    
    async def submit_job(self, prompt: str, params: Optional[Dict] = None,
                        priority: float = 1.0) -> Dict[str, Any]:
        """Submit inference job to the engine."""
        if not self.inference_actor:
            raise RuntimeError("Engine not initialized")
        
        result = await self.inference_actor.ask({
            "prompt": prompt,
            "params": params or {},
            "priority": priority
        }, timeout=60.0)
        
        return result or {"error": "Timeout"}
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive system statistics."""
        return {
            "actor_system": self.system.get_stats(),
            "gpu": self.gpu_semaphore.get_stats(),
            "cache": self.cache.get_stats(),
            "metrics": self.metrics.collect(),
            "bandit": self.bandit.get_stats(),
            "retry": self.retry_manager.get_stats(),
            "llm_timeout": self.llm_timeout.get_stats(),
            "gc": self.gc_tuner.get_stats() if self.gc_tuner else None,
            "training": self.trainer.get_stats() if self.trainer else None,
            "chaos": self.chaos.get_stats() if self.chaos else None
        }
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Kiro v3.0 Engine...")
        
        # Stop chaos
        self.chaos.stop()
        
        # Stop trainer
        await self.trainer.stop()
        
        # Stop actor system
        await self.system.stop()
        
        # Cleanup GC tuner
        if self.gc_tuner:
            self.gc_tuner.shutdown()
        
        logger.info("Kiro v3.0 Engine shutdown complete")


async def main():
    """Run integration example."""
    
    engine = ComfyUIEngineKiro({
        "gpu_count": 2,
        "llm_timeout": 30.0,
        "max_retries": 3,
        "latency_target": 100.0,
        "training_enabled": False,
        "chaos_enabled": False
    })
    
    try:
        await engine.initialize()
        
        # Submit sample jobs
        prompts = [
            "A beautiful sunset over mountains",
            "A futuristic city skyline",
            "A beautiful sunset over mountains",  # Cache hit
            "An abstract painting of emotions",
            "A futuristic city skyline",  # Cache hit
        ]
        
        for i, prompt in enumerate(prompts):
            logger.info(f"Submitting job {i+1}: {prompt[:40]}...")
            result = await engine.submit_job(prompt, priority=2.0 if i == 0 else 1.0)
            logger.info(f"Result: {result}")
            await asyncio.sleep(0.1)
        
        # Print stats
        stats = await engine.get_stats()
        logger.info("\n=== System Statistics ===")
        logger.info(f"Cache hit rate: {stats['cache']['hit_rate']:.2%}")
        logger.info(f"GPU utilization: {stats['gpu']['device_loads']}")
        logger.info(f"Bandit best arm: {stats['bandit']['best_arm']}")
        logger.info(f"Total requests: {stats['metrics']['kiro_inference_requests']['values']}")
        
    finally:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(main())