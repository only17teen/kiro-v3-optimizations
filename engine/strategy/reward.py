"""Reward signal computation for bandit and RL feedback."""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime
from enum import Enum, auto

logger = logging.getLogger(__name__)


class RewardType(Enum):
    LATENCY = auto()
    THROUGHPUT = auto()
    SUCCESS_RATE = auto()
    COST = auto()
    QUALITY = auto()
    COMPOSITE = auto()


@dataclass
class RewardSignal:
    """Computed reward signal."""
    value: float
    reward_type: RewardType
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0  # 0-1 confidence in this reward
    
    def normalize(self, min_val: float = -1.0, max_val: float = 1.0) -> 'RewardSignal':
        """Normalize reward to [-1, 1] range."""
        if max_val == min_val:
            return self
        normalized = 2 * (self.value - min_val) / (max_val - min_val) - 1
        return RewardSignal(
            value=max(-1.0, min(1.0, normalized)),
            reward_type=self.reward_type,
            confidence=self.confidence
        )


@dataclass
class RewardConfig:
    """Reward computation configuration."""
    latency_target_ms: float = 100.0
    throughput_target: float = 100.0
    cost_target: float = 1.0
    quality_threshold: float = 0.8
    latency_weight: float = 0.3
    throughput_weight: float = 0.2
    success_weight: float = 0.3
    cost_weight: float = 0.1
    quality_weight: float = 0.1


