"""Lock-free mailbox with backpressure and priority queues."""

import asyncio
import heapq
from dataclasses import dataclass, field
from typing import Any, Optional, Callable, List
from enum import Enum, auto
import time


class Priority(Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    BACKGROUND = 4


@dataclass(order=True)
class QueuedMessage:
    priority: int
    timestamp: float = field(compare=True)
    sequence: int = field(compare=True)
    message: Any = field(compare=False)
    future: Optional[asyncio.Future] = field(compare=False, default=None)


@dataclass
class MailboxConfig:
    max_size: int = 10000
    backpressure_threshold: float = 0.8
    priority_weights: dict = field(default_factory=lambda: {
        Priority.CRITICAL: 1.0,
        Priority.HIGH: 0.5,
        Priority.NORMAL: 0.2,
        Priority.LOW: 0.1,
        Priority.BACKGROUND: 0.05
    })


class Mailbox:
    """Priority mailbox with async backpressure."""
    
    def __init__(self, config: Optional[MailboxConfig] = None):
        self.config = config or MailboxConfig()
        self._queue: List[QueuedMessage] = []
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)
        self._sequence = 0
        self._dropped_count = 0
        self._processed_count = 0
        
    async def put(self, message: Any, priority: Priority = Priority.NORMAL,
                  timeout: Optional[float] = None) -> Optional[asyncio.Future]:
        async with self._lock:
            if len(self._queue) >= self.config.max_size:
                if self._should_drop(priority):
                    self._dropped_count += 1
                    return None
                    
            future = asyncio.Future()
            self._sequence += 1
            item = QueuedMessage(
                priority=priority.value,
                timestamp=time.monotonic(),
                sequence=self._sequence,
                message=message,
                future=future
            )
            heapq.heappush(self._queue, item)
            self._not_empty.notify()
            return future
    
    def _should_drop(self, priority: Priority) -> bool:
        # Drop lowest priority first
        if priority == Priority.BACKGROUND:
            return True
        # Check if we can drop existing low-priority messages
        if self._queue and self._queue[-1].priority > priority.value:
            heapq.heappop(self._queue)
            self._dropped_count += 1
            return False
        return True
    
    async def get(self, timeout: Optional[float] = None) -> Optional[Any]:
        async with self._not_empty:
            if not self._queue:
                try:
                    await asyncio.wait_for(self._not_empty.wait(), timeout)
                except asyncio.TimeoutError:
                    return None
                    
            if self._queue:
                item = heapq.heappop(self._queue)
                self._processed_count += 1
                if item.future and not item.future.done():
                    item.future.set_result(True)
                return item.message
            return None
    
    async def get_batch(self, max_size: int = 100, 
                        max_wait: float = 0.001) -> List[Any]:
        batch = []
        deadline = time.monotonic() + max_wait
        
        while len(batch) < max_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
                
            msg = await self.get(timeout=remaining)
            if msg is None:
                break
            batch.append(msg)
            
        return batch
    
    def get_pressure(self) -> float:
        return len(self._queue) / self.config.max_size
    
    def should_apply_backpressure(self) -> bool:
        return self.get_pressure() > self.config.backpressure_threshold
    
    def get_stats(self) -> dict:
        return {
            "queue_size": len(self._queue),
            "dropped": self._dropped_count,
            "processed": self._processed_count,
            "pressure": self.get_pressure()
        }