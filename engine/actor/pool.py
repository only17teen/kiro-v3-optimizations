"""Pre-allocated message pool for zero-allocation hot path."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
from enum import Enum, auto


class MessageType(Enum):
    TELL = auto()
    ASK = auto()
    BROADCAST = auto()
    SYSTEM = auto()


@dataclass
class ActorMessage:
    """Pre-allocated message structure."""
    msg_type: MessageType
    payload: Any
    sender: Optional[str] = None
    recipient: Optional[str] = None
    timestamp: float = field(default_factory=time.monotonic)
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    _dirty: bool = field(default=False, repr=False)
    
    def reset(self) -> None:
        """Reset for reuse from pool."""
        self.msg_type = MessageType.TELL
        self.payload = None
        self.sender = None
        self.recipient = None
        self.timestamp = time.monotonic()
        self.correlation_id = None
        self.metadata.clear()
        self._dirty = False
    
    def mark_dirty(self) -> None:
        self._dirty = True
    
    @property
    def is_dirty(self) -> bool:
        return self._dirty


class MessagePool:
    """Pre-allocated message pool with lock-free borrowing."""
    
    def __init__(self, initial_size: int = 10000, 
                 max_size: int = 100000,
                 growth_factor: float = 2.0):
        self.initial_size = initial_size
        self.max_size = max_size
        self.growth_factor = growth_factor
        self._pool: List[ActorMessage] = []
        self._available: asyncio.Queue = asyncio.Queue()
        self._total_created = 0
        self._total_borrowed = 0
        self._total_returned = 0
        self._lock = asyncio.Lock()
        
    async def initialize(self) -> None:
        """Pre-allocate initial pool."""
        for _ in range(self.initial_size):
            msg = ActorMessage(msg_type=MessageType.TELL, payload=None)
            self._pool.append(msg)
            await self._available.put(msg)
        self._total_created = self.initial_size
        
    async def borrow(self, timeout: Optional[float] = None) -> Optional[ActorMessage]:
        """Borrow a message from the pool."""
        try:
            if timeout:
                msg = await asyncio.wait_for(self._available.get(), timeout)
            else:
                msg = await self._available.get()
            msg.reset()
            self._total_borrowed += 1
            return msg
        except asyncio.TimeoutError:
            # Try to grow pool
            async with self._lock:
                if self._total_created < self.max_size:
                    new_size = min(
                        int(self._total_created * (self.growth_factor - 1)),
                        self.max_size - self._total_created
                    )
                    for _ in range(max(1, new_size)):
                        msg = ActorMessage(msg_type=MessageType.TELL, payload=None)
                        self._pool.append(msg)
                        await self._available.put(msg)
                    self._total_created += new_size
                    
            # Retry
            try:
                msg = await asyncio.wait_for(self._available.get(), 0.1)
                msg.reset()
                self._total_borrowed += 1
                return msg
            except asyncio.TimeoutError:
                return None
    
    async def return_message(self, msg: ActorMessage) -> None:
        """Return a message to the pool."""
        if msg in self._pool:
            msg.reset()
            await self._available.put(msg)
            self._total_returned += 1
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_created": self._total_created,
            "total_borrowed": self._total_borrowed,
            "total_returned": self._total_returned,
            "available": self._available.qsize(),
            "utilization": (self._total_borrowed - self._total_returned) / max(1, self._total_created)
        }