class RewardCalculator:
    """Calculate reward signals from system metrics."""
    
    def __init__(self, config: Optional[RewardConfig] = None):
        self.config = config or RewardConfig()
        self._history: List[RewardSignal] = []
        
    def calculate_latency_reward(self, latency_ms: float) -> RewardSignal:
        """Calculate reward based on latency (lower is better)."""
        # Exponential decay: reward = exp(-latency/target)
        ratio = latency_ms / self.config.latency_target_ms
        reward = math.exp(-ratio) * 2 - 1  # Map to [-1, 1]
        
        return RewardSignal(
            value=reward,
            reward_type=RewardType.LATENCY,
            metadata={"latency_ms": latency_ms, "target_ms": self.config.latency_target_ms}
        )
    
    def calculate_throughput_reward(self, throughput: float) -> RewardSignal:
        """Calculate reward based on throughput (higher is better)."""
        ratio = throughput / self.config.throughput_target
        reward = min(1.0, ratio - 1.0)  # Linear above target, clipped
        
        return RewardSignal(
            value=reward,
            reward_type=RewardType.THROUGHPUT,
            metadata={"throughput": throughput, "target": self.config.throughput_target}
        )
    
    def calculate_success_reward(self, success_rate: float) -> RewardSignal:
        """Calculate reward based on success rate."""
        reward = (success_rate - 0.5) * 2  # Center at 0.5, scale to [-1, 1]
        
        return RewardSignal(
            value=max(-1.0, min(1.0, reward)),
            reward_type=RewardType.SUCCESS_RATE,
            metadata={"success_rate": success_rate}
        )
    
    def calculate_cost_reward(self, cost: float) -> RewardSignal:
        """Calculate reward based on cost (lower is better)."""
        ratio = cost / self.config.cost_target
        reward = -min(1.0, ratio - 1.0)  # Negative reward for high cost
        
        return RewardSignal(
            value=reward,
            reward_type=RewardType.COST,
            metadata={"cost": cost, "target": self.config.cost_target}
        )
    
    def calculate_quality_reward(self, quality_score: float) -> RewardSignal:
        """Calculate reward based on output quality."""
        reward = (quality_score - self.config.quality_threshold) * 5
        
        return RewardSignal(
            value=max(-1.0, min(1.0, reward)),
            reward_type=RewardType.QUALITY,
            metadata={"quality": quality_score, "threshold": self.config.quality_threshold}
        )
    
    def calculate_composite_reward(self, metrics: Dict[str, float]) -> RewardSignal:
        """Calculate weighted composite reward."""
        rewards = []
        
        if "latency_ms" in metrics:
            rewards.append((
                self.calculate_latency_reward(metrics["latency_ms"]),
                self.config.latency_weight
            ))
        
        if "throughput" in metrics:
            rewards.append((
                self.calculate_throughput_reward(metrics["throughput"]),
                self.config.throughput_weight
            ))
        
        if "success_rate" in metrics:
            rewards.append((
                self.calculate_success_reward(metrics["success_rate"]),
                self.config.success_weight
            ))
        
        if "cost" in metrics:
            rewards.append((
                self.calculate_cost_reward(metrics["cost"]),
                self.config.cost_weight
            ))
        
        if "quality" in metrics:
            rewards.append((
                self.calculate_quality_reward(metrics["quality"]),
                self.config.quality_weight
            ))
        
        if not rewards:
            return RewardSignal(value=0.0, reward_type=RewardType.COMPOSITE)
        
        # Weighted average
        total_weight = sum(w for _, w in rewards)
        composite = sum(r.value * w for r, w in rewards) / total_weight
        
        signal = RewardSignal(
            value=composite,
            reward_type=RewardType.COMPOSITE,
            metadata={
                "components": [
                    {"type": r.reward_type.name, "value": r.value, "weight": w}
                    for r, w in rewards
                ],
                "total_weight": total_weight
            }
        )
        
        self._history.append(signal)
        return signal
    
    def get_reward_history(self, reward_type: Optional[RewardType] = None,
                          limit: int = 100) -> List[RewardSignal]:
        """Get reward history, optionally filtered by type."""
        filtered = self._history
        if reward_type:
            filtered = [r for r in filtered if r.reward_type == reward_type]
        return filtered[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get reward statistics."""
        if not self._history:
            return {"samples": 0}
        
        by_type = {}
        for signal in self._history:
            rt = signal.reward_type.name
            if rt not in by_type:
                by_type[rt] = []
            by_type[rt].append(signal.value)
        
        return {
            "samples": len(self._history),
            "by_type": {
                rt: {
                    "mean": sum(vals) / len(vals),
                    "min": min(vals),
                    "max": max(vals),
                    "latest": vals[-1]
                }
                for rt, vals in by_type.items()
            }
        }


import math  # Import for exponential


class RewardFeedbackLoop:
    """Connect reward signals to bandit updates."""
    
    def __init__(self, calculator: RewardCalculator, 
                 bandit: Optional[Any] = None):
        self.calculator = calculator
        self.bandit = bandit
        self._reward_buffer: List[tuple] = []  # (arm_name, reward, timestamp)
        self._buffer_size = 100
        
    def record_outcome(self, arm_name: str, metrics: Dict[str, float]) -> RewardSignal:
        """Record outcome and update bandit."""
        reward = self.calculator.calculate_composite_reward(metrics)
        
        self._reward_buffer.append((arm_name, reward.value, datetime.now()))
        if len(self._reward_buffer) > self._buffer_size:
            self._reward_buffer = self._reward_buffer[-self._buffer_size:]
        
        if self.bandit:
            self.bandit.update(arm_name, reward.value)
        
        logger.debug(
            f"Reward for {arm_name}: {reward.value:.4f} "
            f"(type: {reward.reward_type.name})"
        )
        return reward
    
    def get_arm_performance(self, arm_name: str) -> Optional[Dict[str, Any]]:
        """Get performance summary for an arm."""
        arm_rewards = [
            r for a, r, _ in self._reward_buffer if a == arm_name
        ]
        
        if not arm_rewards:
            return None
        
        return {
            "arm": arm_name,
            "samples": len(arm_rewards),
            "mean_reward": sum(arm_rewards) / len(arm_rewards),
            "total_reward": sum(arm_rewards),
            "latest_reward": arm_rewards[-1]
        }


__all__ = [
    "RewardCalculator",
    "RewardSignal",
    "RewardConfig",
    "RewardType",
    "RewardFeedbackLoop"
]