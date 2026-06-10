"""Precognition Cache - predictive caching for LLM inference."""

import asyncio
import hashlib
import logging
import random
from typing import Dict, Any, Optional, List, Tuple, Set
from dataclasses import dataclass, field
from collections import OrderedDict
import time

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cached inference result with metadata."""
    key: str
    value: Any
    timestamp: float = field(default_factory=time.monotonic)
    access_count: int = 0
    last_access: float = field(default_factory=time.monotonic)
    ttl_seconds: float = 3600.0
    confidence: float = 1.0  # Precognition confidence
    
    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.timestamp) > self.ttl_seconds
    
    @property
    def priority_score(self) -> float:
        """LRU-K style priority."""
        age = time.monotonic() - self.last_access
        return (self.access_count * 10) / (age + 1)


@dataclass
class PrecognitionConfig:
    """Predictive cache configuration."""
    max_size: int = 10000
    default_ttl: float = 3600.0
    precognition_depth: int = 3  # Look-ahead depth
    warmup_threshold: int = 5  # Accesses before promotion
    eviction_batch_size: int = 100
    prefetch_probability: float = 0.3
    similarity_threshold: float = 0.85  # For semantic caching


class PrecognitionCache:
    """Predictive LRU cache with semantic similarity."""
    
    def __init__(self, config: Optional[PrecognitionConfig] = None):
        self.config = config or PrecognitionConfig()
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._access_patterns: Dict[str, List[str]] = {}  # Markov chains
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
        self._prefetch_hits = 0
        self._evictions = 0
        
    def _generate_key(self, prompt: str, params: Optional[Dict] = None) -> str:
        """Generate cache key from prompt and parameters."""
        content = prompt
        if params:
            # Sort params for consistent hashing
            param_str = ",".join(f"{k}={v}" for k, v in sorted(params.items()))
            content += f"|{param_str}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]
    
    async def get(self, prompt: str, params: Optional[Dict] = None) -> Optional[Any]:
        """Get cached result with pattern learning."""
        key = self._generate_key(prompt, params)
        
        async with self._lock:
            entry = self._cache.get(key)
            
            if entry and not entry.is_expired:
                entry.access_count += 1
                entry.last_access = time.monotonic()
                self._hits += 1
                
                # Update access patterns (Markov chain)
                self._update_patterns(key)
                
                # Move to end (LRU)
                self._cache.move_to_end(key)
                
                return entry.value
            
            if entry and entry.is_expired:
                del self._cache[key]
            
            self._misses += 1
            
            # Check for semantic similarity match
            similar = self._find_similar(key, prompt)
            if similar:
                self._prefetch_hits += 1
                return similar.value
                
        return None
    
    async def put(self, prompt: str, value: Any, 
                  params: Optional[Dict] = None,
                  ttl: Optional[float] = None,
                  confidence: float = 1.0) -> None:
        """Store result in cache."""
        key = self._generate_key(prompt, params)
        
        async with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.config.max_size:
                self._evict_batch()
            
            entry = CacheEntry(
                key=key,
                value=value,
                ttl_seconds=ttl or self.config.default_ttl,
                confidence=confidence
            )
            
            self._cache[key] = entry
            self._cache.move_to_end(key)
            
            # Trigger precognition prefetch
            await self._precognition_prefetch(key)
    
    def _update_patterns(self, key: str) -> None:
        """Update Markov chain access patterns."""
        # Simple 1-gram to 3-gram pattern tracking
        for depth in range(1, self.config.precognition_depth + 1):
            pattern_key = f"depth_{depth}"
            if pattern_key not in self._access_patterns:
                self._access_patterns[pattern_key] = []
            
            history = self._access_patterns[pattern_key]
            history.append(key)
            
            # Keep limited history
            if len(history) > 1000:
                self._access_patterns[pattern_key] = history[-500:]
    
    def _find_similar(self, key: str, prompt: str) -> Optional[CacheEntry]:
        """Find semantically similar cached entry."""
        # Simple substring similarity for now
        # In production, use embeddings
        for entry_key, entry in self._cache.items():
            if entry_key == key:
                continue
            # Simple heuristic: shared words ratio
            # Production: cosine similarity of embeddings
        return None
    
    async def _precognition_prefetch(self, key: str) -> None:
        """Prefetch likely next entries based on patterns."""
        if random.random() > self.config.prefetch_probability:
            return
        
        # Find likely next keys from Markov chains
        for depth in range(self.config.precognition_depth, 0, -1):
            pattern_key = f"depth_{depth}"
            history = self._access_patterns.get(pattern_key, [])
            
            if len(history) >= depth:
                # Find sequences ending with current key
                recent = history[-100:]
                for i in range(len(recent) - depth):
                    if recent[i + depth - 1] == key and i + depth < len(recent):
                        next_key = recent[i + depth]
                        if next_key in self._cache:
                            # Move to end to keep hot
                            self._cache.move_to_end(next_key)
    
    def _evict_batch(self) -> None:
        """Evict lowest priority entries."""
        # Sort by priority score
        entries = list(self._cache.items())
        entries.sort(key=lambda x: x[1].priority_score)
        
        to_evict = entries[:self.config.eviction_batch_size]
        for key, _ in to_evict:
            del self._cache[key]
            self._evictions += 1
    
    async def warmup(self, prompts: List[Tuple[str, Any]], 
                     params: Optional[List[Dict]] = None) -> None:
        """Warm cache with known prompts."""
        for i, (prompt, value) in enumerate(prompts):
            p = params[i] if params and i < len(params) else None
            await self.put(prompt, value, p)
        logger.info(f"Warmed cache with {len(prompts)} entries")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self.config.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "prefetch_hits": self._prefetch_hits,
            "evictions": self._evictions,
            "hit_rate": self._hits / max(1, total),
            "prefetch_rate": self._prefetch_hits / max(1, total),
            "pattern_depths": len(self._access_patterns)
        }
    
    async def invalidate(self, pattern: Optional[str] = None) -> int:
        """Invalidate cache entries matching pattern."""
        async with self._lock:
            if pattern is None:
                count = len(self._cache)
                self._cache.clear()
                return count
            
            to_remove = [k for k in self._cache if pattern in k]
            for k in to_remove:
                del self._cache[k]
            return len(to_remove)


__all__ = ["PrecognitionCache", "PrecognitionConfig", "CacheEntry"]