"""Trainer Daemon - Offline LoRA training with checkpoint resume."""

import asyncio
import logging
import os
import json
from typing import Dict, Any, Optional, List, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime
import time

logger = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    """LoRA training configuration."""
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: List[str] = field(default_factory=lambda: ["q_proj", "v_proj"])
    learning_rate: float = 1e-4
    batch_size: int = 4
    gradient_accumulation_steps: int = 4
    max_steps: int = 1000
    warmup_steps: int = 100
    save_steps: int = 100
    eval_steps: int = 50
    max_grad_norm: float = 1.0
    weight_decay: float = 0.01
    output_dir: str = "./lora_output"
    checkpoint_dir: str = "./checkpoints"
    resume_from_checkpoint: Optional[str] = None


@dataclass
class TrainingState:
    """Serializable training state for checkpoint resume."""
    step: int = 0
    epoch: float = 0.0
    global_step: int = 0
    best_metric: float = float('inf')
    learning_rate: float = 1e-4
    loss_history: List[float] = field(default_factory=list)
    eval_history: List[Dict[str, float]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TrainingState':
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CheckpointManager:
    """Manage training checkpoints with resume capability."""
    
    def __init__(self, config: TrainingConfig):
        self.config = config
        self._checkpoint_dir = config.checkpoint_dir
        os.makedirs(self._checkpoint_dir, exist_ok=True)
        
    def save(self, state: TrainingState, tag: Optional[str] = None) -> str:
        """Save checkpoint to disk."""
        tag = tag or f"step_{state.global_step}"
        path = os.path.join(self._checkpoint_dir, f"checkpoint_{tag}.json")
        
        with open(path, 'w') as f:
            json.dump(state.to_dict(), f, indent=2)
        
        logger.info(f"Saved checkpoint: {path}")
        return path
    
    def load_latest(self) -> Optional[TrainingState]:
        """Load most recent checkpoint."""
        checkpoints = self._list_checkpoints()
        if not checkpoints:
            return None
        
        latest = max(checkpoints, key=lambda x: x[1])
        return self._load_file(latest[0])
    
    def load_by_tag(self, tag: str) -> Optional[TrainingState]:
        """Load checkpoint by tag."""
        path = os.path.join(self._checkpoint_dir, f"checkpoint_{tag}.json")
        if os.path.exists(path):
            return self._load_file(path)
        return None
    
    def _list_checkpoints(self) -> List[Tuple[str, float]]:
        """List checkpoint files with modification times."""
        if not os.path.exists(self._checkpoint_dir):
            return []
        
        files = []
        for f in os.listdir(self._checkpoint_dir):
            if f.startswith("checkpoint_") and f.endswith(".json"):
                path = os.path.join(self._checkpoint_dir, f)
                files.append((path, os.path.getmtime(path)))
        return files
    
    def _load_file(self, path: str) -> Optional[TrainingState]:
        """Load checkpoint from file."""
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            state = TrainingState.from_dict(data)
            logger.info(f"Loaded checkpoint from {path} (step {state.global_step})")
            return state
        except Exception as e:
            logger.error(f"Failed to load checkpoint {path}: {e}")
            return None
    
    def cleanup_old(self, keep_last: int = 5) -> int:
        """Remove old checkpoints, keeping only the most recent."""
        checkpoints = self._list_checkpoints()
        if len(checkpoints) <= keep_last:
            return 0
        
        # Sort by modification time, oldest first
        checkpoints.sort(key=lambda x: x[1])
        to_remove = checkpoints[:-keep_last]
        
        removed = 0
        for path, _ in to_remove:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        
        logger.info(f"Cleaned up {removed} old checkpoints")
        return removed


class TrainerDaemon:
    """Offline training daemon with checkpoint resume."""
    
    def __init__(self, config: Optional[TrainingConfig] = None):
        self.config = config or TrainingConfig()
        self.checkpoint_manager = CheckpointManager(self.config)
        self.state = TrainingState()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable] = []
        
    async def start(self, resume: bool = True) -> None:
        """Start training daemon."""
        if self._running:
            return
        
        # Resume from checkpoint if available
        if resume and self.config.resume_from_checkpoint:
            loaded = self.checkpoint_manager.load_by_tag(
                self.config.resume_from_checkpoint
            )
            if loaded:
                self.state = loaded
        elif resume:
            loaded = self.checkpoint_manager.load_latest()
            if loaded:
                self.state = loaded
        
        self._running = True
        self._task = asyncio.create_task(self._training_loop())
        logger.info(f"Training daemon started (step {self.state.global_step})")
    
    async def stop(self) -> None:
        """Stop training daemon."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        # Save final checkpoint
        self.checkpoint_manager.save(self.state, "final")
        logger.info("Training daemon stopped")
    
    async def _training_loop(self) -> None:
        """Main training loop."""
        try:
            while self._running and self.state.global_step < self.config.max_steps:
                # Simulate training step
                await self._training_step()
                
                # Periodic checkpoint
                if self.state.global_step % self.config.save_steps == 0:
                    self.checkpoint_manager.save(self.state)
                
                # Periodic evaluation
                if self.state.global_step % self.config.eval_steps == 0:
                    await self._evaluation_step()
                
                # Cleanup old checkpoints
                if self.state.global_step % (self.config.save_steps * 5) == 0:
                    self.checkpoint_manager.cleanup_old()
                
                await asyncio.sleep(0.01)  # Yield control
                
        except asyncio.CancelledError:
            logger.info("Training loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Training error: {e}")
            # Save emergency checkpoint
            self.checkpoint_manager.save(self.state, "emergency")
            raise
    
    async def _training_step(self) -> None:
        """Execute single training step."""
        self.state.global_step += 1
        
        # Simulate loss computation (replace with actual training)
        # In production, this would call the model forward/backward
        simulated_loss = max(0.1, 2.0 * (0.99 ** self.state.global_step))
        self.state.loss_history.append(simulated_loss)
        
        # Trim history
        if len(self.state.loss_history) > 1000:
            self.state.loss_history = self.state.loss_history[-500:]
        
        # Notify callbacks
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(self.state)
                else:
                    callback(self.state)
            except Exception as e:
                logger.warning(f"Callback error: {e}")
    
    async def _evaluation_step(self) -> None:
        """Execute evaluation step."""
        # Simulate evaluation metrics
        eval_metrics = {
            "loss": sum(self.state.loss_history[-100:]) / max(1, len(self.state.loss_history[-100:])),
            "step": self.state.global_step,
            "timestamp": time.time()
        }
        
        self.state.eval_history.append(eval_metrics)
        
        # Update best metric
        if eval_metrics["loss"] < self.state.best_metric:
            self.state.best_metric = eval_metrics["loss"]
            self.checkpoint_manager.save(self.state, "best")
        
        logger.info(f"Eval at step {self.state.global_step}: loss={eval_metrics['loss']:.4f}")
    
    def register_callback(self, callback: Callable) -> None:
        """Register training callback."""
        self._callbacks.append(callback)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get training statistics."""
        return {
            "running": self._running,
            "step": self.state.global_step,
            "epoch": self.state.epoch,
            "best_metric": self.state.best_metric,
            "loss_history_samples": len(self.state.loss_history),
            "eval_history_samples": len(self.state.eval_history),
            "config": self.config.to_dict() if hasattr(self.config, 'to_dict') else asdict(self.config)
        }
    
    async def export_lora(self, path: Optional[str] = None) -> str:
        """Export trained LoRA weights."""
        export_path = path or os.path.join(
            self.config.output_dir, 
            f"lora_step_{self.state.global_step}"
        )
        os.makedirs(export_path, exist_ok=True)
        
        # Save adapter config
        adapter_config = {
            "r": self.config.lora_r,
            "lora_alpha": self.config.lora_alpha,
            "lora_dropout": self.config.lora_dropout,
            "target_modules": self.config.target_modules,
            "bias": "none",
            "task_type": "CAUSAL_LM"
        }
        
        with open(os.path.join(export_path, "adapter_config.json"), 'w') as f:
            json.dump(adapter_config, f, indent=2)
        
        logger.info(f"Exported LoRA to {export_path}")
        return export_path


__all__ = ["TrainerDaemon", "TrainingConfig", "TrainingState", "CheckpointManager"]