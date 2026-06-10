"""GPU Semaphore - Token-bucket rate limiter for GPU inference."""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class GPUSemaphoreConfig:
    """GPU semaphore configuration."""
    max_concurrent: int = 4          # Max concurrent inference requests
    max_queue_depth: int = 100       # Max queue before backpressure
    token_rate: float = 10.0         # Tokens per second replenishment
    burst_size: int = 20             # Max burst tokens
    timeout_seconds: float = 30.0    # Max wait for token
    priority_boost: float = 2.0      # Priority multiplier for critical


class GPUSemaphore:
    """Token-bucket semaphore for GPU inference rate limiting."""
    
    def __init__(self, config: Optional[GPUSemaphoreConfig] = None):
        self.config = config or GPUSemaphoreConfig()
        self._tokens = self.config.burst_size
        self._max_tokens = self.config.burst_size
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition(self._lock)
        self._active_count = 0
        self._queue_depth = 0
        self._total_acquired = 0
        self._total_rejected = 0
        self._total_timedout = 0
        
    async def acquire(self, tokens: int = 1, priority: float = 1.0,
                     timeout: Optional[float] = None) -> bool:
        """Acquire tokens for GPU access."""
        timeout = timeout or self.config.timeout_seconds
        start_time = time.monotonic()
        
        try:
            async with self._condition:
                self._queue_depth += 1
                while True:
                    self._replenish_tokens()
                    
                    # Check if we can acquire
                    adjusted_tokens = tokens / priority
                    if (self._tokens >= adjusted_tokens and 
                        self._active_count < self.config.max_concurrent):
                        self._tokens -= adjusted_tokens
                        self._active_count += 1
                        self._total_acquired += 1
                        return True
                    
                    # Check timeout
                    elapsed = time.monotonic() - start_time
                    if elapsed >= timeout:
                        self._total_timedout += 1
                        return False
                    
                    # Check queue depth
                    if self._queue_depth > self.config.max_queue_depth:
                        self._total_rejected += 1
                        return False
                    
                    # Wait for token replenishment
                    wait_time = min(1.0, timeout - elapsed)
                    try:
                        await asyncio.wait_for(
                            self._condition.wait(), 
                            timeout=wait_time
                        )
                    except asyncio.TimeoutError:
                        continue
                        
        finally:
            async with self._condition:
                self._queue_depth -= 1
    
    def _replenish_tokens(self) -> None:
        """Replenish tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._last_update = now
        
        new_tokens = elapsed * self.config.token_rate
        self._tokens = min(self._max_tokens, self._tokens + new_tokens)
    
    async def release(self, tokens: int = 1) -> None:
        """Release tokens back to the pool."""
        async with self._lock:
            self._active_count = max(0, self._active_count - 1)
            # Don't return tokens - they replenish over time
            
        async with self._condition:
            self._condition.notify_all()
    
    @property
    def available_tokens(self) -> float:
        self._replenish_tokens()
        return self._tokens
    
    @property
    def utilization(self) -> float:
        return self._active_count / max(1, self.config.max_concurrent)
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "tokens": self.available_tokens,
            "active": self._active_count,
            "queue_depth": self._queue_depth,
            "utilization": self.utilization,
            "total_acquired": self._total_acquired,
            "total_rejected": self._total_rejected,
            "total_timedout": self._total_timedout,
            "max_concurrent": self.config.max_concurrent
        }
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


class MultiGPUSemaphore:
    """Manage semaphores across multiple GPU devices."""
    
    def __init__(self, device_count: int = 1, 
                 config: Optional[GPUSemaphoreConfig] = None):
        self.device_count = device_count
        self.config = config or GPUSemaphoreConfig()
        self._semaphores: Dict[int, GPUSemaphore] = {
            i: GPUSemaphore(config) for i in range(device_count)
        }
        self._device_loads: Dict[int, float] = {i: 0.0 for i in range(device_count)}
        
    async def acquire_best(self, tokens: int = 1, 
                           priority: float = 1.0) -> tuple[bool, int]:
        """Acquire on least-loaded GPU."""
        # Sort by load
        devices = sorted(self._device_loads.items(), key=lambda x: x[1])
        
        for device_id, _ in devices:
            sem = self._semaphores[device_id]
            if await sem.acquire(tokens, priority, timeout=0.1):
                self._device_loads[device_id] = sem.utilization
                return True, device_id
                
        # Fallback to first available with longer timeout
        for device_id, _ in devices:
            sem = self._semaphores[device_id]
            if await sem.acquire(tokens, priority):
                self._device_loads[device_id] = sem.utilization
                return True, device_id
                
        return False, -1
    
    async def release(self, device_id: int, tokens: int = 1) -> None:
        if device_id in self._semaphores:
            await self._semaphores[device_id].release(tokens)
            self._device_loads[device_id] = self._semaphores[device_id].utilization
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "devices": {
                i: sem.get_stats() for i, sem in self._semaphores.items()
            },
            "device_loads": self._device_loads.copy()
        }


__all__ = ["GPUSemaphore", "GPUSemaphoreConfig", "MultiGPUSemaphore"]