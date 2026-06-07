"""GPU Strategy Example - Dynamic GPU strategy selection with UCB1 Bandit.

This example demonstrates how to use the UCB1 Bandit to dynamically select
the best GPU allocation strategy based on workload characteristics.
"""

import asyncio
import random
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

from engine.gpu.semaphore import GPUSemaphore, MultiGPUSemaphore, GPUSemaphoreConfig
from engine.strategy.bandit import UCB1Bandit, ContextualBandit, BanditConfig
from engine.strategy.reward import RewardCalculator, RewardFeedbackLoop, RewardConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class WorkloadProfile:
    """Profile of incoming workload."""
    batch_size: int
    image_resolution: str  # "512x512", "1024x1024", "2048x2048"
    model_complexity: str  # "sdxl", "sd15", "flux"
    queue_depth: int
    time_of_day: str  # "peak", "off-peak", "night"


class GPUStrategySelector:
    """Dynamic GPU strategy selector using UCB1 Bandit."""
    
    def __init__(self, device_count: int = 4):
        self.device_count = device_count
        self.bandit = UCB1Bandit(BanditConfig(
            exploration_factor=2.0,
            warmup_pulls=10,
            decay_factor=0.95
        ))
        self.reward_calculator = RewardCalculator(RewardConfig(
            latency_target_ms=100.0,
            throughput_target=50.0,
            latency_weight=0.4,
            throughput_weight=0.3,
            success_weight=0.2,
            cost_weight=0.1
        ))
        self.feedback_loop = RewardFeedbackLoop(self.reward_calculator, self.bandit)
        
        # Initialize strategies
        self._init_strategies()
    
    def _init_strategies(self) -> None:
        """Initialize GPU allocation strategies."""
        strategies = {
            "greedy": {
                "description": "Always use GPU 0 (single GPU)",
                "devices": [0],
                "batch_size": 1
            },
            "round_robin": {
                "description": "Rotate across all GPUs evenly",
                "devices": list(range(self.device_count)),
                "batch_size": 1
            },
            "least_loaded": {
                "description": "Pick GPU with lowest utilization",
                "devices": list(range(self.device_count)),
                "batch_size": 1
            },
            "batch_parallel": {
                "description": "Split batch across all GPUs",
                "devices": list(range(self.device_count)),
                "batch_size": self.device_count
            },
            "dynamic_batch": {
                "description": "Adaptive batch size based on queue depth",
                "devices": list(range(self.device_count)),
                "batch_size": "adaptive"
            },
            "priority_queue": {
                "description": "Separate queues per GPU by priority",
                "devices": list(range(self.device_count)),
                "batch_size": 2
            }
        }
        
        for name, config in strategies.items():
            self.bandit.add_arm(name, metadata=config)
        
        logger.info(f"Initialized {len(strategies)} GPU strategies")
    
    async def select_strategy(self, workload: WorkloadProfile) -> str:
        """Select best strategy for current workload."""
        strategy = self.bandit.select_arm()
        logger.info(f"Selected strategy '{strategy}' for workload: "
                   f"batch={workload.batch_size}, res={workload.image_resolution}")
        return strategy
    
    async def record_outcome(self, strategy: str, workload: WorkloadProfile,
                            latency_ms: float, throughput: float,
                            success: bool, gpu_utilization: List[float]) -> None:
        """Record strategy outcome and update bandit."""
        
        # Calculate composite reward
        metrics = {
            "latency_ms": latency_ms,
            "throughput": throughput,
            "success_rate": 1.0 if success else 0.0,
            "cost": sum(gpu_utilization) / len(gpu_utilization) if gpu_utilization else 0.5,
        }
        
        reward = await self.feedback_loop.record_outcome(strategy, metrics)
        
        logger.info(f"Strategy '{strategy}' reward: {reward.value:.4f} "
                   f"(latency={latency_ms:.1f}ms, throughput={throughput:.1f})")
    
    def get_best_strategy(self) -> str:
        """Get currently best performing strategy."""
        return self.bandit.get_best_arm() or "greedy"
    
    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics."""
        return {
            "bandit": self.bandit.get_stats(),
            "best_strategy": self.get_best_strategy(),
        }


class SimulatedGPUWorkload:
    """Simulate GPU workload for testing strategies."""
    
    def __init__(self, device_count: int = 4):
        self.device_count = device_count
        self.gpu_utilization = [0.0] * device_count
        self.multi_gpu = MultiGPUSemaphore(device_count=device_count)
    
    async def simulate_inference(self, strategy: str, workload: WorkloadProfile) -> Dict[str, Any]:
        """Simulate inference with given strategy."""
        
        # Base latency depends on resolution and model
        resolution_multiplier = {
            "512x512": 1.0,
            "1024x1024": 2.5,
            "2048x2048": 6.0
        }
        
        model_multiplier = {
            "sd15": 1.0,
            "sdxl": 1.8,
            "flux": 3.0
        }
        
        base_latency = 50.0  # ms
        latency = (base_latency * 
                  resolution_multiplier.get(workload.image_resolution, 1.0) *
                  model_multiplier.get(workload.model_complexity, 1.0))
        
        # Strategy affects latency
        strategy_latency = {
            "greedy": latency * 1.5,  # Queue buildup
            "round_robin": latency * 1.0,
            "least_loaded": latency * 0.9,
            "batch_parallel": latency * 0.7,
            "dynamic_batch": latency * 0.8,
            "priority_queue": latency * 0.85
        }
        
        actual_latency = strategy_latency.get(strategy, latency)
        
        # Add noise
        actual_latency *= random.uniform(0.9, 1.1)
        
        # Simulate GPU utilization
        for i in range(self.device_count):
            if strategy in ["greedy"] and i == 0:
                self.gpu_utilization[i] = min(1.0, self.gpu_utilization[i] + 0.3)
            elif strategy in ["round_robin", "least_loaded"]:
                self.gpu_utilization[i] = random.uniform(0.3, 0.7)
            else:
                self.gpu_utilization[i] = random.uniform(0.5, 0.9)
        
        # Simulate throughput
        throughput = workload.batch_size / (actual_latency / 1000.0)
        
        # Simulate occasional failures
        success = random.random() > 0.02  # 2% failure rate
        
        await asyncio.sleep(actual_latency / 1000.0)  # Simulate processing time
        
        return {
            "latency_ms": actual_latency,
            "throughput": throughput,
            "success": success,
            "gpu_utilization": self.gpu_utilization.copy(),
        }


async def main():
    """Run GPU strategy example."""
    
    # Initialize components
    selector = GPUStrategySelector(device_count=4)
    simulator = SimulatedGPUWorkload(device_count=4)
    
    # Generate workload profiles
    workloads = [
        WorkloadProfile(
            batch_size=random.randint(1, 8),
            image_resolution=random.choice(["512x512", "1024x1024", "2048x2048"]),
            model_complexity=random.choice(["sd15", "sdxl", "flux"]),
            queue_depth=random.randint(0, 50),
            time_of_day=random.choice(["peak", "off-peak", "night"])
        )
        for _ in range(100)
    ]
    
    logger.info("Starting GPU strategy optimization...")
    
    # Run workload through different strategies
    for i, workload in enumerate(workloads):
        # Select strategy
        strategy = await selector.select_strategy(workload)
        
        # Simulate inference
        result = await simulator.simulate_inference(strategy, workload)
        
        # Record outcome
        await selector.record_outcome(
            strategy=strategy,
            workload=workload,
            latency_ms=result["latency_ms"],
            throughput=result["throughput"],
            success=result["success"],
            gpu_utilization=result["gpu_utilization"]
        )
        
        # Log progress
        if (i + 1) % 20 == 0:
            stats = selector.get_stats()
            logger.info(f"\nProgress: {i+1}/100")
            logger.info(f"Best strategy: {stats['best_strategy']}")
            logger.info(f"Arm statistics:")
            for arm_name, arm_stats in stats["bandit"]["arms"].items():
                pulls = arm_stats["pulls"]
                mean_reward = arm_stats.get("mean_reward", "N/A")
                logger.info(f"  {arm_name}: pulls={pulls}, mean_reward={mean_reward}")
    
    # Final results
    logger.info("\n" + "=" * 50)
    logger.info("FINAL RESULTS")
    logger.info("=" * 50)
    
    stats = selector.get_stats()
    logger.info(f"Best overall strategy: {stats['best_strategy']}")
    logger.info(f"\nStrategy rankings:")
    
    arms = stats["bandit"]["arms"]
    sorted_arms = sorted(
        arms.items(),
        key=lambda x: x[1].get("mean_reward", -1) or -1,
        reverse=True
    )
    
    for rank, (name, arm_stats) in enumerate(sorted_arms, 1):
        pulls = arm_stats["pulls"]
        mean_reward = arm_stats.get("mean_reward", "N/A")
        logger.info(f"  {rank}. {name}: pulls={pulls}, mean_reward={mean_reward}")
    
    # Recommend strategy for different workload types
    logger.info("\nRecommended strategies by workload:")
    
    test_workloads = {
        "small_batch": WorkloadProfile(1, "512x512", "sd15", 5, "peak"),
        "large_batch": WorkloadProfile(8, "1024x1024", "sdxl", 30, "peak"),
        "high_res": WorkloadProfile(2, "2048x2048", "flux", 10, "off-peak"),
    }
    
    for name, workload in test_workloads.items():
        strategy = await selector.select_strategy(workload)
        logger.info(f"  {name}: {strategy}")


if __name__ == "__main__":
    asyncio.run(main())