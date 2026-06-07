"""UCB1 Bandit for dynamic strategy selection."""

import math
import random
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Arm:
    """Single bandit arm (strategy variant)."""
    name: str
    pulls: int = 0
    rewards: float = 0.0
    last_pull: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def mean_reward(self) -> float:
        if self.pulls == 0:
            return float('inf')  # Explore unpulled arms
        return self.rewards / self.pulls
    
    def ucb1_score(self, total_pulls: int, exploration_factor: float = 2.0) -> float:
        if self.pulls == 0:
            return float('inf')
        exploitation = self.mean_reward
        exploration = math.sqrt(
            exploration_factor * math.log(total_pulls) / self.pulls
        )
        return exploitation + exploration


@dataclass
class BanditConfig:
    """UCB1 bandit configuration."""
    exploration_factor: float = 2.0
    min_pulls_before_exploit: int = 10
    decay_factor: float = 0.95  # Reward decay for old data
    warmup_pulls: int = 5  # Random exploration during warmup


class UCB1Bandit:
    """Upper Confidence Bound 1 bandit for strategy optimization."""
    
    def __init__(self, config: Optional[BanditConfig] = None):
        self.config = config or BanditConfig()
        self._arms: Dict[str, Arm] = {}
        self._total_pulls = 0
        self._history: List[Dict] = []
        
    def add_arm(self, name: str, metadata: Optional[Dict] = None) -> None:
        """Add a new strategy arm."""
        self._arms[name] = Arm(name=name, metadata=metadata or {})
        logger.info(f"Added bandit arm: {name}")
    
    def select_arm(self) -> str:
        """Select arm using UCB1 strategy."""
        if not self._arms:
            raise RuntimeError("No arms available")
        
        # Warmup: random exploration
        if self._total_pulls < self.config.warmup_pulls:
            return random.choice(list(self._arms.keys()))
        
        # Ensure minimum exploration
        unpulled = [name for name, arm in self._arms.items() if arm.pulls == 0]
        if unpulled:
            return random.choice(unpulled)
        
        # UCB1 selection
        scores = {
            name: arm.ucb1_score(self._total_pulls, self.config.exploration_factor)
            for name, arm in self._arms.items()
        }
        
        selected = max(scores, key=scores.get)
        logger.debug(f"Selected arm {selected} with score {scores[selected]:.4f}")
        return selected
    
    def update(self, arm_name: str, reward: float) -> None:
        """Update arm with observed reward."""
        if arm_name not in self._arms:
            raise ValueError(f"Unknown arm: {arm_name}")
        
        arm = self._arms[arm_name]
        arm.pulls += 1
        arm.rewards += reward
        arm.last_pull = datetime.now()
        self._total_pulls += 1
        
        self._history.append({
            "arm": arm_name,
            "reward": reward,
            "timestamp": datetime.now(),
            "cumulative_reward": arm.rewards
        })
        
        # Trim history if too large
        if len(self._history) > 10000:
            self._history = self._history[-5000:]
    
    def get_best_arm(self) -> Optional[str]:
        """Get currently best performing arm."""
        if not self._arms:
            return None
        return max(
            self._arms.items(),
            key=lambda x: x[1].mean_reward if x[1].pulls > 0 else -1
        )[0]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get bandit statistics."""
        return {
            "total_pulls": self._total_pulls,
            "arms": {
                name: {
                    "pulls": arm.pulls,
                    "mean_reward": arm.mean_reward if arm.pulls > 0 else None,
                    "total_reward": arm.rewards,
                    "ucb1_score": arm.ucb1_score(
                        self._total_pulls, self.config.exploration_factor
                    ) if self._total_pulls > 0 else float('inf')
                }
                for name, arm in self._arms.items()
            },
            "best_arm": self.get_best_arm(),
            "exploration_ratio": sum(
                1 for arm in self._arms.values() if arm.pulls < self.config.min_pulls_before_exploit
            ) / max(1, len(self._arms))
        }
    
    def decay_old_rewards(self) -> None:
        """Apply temporal decay to old rewards."""
        for arm in self._arms.values():
            arm.rewards *= self.config.decay_factor


class ContextualBandit(UCB1Bandit):
    """Contextual UCB1 with feature-based arm selection."""
    
    def __init__(self, config: Optional[BanditConfig] = None):
        super().__init__(config)
        self._context_weights: Dict[str, Dict[str, float]] = {}
        
    def add_arm(self, name: str, metadata: Optional[Dict] = None,
                context_features: Optional[List[str]] = None) -> None:
        super().add_arm(name, metadata)
        if context_features:
            self._context_weights[name] = {f: 1.0 for f in context_features}
    
    def select_arm_for_context(self, context: Dict[str, float]) -> str:
        """Select arm based on context features."""
        if not self._arms:
            raise RuntimeError("No arms available")
        
        scores = {}
        for name, arm in self._arms.items():
            base_score = arm.ucb1_score(self._total_pulls, self.config.exploration_factor)
            
            # Apply context weights
            if name in self._context_weights:
                context_bonus = sum(
                    context.get(feature, 0) * weight
                    for feature, weight in self._context_weights[name].items()
                )
                base_score += context_bonus
            
            scores[name] = base_score
        
        return max(scores, key=scores.get)
    
    def update_context_weights(self, arm_name: str, 
                               context: Dict[str, float],
                               reward: float) -> None:
        """Update context weights based on reward."""
        if arm_name not in self._context_weights:
            return
        
        learning_rate = 0.1
        for feature, value in context.items():
            if feature in self._context_weights[arm_name]:
                error = reward - self._context_weights[arm_name][feature] * value
                self._context_weights[arm_name][feature] += learning_rate * error * value


__all__ = ["UCB1Bandit", "ContextualBandit", "BanditConfig", "Arm"]