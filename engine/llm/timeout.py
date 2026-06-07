"""LLM Timeout - Circuit breaker and timeout management for LLM calls."""

import asyncio
import time
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, List
from functools import wraps

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = auto()      # Normal operation
    OPEN = auto()        # Failing, reject requests
    HALF_OPEN = auto()   # Testing recovery


@dataclass
class TimeoutConfig:
    """Timeout and circuit breaker configuration."""
    request_timeout: float = 30.0
    connect_timeout: float = 5.0
    read_timeout: float = 25.0
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 30.0
    circuit_half_open_max: int = 3
    adaptive_timeout: bool = True
    timeout_percentile: float = 0.95
    min_timeout: float = 5.0
    max_timeout: float = 120.0


class CircuitBreaker:
    """Circuit breaker for LLM service protection."""
    
    def __init__(self, config: Optional[TimeoutConfig] = None):
        self.config = config or TimeoutConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
        
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info("Circuit breaker entering HALF_OPEN state")
                else:
                    raise CircuitBreakerOpen("Circuit breaker is OPEN")
                    
            elif self._state == CircuitState.HALF_OPEN:
                if self._success_count >= self.config.circuit_half_open_max:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit breaker CLOSED")
        
        try:
            result = await func(*args, **kwargs)
            await self._record_success()
            return result
        except Exception as e:
            await self._record_failure()
            raise
    
    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
            else:
                self._failure_count = max(0, self._failure_count - 1)
    
    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit breaker OPEN (half-open failure)")
            elif self._failure_count >= self.config.circuit_failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(f"Circuit breaker OPEN ({self._failure_count} failures)")
    
    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return True
        return (time.monotonic() - self._last_failure_time) >= self.config.circuit_recovery_timeout
    
    @property
    def state(self) -> CircuitState:
        return self._state
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "last_failure": self._last_failure_time
        }


class CircuitBreakerOpen(Exception):
    pass


class AdaptiveTimeout:
    """Adaptive timeout based on response time percentiles."""
    
    def __init__(self, config: Optional[TimeoutConfig] = None):
        self.config = config or TimeoutConfig()
        self._response_times: List[float] = []
        self._max_samples = 1000
        self._lock = asyncio.Lock()
        
    async def record_response_time(self, duration: float) -> None:
        async with self._lock:
            self._response_times.append(duration)
            if len(self._response_times) > self._max_samples:
                self._response_times = self._response_times[-self._max_samples:]
    
    def get_timeout(self) -> float:
        if not self._response_times or not self.config.adaptive_timeout:
            return self.config.request_timeout
            
        sorted_times = sorted(self._response_times)
        idx = int(len(sorted_times) * self.config.timeout_percentile)
        idx = min(idx, len(sorted_times) - 1)
        
        adaptive = sorted_times[idx] * 2.0  # 2x p95
        return max(
            self.config.min_timeout,
            min(adaptive, self.config.max_timeout)
        )
    
    def get_stats(self) -> Dict[str, Any]:
        if not self._response_times:
            return {"samples": 0, "current_timeout": self.config.request_timeout}
            
        sorted_times = sorted(self._response_times)
        return {
            "samples": len(sorted_times),
            "p50": sorted_times[len(sorted_times)//2],
            "p95": sorted_times[int(len(sorted_times)*0.95)],
            "p99": sorted_times[int(len(sorted_times)*0.99)],
            "current_timeout": self.get_timeout()
        }


class LLMTimeoutManager:
    """Combined timeout and circuit breaker for LLM calls."""
    
    def __init__(self, config: Optional[TimeoutConfig] = None):
        self.config = config or TimeoutConfig()
        self.circuit = CircuitBreaker(config)
        self.adaptive = AdaptiveTimeout(config)
        self._total_calls = 0
        self._total_timeouts = 0
        self._total_success = 0
        
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute LLM call with full protection."""
        self._total_calls += 1
        timeout = self.adaptive.get_timeout()
        
        start = time.monotonic()
        try:
            # Circuit breaker wrapper
            result = await self.circuit.call(
                self._with_timeout, func, timeout, *args, **kwargs
            )
            self._total_success += 1
            return result
            
        except asyncio.TimeoutError:
            self._total_timeouts += 1
            await self.circuit._record_failure()
            raise LLMTimeoutError(f"LLM call exceeded {timeout}s")
            
        finally:
            duration = time.monotonic() - start
            await self.adaptive.record_response_time(duration)
    
    async def _with_timeout(self, func: Callable, timeout: float, 
                            *args, **kwargs) -> Any:
        """Execute with timeout."""
        return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "circuit": self.circuit.get_stats(),
            "adaptive": self.adaptive.get_stats(),
            "total_calls": self._total_calls,
            "total_timeouts": self._total_timeouts,
            "total_success": self._total_success,
            "success_rate": self._total_success / max(1, self._total_calls)
        }


class LLMTimeoutError(Exception):
    pass


def with_llm_timeout(config: Optional[TimeoutConfig] = None):
    """Decorator for LLM functions."""
    manager = LLMTimeoutManager(config)
    
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await manager.call(func, *args, **kwargs)
        wrapper._timeout_manager = manager
        return wrapper
    return decorator


__all__ = [
    "LLMTimeoutManager",
    "CircuitBreaker",
    "AdaptiveTimeout", 
    "TimeoutConfig",
    "CircuitBreakerOpen",
    "LLMTimeoutError",
    "with_llm_timeout"
]