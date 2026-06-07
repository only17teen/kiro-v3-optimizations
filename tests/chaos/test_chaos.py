"""Chaos Monkey tests for resilience validation."""

import asyncio
import random
import logging
import time
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger(__name__)


class FailureType(Enum):
    """Types of failures to inject."""
    DELAY = auto()           # Network delay
    ERROR = auto()           # Exception
    TIMEOUT = auto()         # Timeout
    MEMORY_PRESSURE = auto()  # High memory usage
    CPU_SPIKE = auto()       # CPU intensive operation
    KILL = auto()            # Process kill simulation
    PARTITION = auto()         # Network partition
    CORRUPTION = auto()      # Data corruption


@dataclass
class ChaosConfig:
    """Chaos testing configuration."""
    enabled: bool = True
    failure_rate: float = 0.1  # 10% failure injection
    max_delay_ms: float = 5000.0
    min_delay_ms: float = 100.0
    error_types: List[type] = field(default_factory=lambda: [RuntimeError, ValueError])
    memory_spike_mb: int = 100
    cpu_spike_duration_ms: float = 100.0
    target_services: List[str] = field(default_factory=lambda: ["all"])
    safe_hours: tuple = (2, 6)  # Don't chaos between 2-6 AM


@dataclass
class ChaosEvent:
    """Recorded chaos event."""
    timestamp: float
    failure_type: FailureType
    target: str
    duration_ms: float
    recovered: bool = False
    error_message: Optional[str] = None


