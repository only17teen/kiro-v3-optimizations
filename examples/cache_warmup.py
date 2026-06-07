"""Cache Warmup Example - Pre-populating cache for known workloads.

This example demonstrates how to use the Precognition Cache to:
- Pre-populate with common prompts
- Warm cache from historical data
- Monitor hit rates and prefetch effectiveness
"""

import asyncio
import logging
import random
from typing import Dict, List, Any
from dataclasses import dataclass

from engine.cache.precognition import PrecognitionCache, PrecognitionConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class PromptTemplate:
    """Template for generating prompts."""
    category: str
    template: str
    variables: List[str]
    common_values: Dict[str, List[str]]


class CacheWarmer:
    """Cache warmup utility for common workloads."""
    
    def __init__(self, cache: PrecognitionCache):
        self.cache = cache
        self.warmup_stats = {
            "pre_populated": 0,
            "warmup_hits": 0,
            "warmup_misses": 0,
        }
    
    async def pre_populate_common_prompts(self) -> int:
        """Pre-populate cache with common prompts."""
        
        common_prompts = [
            # Portrait prompts
            ("portrait of a beautiful woman, detailed face, professional photography", 
             "portrait_woman_001"),
            ("portrait of a handsome man, detailed face, professional photography",
             "portrait_man_001"),
            ("portrait of an elderly person, wrinkles, wisdom, detailed",
             "portrait_elderly_001"),
            
            # Landscape prompts
            ("beautiful sunset over mountains, golden hour, 8k resolution",
             "landscape_sunset_001"),
            ("serene lake in the mountains, reflection, misty morning",
             "landscape_lake_001"),
            ("vast desert dunes, dramatic lighting, cinematic",
             "landscape_desert_001"),
            
            # Architecture prompts
            ("futuristic city skyline, neon lights, cyberpunk style",
             "arch_futuristic_001"),
            ("ancient temple ruins, overgrown, mysterious atmosphere",
             "arch_ancient_001"),
            ("modern glass building, reflections, minimalist design",
             "arch_modern_001"),
            
            # Abstract prompts
            ("abstract flowing colors, vibrant, fluid art",
             "abstract_flowing_001"),
            ("geometric patterns, sacred geometry, golden ratio",
             "abstract_geometric_001"),
            ("cosmic nebula, stars, deep space, colorful",
             "abstract_cosmic_001"),
            
            # Character prompts
            ("cute anime character, big eyes, colorful hair, chibi style",
             "char_anime_001"),
            ("realistic dragon, scales, fire, epic fantasy",
             "char_dragon_001"),
            ("cute robot, friendly, round design, pastel colors",
             "char_robot_001"),
        ]
        
        count = 0
        for prompt, result_id in common_prompts:
            await self.cache.put(prompt, f"result:{result_id}")
            count += 1
        
        self.warmup_stats["pre_populated"] = count
        logger.info(f"Pre-populated {count} common prompts")
        return count
    
    async def warmup_from_templates(self, templates: List[PromptTemplate],
                                    samples_per_template: int = 5) -> int:
        """Warm cache by generating prompts from templates."""
        
        count = 0
        for template in templates:
            for _ in range(samples_per_template):
                # Generate random prompt from template
                prompt = template.template
                for var in template.variables:
                    if var in template.common_values:
                        value = random.choice(template.common_values[var])
                        prompt = prompt.replace(f"{{{var}}}", value)
                
                # Generate result
                result = f"generated:{template.category}:{hash(prompt) % 10000}"
                
                await self.cache.put(prompt, result)
                count += 1
        
        logger.info(f"Warmed cache with {count} template-generated prompts")
        return count
    
    async def warmup_from_historical_data(self, historical_prompts: List[str],
                                          hit_threshold: float = 0.3) -> int:
        """Warm cache from historical prompt data."""
        
        # Sort by frequency (most common first)
        from collections import Counter
        prompt_counts = Counter(historical_prompts)
        most_common = prompt_counts.most_common()
        
        # Calculate how many to pre-populate based on hit threshold
        total_prompts = len(historical_prompts)
        target_hits = int(total_prompts * hit_threshold)
        
        cumulative = 0
        count = 0
        
        for prompt, freq in most_common:
            if cumulative >= target_hits:
                break
            
            await self.cache.put(prompt, f"historical:{hash(prompt) % 10000}")
            cumulative += freq
            count += 1
        
        logger.info(f"Warmed cache with top {count} historical prompts "
                   f"(covering {cumulative}/{total_prompts} requests, "
                   f"{cumulative/total_prompts*100:.1f}%)")
        return count
    
    async def simulate_workload(self, prompts: List[str],
                                iterations: int = 100) -> Dict[str, Any]:
        """Simulate workload and measure cache performance."""
        
        hits = 0
        misses = 0
        
        for _ in range(iterations):
            # Pick random prompt (with bias towards common ones)
            prompt = random.choice(prompts)
            
            result = await self.cache.get(prompt)
            
            if result:
                hits += 1
                self.warmup_stats["warmup_hits"] += 1
            else:
                misses += 1
                self.warmup_stats["warmup_misses"] += 1
                
                # Simulate generation and cache result
                await self.cache.put(prompt, f"generated:{hash(prompt) % 10000}")
        
        total = hits + misses
        hit_rate = hits / total if total > 0 else 0
        
        return {
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "total_requests": total,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get warmup statistics."""
        return {
            **self.warmup_stats,
            "cache_stats": self.cache.get_stats(),
        }


def create_sample_templates() -> List[PromptTemplate]:
    """Create sample prompt templates."""
    
    return [
        PromptTemplate(
            category="portrait",
            template="portrait of a {gender} {age}, {style}, detailed face, {quality}",
            variables=["gender", "age", "style", "quality"],
            common_values={
                "gender": ["woman", "man", "child", "elderly person"],
                "age": ["young", "middle-aged", "old"],
                "style": ["professional photography", "candid shot", "studio lighting"],
                "quality": ["8k resolution", "high detail", "photorealistic"]
            }
        ),
        PromptTemplate(
            category="landscape",
            template="{time_of_day} {location}, {weather}, {mood}, {quality}",
            variables=["time_of_day", "location", "weather", "mood", "quality"],
            common_values={
                "time_of_day": ["sunrise", "sunset", "midday", "night"],
                "location": ["mountain range", "ocean shore", "forest path", "city skyline"],
                "weather": ["clear sky", "cloudy", "rainy", "snowy"],
                "mood": ["serene", "dramatic", "mystical", "peaceful"],
                "quality": ["8k resolution", "cinematic", "wide angle"]
            }
        ),
        PromptTemplate(
            category="character",
            template="{style} {character_type}, {features}, {colors}, {quality}",
            variables=["style", "character_type", "features", "colors", "quality"],
            common_values={
                "style": ["anime style", "realistic", "chibi", "fantasy art"],
                "character_type": ["warrior", "mage", "robot", "animal"],
                "features": ["detailed armor", "magical aura", "glowing eyes", "cute expression"],
                "colors": ["vibrant colors", "pastel palette", "dark tones", "neon accents"],
                "quality": ["high detail", "clean lines", "professional art"]
            }
        ),
    ]


def generate_historical_prompts(count: int = 1000) -> List[str]:
    """Generate sample historical prompt data with realistic frequency distribution."""
    
    templates = create_sample_templates()
    prompts = []
    
    # Generate with power-law distribution (some prompts much more common)
    for i in range(count):
        # Pick template (first template is more common)
        template_idx = int(random.paretovariate(1.5) % len(templates))
        template = templates[template_idx]
        
        # Generate prompt
        prompt = template.template
        for var in template.variables:
            if var in template.common_values:
                # Bias towards first few values
                values = template.common_values[var]
                idx = int(random.paretovariate(2.0) % len(values))
                value = values[idx]
                prompt = prompt.replace(f"{{{var}}}", value)
        
        # Add multiple copies for popular prompts (power law)
        copies = max(1, int(random.paretovariate(1.0)))
        prompts.extend([prompt] * copies)
    
    return prompts


async def main():
    """Run cache warmup example."""
    
    # Initialize cache
    cache = PrecognitionCache(PrecognitionConfig(
        max_size=10000,
        default_ttl=3600,
        precognition_depth=3,
        prefetch_probability=0.3
    ))
    
    warmer = CacheWarmer(cache)
    
    logger.info("=" * 60)
    logger.info("CACHE WARMUP EXAMPLE")
    logger.info("=" * 60)
    
    # Step 1: Pre-populate common prompts
    logger.info("\nStep 1: Pre-populating common prompts...")
    common_count = await warmer.pre_populate_common_prompts()
    
    # Step 2: Warm from templates
    logger.info("\nStep 2: Warming from templates...")
    templates = create_sample_templates()
    template_count = await warmer.warmup_from_templates(templates, samples_per_template=10)
    
    # Step 3: Generate historical data and warm from it
    logger.info("\nStep 3: Generating historical data...")
    historical_prompts = generate_historical_prompts(count=5000)
    
    logger.info(f"Historical data: {len(historical_prompts)} total requests")
    logger.info(f"Unique prompts: {len(set(historical_prompts))}")
    
    logger.info("\nStep 4: Warming from historical data...")
    historical_count = await warmer.warmup_from_historical_data(
        historical_prompts,
        hit_threshold=0.5  # Target 50% hit rate
    )
    
    # Step 5: Simulate workload
    logger.info("\nStep 5: Simulating workload...")
    
    # Use historical prompts for simulation
    simulation_prompts = random.sample(historical_prompts, min(1000, len(historical_prompts)))
    
    results = await warmer.simulate_workload(simulation_prompts, iterations=1000)
    
    logger.info(f"\nSimulation Results:")
    logger.info(f"  Total requests: {results['total_requests']}")
    logger.info(f"  Cache hits: {results['hits']}")
    logger.info(f"  Cache misses: {results['misses']}")
    logger.info(f"  Hit rate: {results['hit_rate']:.2%}")
    
    # Step 6: Print final stats
    logger.info("\n" + "=" * 60)
    logger.info("FINAL STATISTICS")
    logger.info("=" * 60)
    
    stats = warmer.get_stats()
    logger.info(f"Pre-populated: {stats['pre_populated']}")
    logger.info(f"Warmup hits: {stats['warmup_hits']}")
    logger.info(f"Warmup misses: {stats['warmup_misses']}")
    logger.info(f"Cache size: {stats['cache_stats']['size']}")
    logger.info(f"Cache hit rate: {stats['cache_stats']['hit_rate']:.2%}")
    logger.info(f"Prefetch hits: {stats['cache_stats']['prefetch_hits']}")
    
    # Recommendations
    logger.info("\nRecommendations:")
    if results['hit_rate'] < 0.3:
        logger.info("  - Hit rate is low. Consider increasing warmup coverage.")
    elif results['hit_rate'] < 0.6:
        logger.info("  - Hit rate is moderate. Good baseline, room for improvement.")
    else:
        logger.info("  - Hit rate is excellent. Cache is well-optimized.")
    
    if stats['cache_stats']['size'] > stats['cache_stats']['max_size'] * 0.9:
        logger.info("  - Cache is near capacity. Consider increasing max_size or reducing TTL.")


if __name__ == "__main__":
    asyncio.run(main())