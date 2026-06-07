"""Actor system with typed references and lifecycle management."""

import asyncio
import logging
from typing import Dict, Any, Optional, Callable, TypeVar, Generic
from dataclasses import dataclass
from .router import ActorRouter, RouteStrategy
from .mailbox import Mailbox, MailboxConfig, Priority
from .supervisor import Supervisor, SupervisorConfig, RestartPolicy

logger = logging.getLogger(__name__)
T = TypeVar('T')


@dataclass
class ActorRef(Generic[T]):
    """Typed reference to an actor."""
    actor_id: str
    system: 'ActorSystem'
    
    async def tell(self, message: T, priority: Priority = Priority.NORMAL) -> bool:
        return await self.system.send(self.actor_id, message, priority)
    
    async def ask(self, message: T, timeout: float = 30.0,
                 priority: Priority = Priority.NORMAL) -> Optional[Any]:
        return await self.system.ask(self.actor_id, message, timeout, priority)
    
    async def stop(self) -> bool:
        return await self.system.stop_actor(self.actor_id)


class ActorSystem:
    """Main actor system with router, mailbox, and supervisor."""
    
    def __init__(self, 
                 router_strategy: RouteStrategy = RouteStrategy.HASH_RING,
                 mailbox_config: Optional[MailboxConfig] = None,
                 supervisor_config: Optional[SupervisorConfig] = None):
        self.router = ActorRouter(strategy=router_strategy)
        self.mailbox = Mailbox(config=mailbox_config)
        self.supervisor = Supervisor(config=supervisor_config)
        self._actors: Dict[str, Dict] = {}
        self._running = False
        self._tasks: set = set()
        
    async def start(self) -> None:
        self._running = True
        # Start mailbox processor
        task = asyncio.create_task(self._process_mailbox())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info("Actor system started")
    
    async def stop(self) -> None:
        self._running = False
        await self.supervisor.shutdown()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Actor system stopped")
    
    async def spawn(self, actor_id: str, handler: Callable,
                   dependencies: Optional[list] = None,
                   metadata: Optional[Dict] = None) -> ActorRef:
        """Spawn a new actor."""
        await self.router.register(actor_id, handler, metadata)
        
        async def factory():
            return handler
            
        await self.supervisor.start_actor(actor_id, factory, dependencies)
        
        self._actors[actor_id] = {
            "handler": handler,
            "metadata": metadata or {}
        }
        
        return ActorRef(actor_id=actor_id, system=self)
    
    async def send(self, actor_id: str, message: Any, 
                   priority: Priority = Priority.NORMAL) -> bool:
        """Fire-and-forget message."""
        future = await self.mailbox.put(
            {"type": "tell", "actor_id": actor_id, "message": message},
            priority=priority
        )
        return future is not None
    
    async def ask(self, actor_id: str, message: Any,
                  timeout: float = 30.0,
                  priority: Priority = Priority.NORMAL) -> Optional[Any]:
        """Request-response pattern."""
        response_future = asyncio.Future()
        
        await self.mailbox.put(
            {
                "type": "ask",
                "actor_id": actor_id,
                "message": message,
                "response_future": response_future
            },
            priority=priority
        )
        
        try:
            return await asyncio.wait_for(response_future, timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Ask timeout for actor {actor_id}")
            return None
    
    async def stop_actor(self, actor_id: str) -> bool:
        await self.router.unregister(actor_id)
        return await self.supervisor.stop_actor(actor_id)
    
    async def _process_mailbox(self) -> None:
        while self._running:
            try:
                batch = await self.mailbox.get_batch(max_size=100, max_wait=0.001)
                for msg in batch:
                    await self._handle_message(msg)
            except Exception as e:
                logger.error(f"Mailbox processing error: {e}")
                await asyncio.sleep(0.001)
    
    async def _handle_message(self, msg: Dict) -> None:
        actor_id = msg["actor_id"]
        message = msg["message"]
        msg_type = msg.get("type", "tell")
        
        try:
            result = await self.router.route(message, key=actor_id)
            
            if msg_type == "ask" and "response_future" in msg:
                future = msg["response_future"]
                if not future.done():
                    future.set_result(result)
                    
        except Exception as e:
            logger.error(f"Error handling message for {actor_id}: {e}")
            await self.supervisor.handle_failure(actor_id, e)
            
            if msg_type == "ask" and "response_future" in msg:
                future = msg["response_future"]
                if not future.done():
                    future.set_exception(e)
    
    def get_stats(self) -> Dict[str, Any]:
        return {
            "router": self.router.get_stats(),
            "mailbox": self.mailbox.get_stats(),
            "supervisor": self.supervisor.get_stats()
        }