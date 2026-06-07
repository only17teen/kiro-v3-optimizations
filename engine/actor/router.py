"""Actor Router with DashMap-inspired concurrent routing."""

import asyncio
import hashlib
from enum import Enum, auto
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


class RouteStrategy(Enum):
    ROUND_ROBIN = auto()
    HASH_RING = auto()
    LEAST_LOADED = auto()
    BROADCAST = auto()


@dataclass
class RouteEntry:
    actor_id: str
    handler: Callable
    load: int = 0
    healthy: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class ActorRouter:
    """Concurrent-safe actor router using sharded locks."""
    
    def __init__(self, strategy: RouteStrategy = RouteStrategy.HASH_RING, 
                 shard_count: int = 16):
        self.strategy = strategy
        self.shard_count = shard_count
        self._routes: Dict[str, RouteEntry] = {}
        self._shard_locks = [asyncio.Lock() for _ in range(shard_count)]
        self._rr_counter = 0
        self._rr_lock = asyncio.Lock()
        
    def _get_shard(self, key: str) -> int:
        return int(hashlib.md5(key.encode()).hexdigest(), 16) % self.shard_count
    
    async def register(self, actor_id: str, handler: Callable, 
                      metadata: Optional[Dict] = None) -> None:
        shard = self._get_shard(actor_id)
        async with self._shard_locks[shard]:
            self._routes[actor_id] = RouteEntry(
                actor_id=actor_id,
                handler=handler,
                metadata=metadata or {}
            )
        logger.info(f"Registered actor {actor_id}")
    
    async def unregister(self, actor_id: str) -> None:
        shard = self._get_shard(actor_id)
        async with self._shard_locks[shard]:
            if actor_id in self._routes:
                del self._routes[actor_id]
    
    async def route(self, message: Any, key: Optional[str] = None) -> Any:
        target = await self._select_target(key or str(id(message)))
        if not target or not target.healthy:
            raise RuntimeError(f"No healthy route for {key}")
        
        async with self._rr_lock:
            target.load += 1
        
        try:
            if asyncio.iscoroutinefunction(target.handler):
                return await target.handler(message)
            return target.handler(message)
        finally:
            async with self._rr_lock:
                target.load -= 1
    
    async def _select_target(self, key: str) -> Optional[RouteEntry]:
        healthy = [r for r in self._routes.values() if r.healthy]
        if not healthy:
            return None
            
        if self.strategy == RouteStrategy.ROUND_ROBIN:
            async with self._rr_lock:
                idx = self._rr_counter % len(healthy)
                self._rr_counter += 1
                return healthy[idx]
                
        elif self.strategy == RouteStrategy.HASH_RING:
            hash_val = int(hashlib.md5(key.encode()).hexdigest(), 16)
            idx = hash_val % len(healthy)
            return healthy[idx]
            
        elif self.strategy == RouteStrategy.LEAST_LOADED:
            return min(healthy, key=lambda r: r.load)
            
        elif self.strategy == RouteStrategy.BROADCAST:
            return healthy[0]  # First actor handles broadcast coordination
            
        return healthy[0]
    
    async def get_stats(self) -> Dict[str, Any]:
        return {
            "total_actors": len(self._routes),
            "healthy_actors": sum(1 for r in self._routes.values() if r.healthy),
            "strategy": self.strategy.name,
            "shard_count": self.shard_count
        }