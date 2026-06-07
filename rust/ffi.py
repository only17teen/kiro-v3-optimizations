"""Python bindings for Rust ActorState FFI."""

import ctypes
import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ActorStatus(Enum):
    RUNNING = 0
    PAUSED = 1
    STOPPED = 2
    FAILED = 3


@dataclass
class ActorState:
    id: str
    status: ActorStatus
    message_count: int = 0
    error_count: int = 0


class RustActorRegistry:
    """Python wrapper for Rust ActorRegistry FFI."""
    
    def __init__(self, lib_path: Optional[str] = None, shard_count: int = 16):
        self._lib = self._load_library(lib_path)
        self._shard_count = shard_count
        self._initialized = False
        
    def _load_library(self, lib_path: Optional[str]) -> ctypes.CDLL:
        """Load Rust shared library."""
        if lib_path is None:
            # Search common paths
            candidates = [
                "./target/release/libkiro_actor.so",
                "./target/release/libkiro_actor.dylib",
                "./target/release/libkiro_actor.dll",
                "../target/release/libkiro_actor.so",
                "../target/release/libkiro_actor.dylib",
            ]
            for path in candidates:
                if os.path.exists(path):
                    lib_path = path
                    break
        
        if lib_path is None:
            raise RuntimeError("Rust library not found. Build with: cd rust && cargo build --release")
        
        lib = ctypes.CDLL(lib_path)
        
        # Configure function signatures
        lib.kiro_init_registry.argtypes = [ctypes.c_int]
        lib.kiro_init_registry.restype = ctypes.c_int
        
        lib.kiro_register_actor.argtypes = [ctypes.c_char_p, ctypes.c_int]
        lib.kiro_register_actor.restype = ctypes.c_int
        
        lib.kiro_actor_count.argtypes = []
        lib.kiro_actor_count.restype = ctypes.c_int
        
        lib.kiro_cleanup.argtypes = []
        lib.kiro_cleanup.restype = None
        
        return lib
    
    def initialize(self) -> None:
        """Initialize Rust registry."""
        result = self._lib.kiro_init_registry(self._shard_count)
        if result != 0:
            raise RuntimeError(f"Failed to initialize registry: {result}")
        self._initialized = True
        logger.info(f"Rust registry initialized with {self._shard_count} shards")
    
    def register(self, actor_id: str, status: ActorStatus = ActorStatus.RUNNING) -> bool:
        """Register an actor in Rust registry."""
        if not self._initialized:
            self.initialize()
        
        actor_id_bytes = actor_id.encode('utf-8')
        result = self._lib.kiro_register_actor(actor_id_bytes, status.value)
        return result == 0
    
    def count(self) -> int:
        """Get total actor count."""
        if not self._initialized:
            return 0
        return self._lib.kiro_actor_count()
    
    def cleanup(self) -> None:
        """Cleanup Rust registry."""
        self._lib.kiro_cleanup()
        self._initialized = False
    
    def __enter__(self):
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()


# Pure Python fallback when Rust is unavailable
class PythonActorRegistry:
    """Pure Python fallback implementation."""
    
    def __init__(self, shard_count: int = 16):
        import threading
        self._shards = [{} for _ in range(shard_count)]
        self._locks = [threading.RLock() for _ in range(shard_count)]
        self._shard_mask = shard_count - 1
        self._count = 0
        
    def _get_shard(self, actor_id: str) -> int:
        return hash(actor_id) & self._shard_mask
    
    def register(self, actor_id: str, status: ActorStatus = ActorStatus.RUNNING) -> bool:
        shard_idx = self._get_shard(actor_id)
        with self._locks[shard_idx]:
            if actor_id in self._shards[shard_idx]:
                return False
            self._shards[shard_idx][actor_id] = ActorState(
                id=actor_id, status=status
            )
            self._count += 1
            return True
    
    def count(self) -> int:
        return self._count
    
    def cleanup(self) -> None:
        for shard in self._shards:
            shard.clear()
        self._count = 0


def create_registry(shard_count: int = 16, use_rust: bool = True) -> Any:
    """Factory to create best available registry."""
    if use_rust:
        try:
            return RustActorRegistry(shard_count=shard_count)
        except Exception as e:
            logger.warning(f"Rust registry unavailable: {e}, using Python fallback")
    return PythonActorRegistry(shard_count=shard_count)


__all__ = [
    "RustActorRegistry",
    "PythonActorRegistry", 
    "ActorState",
    "ActorStatus",
    "create_registry"
]