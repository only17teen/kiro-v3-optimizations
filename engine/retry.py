"""Retry logic with full jitter and status code discrimination."""

import asyncio
import random
import logging
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional, Callable, Any, Dict, Set, Type
from functools import wraps

logger = logging.getLogger(__name__)


class RetryableStatus(Enum):
    """HTTP status code categories."""
    RETRYABLE = {408, 429, 500, 502, 503, 504}
    NON_RETRYABLE = {400, 401, 403, 404, 405, 422}
    TIMEOUT = {408, 504}


class RetryStrategy(Enum):
    FIXED = auto()
    LINEAR = auto()
    EXPONENTIAL = auto()
    FULL_JITTER = auto()
    DECORRELATED_JITTER = auto()


@dataclass
class RetryConfig:
    """Retry configuration."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: RetryStrategy = RetryStrategy.FULL_JITTER
    retryable_statuses: Set[int] = None
    non_retryable_statuses: Set[int] = None
    retryable_exceptions: tuple = (Exception,)
    timeout_multiplier: float = 2.0
    
    def __post_init__(self):
        if self.retryable_statuses is None:
            self.retryable_statuses = RetryableStatus.RETRYABLE.value
        if self.non_retryable_statuses is None:
            self.non_retryable_statuses = RetryableStatus.NON_RETRYABLE.value


class RetryState:
    """Track retry state per operation."""
    
    def __init__(self, config: RetryConfig):
        self.config = config
        self.attempt = 0
        self.total_delay = 0.0
        self.last_exception: Optional[Exception] = None
        self.status_code: Optional[int] = None
        
    def should_retry(self, exception: Exception, status_code: Optional[int] = None) -> bool:
        if self.attempt >= self.config.max_retries:
            return False
            
        # Check status code discrimination
        if status_code is not None:
            self.status_code = status_code
            if status_code in self.config.non_retryable_statuses:
                return False
            if status_code in self.config.retryable_statuses:
                return True
                
        # Check exception type
        if isinstance(exception, self.config.retryable_exceptions):
            return True
            
        return False
    
    def calculate_delay(self) -> float:
        """Calculate next delay with full jitter."""
        self.attempt += 1
        
        if self.config.strategy == RetryStrategy.FIXED:
            delay = self.config.base_delay
        elif self.config.strategy == RetryStrategy.LINEAR:
            delay = self.config.base_delay * self.attempt
        elif self.config.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.config.base_delay * (2 ** (self.attempt - 1))
        elif self.config.strategy == RetryStrategy.FULL_JITTER:
            # Exponential backoff with full jitter
            exp_delay = self.config.base_delay * (2 ** (self.attempt - 1))
            delay = random.uniform(0, min(exp_delay, self.config.max_delay))
        elif self.config.strategy == RetryStrategy.DECORRELATED_JITTER:
            # AWS-style decorrelated jitter
            if self.attempt == 1:
                delay = self.config.base_delay
            else:
                delay = random.uniform(
                    self.config.base_delay,
                    self.last_delay * 3 if hasattr(self, 'last_delay') else self.config.base_delay * 2
                )
        else:
            delay = self.config.base_delay
        
        delay = min(delay, self.config.max_delay)
        self.last_delay = delay
        self.total_delay += delay
        return delay
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "attempt": self.attempt,
            "total_delay": self.total_delay,
            "last_status": self.status_code,
            "max_retries": self.config.max_retries
        }


class RetryManager:
    """Central retry manager with status code discrimination."""
    
    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
        self._total_success = 0
        self._total_failures = 0
        self._total_retries = 0
        self._status_histogram: Dict[int, int] = {}
        
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute with retry logic."""
        state = RetryState(self.config)
        
        while True:
            try:
                result = await func(*args, **kwargs)
                self._total_success += 1
                return result
                
            except Exception as e:
                # Extract status code if available
                status_code = self._extract_status_code(e)
                
                if not state.should_retry(e, status_code):
                    self._total_failures += 1
                    if status_code:
                        self._status_histogram[status_code] = self._status_histogram.get(status_code, 0) + 1
                    raise
                
                self._total_retries += 1
                if status_code:
                    self._status_histogram[status_code] = self._status_histogram.get(status_code, 0) + 1
                
                delay = state.calculate_delay()
                logger.warning(
                    f"Retry {state.attempt}/{self.config.max_retries} "
                    f"after {status_code or e.__class__.__name__}, "
                    f"delaying {delay:.2f}s"
                )
                await asyncio.sleep(delay)
    
    def _extract_status_code(self, exception: Exception) -> Optional[int]:
        """Extract HTTP status code from exception."""
        # Common patterns
        if hasattr(exception, 'status'):
            return exception.status
        if hasattr(exception, 'status_code'):
            return exception.status_code
        if hasattr(exception, 'response') and hasattr(exception.response, 'status_code'):
            return exception.response.status_code
        # aiohttp
        if hasattr(exception, 'status'):
            return exception.status
        # httpx
        if hasattr(exception, 'response') and exception.response:
            if hasattr(exception.response, 'status_code'):
                return exception.response.status_code
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "success": self._total_success,
            "failures": self._total_failures,
            "retries": self._total_retries,
            "status_histogram": self._status_histogram.copy()
        }


def with_retry(config: Optional[RetryConfig] = None):
    """Decorator for retry logic."""
    manager = RetryManager(config)
    
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            return await manager.execute(func, *args, **kwargs)
        wrapper._retry_manager = manager
        return wrapper
    return decorator


class RetryableHTTPError(Exception):
    """Exception with status code for retry discrimination."""
    
    def __init__(self, message: str, status_code: int, 
                 retryable: Optional[bool] = None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        
    def is_retryable(self) -> bool:
        if self.retryable is not None:
            return self.retryable
        return self.status_code in RetryableStatus.RETRYABLE.value


__all__ = [
    "RetryManager",
    "RetryConfig",
    "RetryState",
    "RetryStrategy",
    "RetryableStatus",
    "RetryableHTTPError",
    "with_retry"
]