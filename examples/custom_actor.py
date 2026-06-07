"""Custom Actor Example - Building domain-specific actors with Kiro v3.0.

This example demonstrates how to create custom actors for specific use cases:
- Image processing pipeline
- Batch inference worker
- Result aggregator
"""

import asyncio
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

from engine.actor import ActorSystem, ActorRef, RouteStrategy, Priority
from engine.actor.mailbox import MailboxConfig
from engine.actor.supervisor import SupervisorConfig, RestartPolicy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class ImageTask:
    """Image processing task."""
    task_id: str
    image_url: str
    prompt: str
    width: int = 512
    height: int = 512
    steps: int = 20


@dataclass
class BatchJob:
    """Batch inference job."""
    job_id: str
    tasks: List[ImageTask]
    callback_url: str


class ImageProcessingActor:
    """Actor for image processing pipeline."""
    
    def __init__(self, system: ActorSystem):
        self.system = system
        self.processed_count = 0
        self.failed_count = 0
    
    async def create(self) -> ActorRef:
        """Create image processing actor."""
        
        async def process_image(task: ImageTask) -> Dict[str, Any]:
            """Process single image."""
            logger.info(f"Processing image task {task.task_id}")
            
            # Simulate image generation
            await asyncio.sleep(0.1)
            
            self.processed_count += 1
            
            return {
                "task_id": task.task_id,
                "status": "completed",
                "result_url": f"https://cdn.example.com/{task.task_id}.png",
                "metadata": {
                    "width": task.width,
                    "height": task.height,
                    "steps": task.steps,
                }
            }
        
        ref = await self.system.spawn(
            "image-processor",
            process_image,
            metadata={"actor_type": "image_processor"}
        )
        
        logger.info("Image processing actor created")
        return ref


class BatchInferenceActor:
    """Actor for batch inference jobs."""
    
    def __init__(self, system: ActorSystem, image_processor: ActorRef):
        self.system = system
        self.image_processor = image_processor
        self.active_jobs: Dict[str, Any] = {}
    
    async def create(self) -> ActorRef:
        """Create batch inference actor."""
        
        async def process_batch(job: BatchJob) -> Dict[str, Any]:
            """Process batch of image tasks."""
            logger.info(f"Processing batch job {job.job_id} with {len(job.tasks)} tasks")
            
            self.active_jobs[job.job_id] = {
                "status": "running",
                "total": len(job.tasks),
                "completed": 0,
                "failed": 0,
            }
            
            # Process tasks in parallel using image processor
            results = []
            for task in job.tasks:
                # Send with HIGH priority for batch jobs
                result = await self.image_processor.ask(
                    task,
                    timeout=30.0,
                    priority=Priority.HIGH
                )
                
                if result:
                    results.append(result)
                    self.active_jobs[job.job_id]["completed"] += 1
                else:
                    self.active_jobs[job.job_id]["failed"] += 1
            
            self.active_jobs[job.job_id]["status"] = "completed"
            
            return {
                "job_id": job.job_id,
                "status": "completed",
                "results": results,
                "summary": {
                    "total": len(job.tasks),
                    "successful": len(results),
                    "failed": len(job.tasks) - len(results),
                }
            }
        
        ref = await self.system.spawn(
            "batch-inference",
            process_batch,
            dependencies=["image-processor"],
            metadata={"actor_type": "batch_inference"}
        )
        
        logger.info("Batch inference actor created")
        return ref


class ResultAggregatorActor:
    """Actor for aggregating and storing results."""
    
    def __init__(self, system: ActorSystem):
        self.system = system
        self.results: Dict[str, Any] = {}
    
    async def create(self) -> ActorRef:
        """Create result aggregator actor."""
        
        async def store_result(result: Dict[str, Any]) -> str:
            """Store and index result."""
            job_id = result.get("job_id", "unknown")
            
            self.results[job_id] = {
                "data": result,
                "timestamp": asyncio.get_event_loop().time(),
            }
            
            logger.info(f"Stored result for job {job_id}")
            
            # Simulate async storage (S3, database, etc.)
            await asyncio.sleep(0.01)
            
            return f"stored:{job_id}"
        
        async def get_results(query: Dict[str, Any]) -> List[Dict[str, Any]]:
            """Query stored results."""
            job_id = query.get("job_id")
            
            if job_id and job_id in self.results:
                return [self.results[job_id]["data"]]
            
            # Return all results if no filter
            return [r["data"] for r in self.results.values()]
        
        async def aggregator_handler(msg: Dict[str, Any]) -> Any:
            """Route to appropriate handler."""
            action = msg.get("action", "store")
            
            if action == "store":
                return await store_result(msg.get("data"))
            elif action == "query":
                return await get_results(msg.get("query", {}))
            else:
                return {"error": f"Unknown action: {action}"}
        
        ref = await self.system.spawn(
            "result-aggregator",
            aggregator_handler,
            metadata={"actor_type": "result_aggregator"}
        )
        
        logger.info("Result aggregator actor created")
        return ref


async def main():
    """Run custom actor example."""
    
    # Create actor system with custom configuration
    system = ActorSystem(
        router_strategy=RouteStrategy.HASH_RING,
        mailbox_config=MailboxConfig(
            max_size=50000,
            backpressure_threshold=0.9
        ),
        supervisor_config=SupervisorConfig(
            max_restarts=10,
            restart_window=300,
            restart_policy=RestartPolicy.ONE_FOR_ONE
        )
    )
    
    await system.start()
    
    try:
        # Create actors
        image_processor = ImageProcessingActor(system)
        image_ref = await image_processor.create()
        
        batch_inference = BatchInferenceActor(system, image_ref)
        batch_ref = await batch_inference.create()
        
        aggregator = ResultAggregatorActor(system)
        aggregator_ref = await aggregator.create()
        
        # Create sample batch job
        tasks = [
            ImageTask(
                task_id=f"task-{i}",
                image_url=f"https://example.com/image-{i}.jpg",
                prompt=f"A beautiful landscape {i}",
                width=512,
                height=512,
                steps=20
            )
            for i in range(10)
        ]
        
        job = BatchJob(
            job_id="batch-001",
            tasks=tasks,
            callback_url="https://webhook.example.com/callback"
        )
        
        # Submit batch job
        logger.info("Submitting batch job...")
        result = await batch_ref.ask(job, timeout=60.0)
        
        if result:
            logger.info(f"Batch job completed: {result['summary']}")
            
            # Store result
            store_result = await aggregator_ref.ask({
                "action": "store",
                "data": result
            })
            logger.info(f"Store result: {store_result}")
            
            # Query results
            query_result = await aggregator_ref.ask({
                "action": "query",
                "query": {"job_id": "batch-001"}
            })
            logger.info(f"Query returned {len(query_result)} results")
        
        # Print system stats
        stats = system.get_stats()
        logger.info(f"\nSystem Statistics:")
        logger.info(f"  Total actors: {stats['router']['total_actors']}")
        logger.info(f"  Healthy actors: {stats['router']['healthy_actors']}")
        logger.info(f"  Mailbox size: {stats['mailbox']['queue_size']}")
        logger.info(f"  Processed: {image_processor.processed_count}")
        
    finally:
        await system.stop()
        logger.info("System stopped")


if __name__ == "__main__":
    asyncio.run(main())