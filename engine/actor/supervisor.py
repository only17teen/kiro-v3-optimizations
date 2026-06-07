"""Actor supervisor with hierarchical restart policies."""

import asyncio
import logging
from enum import Enum, auto
from dataclasses import dataclass
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class RestartPolicy(Enum):
    ONE_FOR_ONE = auto()      # Restart only failed actor
    ONE_FOR_ALL = auto()      # Restart all on failure
    REST_FOR_ONE = auto()     # Restart failed and subsequent
    TEMPORARY = auto()        # No restart
    TRANSIENT = auto()        # Restart only on abnormal exit


class ActorState(Enum):
    RUNNING = auto()
    RESTARTING = auto()
    STOPPED = auto()
    FAILED = auto()


@dataclass
class SupervisorConfig:
    max_restarts: int = 5
    restart_window: int = 60  # seconds
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    restart_policy: RestartPolicy = RestartPolicy.ONE_FOR_ONE


class Supervisor:
    """Hierarchical actor supervisor with exponential backoff."""
    
    def __init__(self, config: Optional[SupervisorConfig] = None):
        self.config = config or SupervisorConfig()
        self._actors: Dict[str, Dict] = {}
        self._restart_history: Dict[str, list] = {}
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        
    async def start_actor(self, actor_id: str, factory: Callable,
                         dependencies: Optional[list] = None) -> bool:
        async with self._lock:
            if actor_id in self._actors:
                logger.warning(f"Actor {actor_id} already running")
                return False
                
            try:
                instance = await factory() if asyncio.iscoroutinefunction(factory) else factory()
                self._actors[actor_id] = {
                    "instance": instance,
                    "factory": factory,
                    "state": ActorState.RUNNING,
                    "dependencies": dependencies or [],
                    "started_at": datetime.now()
                }
                self._restart_history[actor_id] = []
                logger.info(f"Started actor {actor_id}")
                return True
            except Exception as e:
                logger.error(f"Failed to start actor {actor_id}: {e}")
                return False
    
    async def stop_actor(self, actor_id: str) -> bool:
        async with self._lock:
            return await self._stop_locked(actor_id)
    
    async def _stop_locked(self, actor_id: str) -> bool:
        if actor_id not in self._actors:
            return False
            
        actor = self._actors[actor_id]
        actor["state"] = ActorState.STOPPED
        
        # Stop dependencies first (reverse order)
        for dep in reversed(actor["dependencies"]):
            if dep in self._actors:
                await self._stop_locked(dep)
        
        # Cleanup instance
        instance = actor["instance"]
        if hasattr(instance, 'stop'):
            if asyncio.iscoroutinefunction(instance.stop):
                await instance.stop()
            else:
                instance.stop()
        
        del self._actors[actor_id]
        logger.info(f"Stopped actor {actor_id}")
        return True
    
    async def handle_failure(self, actor_id: str, exception: Exception) -> bool:
        async with self._lock:
            if actor_id not in self._actors:
                return False
                
            actor = self._actors[actor_id]
            actor["state"] = ActorState.FAILED
            
            # Check restart limits
            now = datetime.now()
            history = self._restart_history.get(actor_id, [])
            history = [t for t in history if now - t < timedelta(seconds=self.config.restart_window)]
            
            if len(history) >= self.config.max_restarts:
                logger.error(f"Actor {actor_id} exceeded max restarts, stopping")
                await self._stop_locked(actor_id)
                return False
            
            # Calculate backoff
            backoff = min(
                self.config.backoff_base * (2 ** len(history)),
                self.config.backoff_max
            )
            
            logger.info(f"Restarting actor {actor_id} in {backoff}s (attempt {len(history)+1})")
            await asyncio.sleep(backoff)
            
            # Apply restart policy
            if self.config.restart_policy == RestartPolicy.ONE_FOR_ALL:
                await self._restart_all()
            elif self.config.restart_policy == RestartPolicy.REST_FOR_ONE:
                await self._restart_from(actor_id)
            else:
                await self._restart_single(actor_id)
            
            history.append(now)
            self._restart_history[actor_id] = history
            return True
    
    async def _restart_single(self, actor_id: str) -> None:
        actor = self._actors[actor_id]
        try:
            instance = await actor["factory"]() if asyncio.iscoroutinefunction(actor["factory"]) else actor["factory"]()
            actor["instance"] = instance
            actor["state"] = ActorState.RUNNING
            logger.info(f"Restarted actor {actor_id}")
        except Exception as e:
            logger.error(f"Failed to restart actor {actor_id}: {e}")
            actor["state"] = ActorState.FAILED
    
    async def _restart_all(self) -> None:
        for actor_id in list(self._actors.keys()):
            await self._restart_single(actor_id)
    
    async def _restart_from(self, start_actor_id: str) -> None:
        restart = False
        for actor_id in list(self._actors.keys()):
            if restart or actor_id == start_actor_id:
                restart = True
                await self._restart_single(actor_id)
    
    async def shutdown(self) -> None:
        async with self._lock:
            for actor_id in list(self._actors.keys()):
                await self._stop_locked(actor_id)
        self._shutdown_event.set()
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "actors": {
                aid: {
                    "state": a["state"].name,
                    "dependencies": a["dependencies"]
                }
                for aid, a in self._actors.items()
            },
            "total": len(self._actors),
            "running": sum(1 for a in self._actors.values() if a["state"] == ActorState.RUNNING)
        }