class ChaosMonkey:
    """Chaos engineering test harness."""
    
    def __init__(self, config: Optional[ChaosConfig] = None):
        self.config = config or ChaosConfig()
        self._events: List[ChaosEvent] = []
        self._running = False
        self._targets: Dict[str, Callable] = {}
        self._lock = asyncio.Lock()
        
    def register_target(self, name: str, target: Callable) -> None:
        """Register a service for chaos testing."""
        self._targets[name] = target
        logger.info(f"Registered chaos target: {name}")
    
    async def start(self, interval_seconds: float = 60.0) -> None:
        """Start chaos monkey."""
        if not self.config.enabled:
            logger.info("Chaos monkey disabled")
            return
        
        self._running = True
        logger.info("Chaos monkey started")
        
        while self._running:
            try:
                await self._inject_random_failure()
                await asyncio.sleep(interval_seconds)
            except Exception as e:
                logger.error(f"Chaos injection error: {e}")
                await asyncio.sleep(1.0)
    
    def stop(self) -> None:
        """Stop chaos monkey."""
        self._running = False
        logger.info("Chaos monkey stopped")
    
    async def _inject_random_failure(self) -> None:
        """Inject a random failure."""
        if random.random() > self.config.failure_rate:
            return
        
        # Check safe hours
        current_hour = time.localtime().tm_hour
        if self.config.safe_hours[0] <= current_hour <= self.config.safe_hours[1]:
            return
        
        failure_type = random.choice(list(FailureType))
        target = random.choice(list(self._targets.keys())) if self._targets else "system"
        
        start_time = time.monotonic()
        
        try:
            await self._apply_failure(failure_type, target)
            duration_ms = (time.monotonic() - start_time) * 1000
            
            event = ChaosEvent(
                timestamp=time.time(),
                failure_type=failure_type,
                target=target,
                duration_ms=duration_ms,
                recovered=True
            )
            self._events.append(event)
            logger.info(f"Chaos: {failure_type.name} on {target} ({duration_ms:.0f}ms)")
            
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            event = ChaosEvent(
                timestamp=time.time(),
                failure_type=failure_type,
                target=target,
                duration_ms=duration_ms,
                recovered=False,
                error_message=str(e)
            )
            self._events.append(event)
            logger.error(f"Chaos failed: {failure_type.name} on {target}: {e}")
    
    async def _apply_failure(self, failure_type: FailureType, target: str) -> None:
        """Apply specific failure type."""
        if failure_type == FailureType.DELAY:
            delay = random.uniform(self.config.min_delay_ms, self.config.max_delay_ms)
            await asyncio.sleep(delay / 1000.0)
            
        elif failure_type == FailureType.ERROR:
            error_type = random.choice(self.config.error_types)
            raise error_type(f"Chaos injected error in {target}")
            
        elif failure_type == FailureType.TIMEOUT:
            await asyncio.sleep(self.config.max_delay_ms / 1000.0 + 1.0)
            
        elif failure_type == FailureType.MEMORY_PRESSURE:
            # Allocate memory spike
            data = [0] * (self.config.memory_spike_mb * 1024 * 1024 // 8)
            await asyncio.sleep(0.1)
            del data
            
        elif failure_type == FailureType.CPU_SPIKE:
            # CPU-intensive calculation
            start = time.monotonic()
            while (time.monotonic() - start) * 1000 < self.config.cpu_spike_duration_ms:
                _ = sum(i * i for i in range(10000))
            await asyncio.sleep(0)
            
        elif failure_type == FailureType.KILL:
            # Simulate service restart
            if target in self._targets:
                # Call target's stop method if available
                t = self._targets[target]
                if hasattr(t, 'stop'):
                    if asyncio.iscoroutinefunction(t.stop):
                        await t.stop()
                    else:
                        t.stop()
                    await asyncio.sleep(1.0)
                    if hasattr(t, 'start'):
                        if asyncio.iscoroutinefunction(t.start):
                            await t.start()
                        else:
                            t.start()
                            
        elif failure_type == FailureType.PARTITION:
            # Simulate network partition by delaying all traffic
            await asyncio.sleep(random.uniform(1.0, 5.0))
            
        elif failure_type == FailureType.CORRUPTION:
            # Return corrupted data
            pass  # Handled by test assertions
    
    async def run_test_scenario(self, scenario: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run a predefined chaos scenario."""
        results = []
        
        for step in scenario:
            failure_type = FailureType[step.get("type", "DELAY")]
            target = step.get("target", "system")
            duration = step.get("duration_ms", 1000)
            
            start = time.monotonic()
            try:
                await self._apply_failure(failure_type, target)
                results.append({
                    "step": step,
                    "success": True,
                    "duration_ms": (time.monotonic() - start) * 1000
                })
            except Exception as e:
                results.append({
                    "step": step,
                    "success": False,
                    "error": str(e),
                    "duration_ms": (time.monotonic() - start) * 1000
                })
            
            # Wait between steps
            await asyncio.sleep(step.get("wait_after_ms", 1000) / 1000.0)
        
        return {
            "total_steps": len(scenario),
            "successful": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
            "results": results
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get chaos testing statistics."""
        if not self._events:
            return {"events": 0}
        
        by_type = {}
        for event in self._events:
            ft = event.failure_type.name
            if ft not in by_type:
                by_type[ft] = {"count": 0, "recovered": 0, "total_ms": 0}
            by_type[ft]["count"] += 1
            if event.recovered:
                by_type[ft]["recovered"] += 1
            by_type[ft]["total_ms"] += event.duration_ms
        
        return {
            "total_events": len(self._events),
            "recovery_rate": sum(1 for e in self._events if e.recovered) / len(self._events),
            "by_type": {
                ft: {
                    "count": stats["count"],
                    "recovery_rate": stats["recovered"] / stats["count"],
                    "avg_duration_ms": stats["total_ms"] / stats["count"]
                }
                for ft, stats in by_type.items()
            },
            "recent_events": [
                {
                    "type": e.failure_type.name,
                    "target": e.target,
                    "recovered": e.recovered,
                    "timestamp": e.timestamp
                }
                for e in self._events[-10:]
            ]
        }


# Common test scenarios
SCENARIO_GRACEFUL_DEGRADATION = [
    {"type": "DELAY", "target": "api", "duration_ms": 500, "wait_after_ms": 1000},
    {"type": "DELAY", "target": "api", "duration_ms": 1000, "wait_after_ms": 1000},
    {"type": "DELAY", "target": "api", "duration_ms": 2000, "wait_after_ms": 1000},
    {"type": "ERROR", "target": "api", "duration_ms": 0, "wait_after_ms": 2000},
    {"type": "RECOVER", "target": "api", "duration_ms": 0, "wait_after_ms": 5000}
]

SCENARIO_CASCADE_FAILURE = [
    {"type": "KILL", "target": "service_a", "duration_ms": 0, "wait_after_ms": 2000},
    {"type": "KILL", "target": "service_b", "duration_ms": 0, "wait_after_ms": 2000},
    {"type": "PARTITION", "target": "database", "duration_ms": 5000, "wait_after_ms": 5000}
]

SCENARIO_RESOURCE_EXHAUSTION = [
    {"type": "MEMORY_PRESSURE", "target": "worker", "duration_ms": 5000, "wait_after_ms": 1000},
    {"type": "CPU_SPIKE", "target": "worker", "duration_ms": 3000, "wait_after_ms": 1000},
    {"type": "MEMORY_PRESSURE", "target": "worker", "duration_ms": 10000, "wait_after_ms": 5000}
]


__all__ = [
    "ChaosMonkey",
    "ChaosConfig",
    "ChaosEvent",
    "FailureType",
    "SCENARIO_GRACEFUL_DEGRADATION",
    "SCENARIO_CASCADE_FAILURE",
    "SCENARIO_RESOURCE_EXHAUSTION"
]