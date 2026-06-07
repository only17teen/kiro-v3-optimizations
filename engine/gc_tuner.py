"""GC Tuner - Freeze on boot and background collection for stable latency."""

import gc
import os
import sys
import threading
import logging
import time
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@dataclass
class GCTunerConfig:
    """Configuration for GC tuning."""
    freeze_on_boot: bool = True
    freeze_duration: float = 300.0  # 5 minutes
    background_interval: float = 60.0
    generation_thresholds: tuple = (700, 10, 10)  # gen0, gen1, gen2
    max_latency_ms: float = 50.0
    emergency_threshold: float = 0.85  # Memory pressure threshold


class GCTuner:
    """Garbage Collection tuner for stable inference latency."""
    
    def __init__(self, config: Optional[GCTunerConfig] = None):
        self.config = config or GCTunerConfig()
        self._frozen = False
        self._background_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._collection_stats: Dict[str, Any] = {
            "collections": [0, 0, 0],
            "total_pause_ms": 0.0,
            "max_pause_ms": 0.0,
            "avg_pause_ms": 0.0
        }
        self._callbacks: list = []
        
    def freeze_on_boot(self) -> None:
        """Freeze GC during boot to prevent stalls."""
        if not self.config.freeze_on_boot:
            return
            
        logger.info(f"Freezing GC for {self.config.freeze_duration}s")
        self._frozen = True
        
        # Disable automatic collection
        gc.disable()
        
        # Pre-collect before freeze
        gc.collect(2)
        
        # Set thresholds high to prevent triggers
        gc.set_threshold(100000, 100000, 100000)
        
        # Schedule unfreeze
        def unfreeze():
            time.sleep(self.config.freeze_duration)
            self._unfreeze()
            
        threading.Thread(target=unfreeze, daemon=True, name="gc-unfreeze").start()
        logger.info("GC frozen - automatic collection disabled")
    
    def _unfreeze(self) -> None:
        """Unfreeze GC and restore normal operation."""
        self._frozen = False
        gc.enable()
        gc.set_threshold(*self.config.generation_thresholds)
        logger.info("GC unfrozen - normal collection restored")
        
        # Start background collection
        self._start_background_collection()
    
    def _start_background_collection(self) -> None:
        """Start background GC thread."""
        if self._background_thread and self._background_thread.is_alive():
            return
            
        self._stop_event.clear()
        self._background_thread = threading.Thread(
            target=self._background_loop,
            daemon=True,
            name="gc-background"
        )
        self._background_thread.start()
        logger.info("Background GC collection started")
    
    def _background_loop(self) -> None:
        """Background collection loop with latency monitoring."""
        while not self._stop_event.is_set():
            try:
                self._stop_event.wait(self.config.background_interval)
                if self._stop_event.is_set():
                    break
                    
                # Check memory pressure
                if self._get_memory_pressure() > self.config.emergency_threshold:
                    logger.warning("Memory pressure high - emergency collection")
                    self._collect_with_timing(2)
                else:
                    # Gentle generation 0/1 collection
                    self._collect_with_timing(1)
                    
            except Exception as e:
                logger.error(f"Background GC error: {e}")
    
    def _collect_with_timing(self, generation: int) -> float:
        """Collect with pause time measurement."""
        start = time.perf_counter()
        gc.collect(generation)
        pause_ms = (time.perf_counter() - start) * 1000
        
        self._collection_stats["collections"][generation] += 1
        self._collection_stats["total_pause_ms"] += pause_ms
        self._collection_stats["max_pause_ms"] = max(
            self._collection_stats["max_pause_ms"], pause_ms
        )
        total = sum(self._collection_stats["collections"])
        if total > 0:
            self._collection_stats["avg_pause_ms"] = (
                self._collection_stats["total_pause_ms"] / total
            )
        
        # Notify callbacks if pause exceeds threshold
        if pause_ms > self.config.max_latency_ms:
            for callback in self._callbacks:
                try:
                    callback("high_latency", {"pause_ms": pause_ms, "generation": generation})
                except Exception:
                    pass
                    
        return pause_ms
    
    def _get_memory_pressure(self) -> float:
        """Get current memory pressure ratio."""
        try:
            import psutil
            mem = psutil.virtual_memory()
            return mem.percent / 100.0
        except ImportError:
            # Fallback: check RSS vs some estimate
            return 0.5
    
    @contextmanager
    def pause_gc(self):
        """Context manager to temporarily pause GC."""
        was_enabled = gc.isenabled()
        if was_enabled:
            gc.disable()
        try:
            yield
        finally:
            if was_enabled:
                gc.enable()
    
    def force_collect(self, generation: int = 2) -> float:
        """Force collection and return pause time."""
        return self._collect_with_timing(generation)
    
    def register_callback(self, callback: Callable) -> None:
        """Register callback for GC events."""
        self._callbacks.append(callback)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get GC statistics."""
        return {
            "frozen": self._frozen,
            "collections": self._collection_stats["collections"].copy(),
            "total_pause_ms": self._collection_stats["total_pause_ms"],
            "max_pause_ms": self._collection_stats["max_pause_ms"],
            "avg_pause_ms": self._collection_stats["avg_pause_ms"],
            "memory_pressure": self._get_memory_pressure(),
            "thresholds": gc.get_threshold() if hasattr(gc, 'get_threshold') else None
        }
    
    def shutdown(self) -> None:
        """Shutdown background collection."""
        self._stop_event.set()
        if self._background_thread:
            self._background_thread.join(timeout=5.0)
        gc.enable()
        logger.info("GC tuner shutdown")


# Global instance for easy access
_default_tuner: Optional[GCTuner] = None


def get_tuner() -> GCTuner:
    """Get or create default GC tuner."""
    global _default_tuner
    if _default_tuner is None:
        _default_tuner = GCTuner()
    return _default_tuner


def freeze_on_boot() -> None:
    """Convenience function to freeze GC on boot."""
    get_tuner().freeze_on_boot()


def background_collect() -> None:
    """Convenience function to start background collection."""
    get_tuner()._start_background_collection()


__all__ = [
    "GCTuner",
    "GCTunerConfig", 
    "freeze_on_boot",
    "background_collect",
    "get_tuner"
]