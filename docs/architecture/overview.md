# Architecture Overview

## Design Principles

Kiro Protocol v3.0 follows these core design principles:

1. **Relentless Optimization**: Every millisecond matters. Batch operations, pre-compute, cache aggressively.
2. **Lock-Free by Default**: Sharded locks, atomic operations, lock-free data structures.
3. **Memory First**: Object pooling, __slots__, zero-allocation hot paths.
4. **Graceful Degradation**: Circuit breakers, backpressure, fallback strategies.
5. **Observable Everything**: Metrics, tracing, chaos testing for validation.

## System Architecture

```mermaid
graph TB
    subgraph "Phase 1: Actor Model"
        A[Client Request] --> B[Actor Router]
        B -- Hash Ring --> C[Mailbox]
        C -- Priority Queue --> D[Actor Worker]
        D --> E[Message Pool]
        F[Supervisor] -- Restart Policy --> D
    end
    
    subgraph "Phase 2: Resource Management"
        D --> G[GPU Semaphore]
        G -- Token Bucket --> H[GPU Device 0]
        G --> I[GPU Device 1]
        D --> J[LLM Timeout]
        J -- Circuit Breaker --> K[LLM Service]
    end
    
    subgraph "Phase 3: Resilience"
        K -- Error --> L[Retry Manager]
        L -- Full Jitter --> M{Retryable?}
        M -- 503 --> N[Backoff Retry]
        M -- 400 --> O[Fail Fast]
    end
    
    subgraph "Phase 4: Strategy"
        P[UCB1 Bandit] -- Select Arm --> G
        Q[Reward Calculator] -- Update --> P
        R[Metrics] -- Feed --> Q
    end
    
    subgraph "Phase 5: Learning"
        S[Prompt] --> T[Cache Check]
        T -- Miss --> U[Inference]
        U --> V[Cache Store]
        T -- Hit --> W[Fast Return]
        X[Trainer Daemon] -- LoRA --> U
    end
    
    subgraph "Phase 6: Observability"
        Y[Prometheus] --> Z[Grafana]
        AA[Chaos Monkey] -- Inject --> B
    end
    
    subgraph "Phase 7: Native"
        AB[Rust FFI] --> AC[Actor Registry]
        AD[CUDA] --> AE[Flash Attention]
        AD --> AF[INT8 Quantize]
    end
```

## Data Flow

### Inference Request Flow

1. **Request Ingress**: Client sends prompt to API
2. **Actor Routing**: Hash ring selects actor worker
3. **Priority Enqueue**: Message placed in priority mailbox (CRITICAL > HIGH > NORMAL > LOW > BACKGROUND)
4. **Cache Check**: Precognition cache checks for exact or semantic match
5. **GPU Acquisition**: Token-bucket semaphore acquires GPU slot
6. **Strategy Selection**: UCB1 bandit selects best GPU strategy based on historical rewards
7. **LLM Call**: Circuit breaker protected, timeout managed, retry enabled
8. **Result Processing**: Post-process, cache result, record metrics
9. **Response**: Return to client with latency metadata

### Actor Lifecycle

```mermaid
sequenceDiagram
    participant Client
    participant Router
    participant Mailbox
    participant Supervisor
    participant Worker
    participant Pool
    
    Client->>Router: spawn(handler)
    Router->>Supervisor: register(actor_id, factory)
    Supervisor->>Worker: start()
    Worker->>Pool: borrow_message()
    Pool-->>Worker: ActorMessage
    
    loop Message Processing
        Client->>Router: tell(message)
        Router->>Mailbox: enqueue(message, priority)
        Mailbox->>Worker: dequeue_batch()
        Worker->>Worker: process(message)
        Worker->>Pool: return_message()
    end
    
    Worker->>Supervisor: failure(exception)
    Supervisor->>Supervisor: check_restart_policy()
    alt ONE_FOR_ONE
        Supervisor->>Worker: restart()
    else ONE_FOR_ALL
        Supervisor->>Worker: restart_all()
    end
```

## Component Interaction

### Actor System + GPU Semaphore

```python
async def inference_actor(msg):
    # 1. Check cache (Phase 5)
    cached = await cache.get(msg.prompt)
    if cached:
        return cached
    
    # 2. Select strategy (Phase 4)
    strategy = bandit.select_arm()
    
    # 3. Acquire GPU (Phase 2)
    async with gpu_semaphore:
        # 4. Call LLM with protection (Phase 3)
        result = await retry_manager.execute(
            lambda: llm_timeout.call(generate, msg.prompt)
        )
    
    # 5. Cache and record (Phase 5, 6)
    await cache.put(msg.prompt, result)
    metrics.counter("inference").inc()
    
    return result
```

## Performance Characteristics

| Component | Throughput | Latency (p99) | Memory |
|-----------|-----------|--------------|--------|
| Actor Router | 500K msg/s | 10μs | 2MB |
| Priority Mailbox | 100K msg/s | 50μs | 10MB |
| GPU Semaphore | 10K acquire/s | 100μs | 1MB |
| Precognition Cache | 50K lookup/s | 20μs | 500MB |
| UCB1 Bandit | 1M selects/s | 5μs | 100KB |
| Rust FFI Registry | 2M ops/s | 2μs | 5MB |

## Scaling Model

### Horizontal Scaling
- Actor workers scale across CPU cores
- GPU semaphore handles multiple devices
- Cache shards across instances
- Bandit instances per GPU device

### Vertical Scaling
- Rust FFI for CPU-bound operations
- CUDA kernels for GPU-bound operations
- Lock-free structures for contention reduction
- Object pooling for allocation reduction

## Failure Modes

| Failure | Detection | Mitigation |
|---------|-----------|------------|
| Actor crash | Supervisor heartbeat | ONE_FOR_ONE restart |
| GPU OOM | Memory pressure signal | Queue backpressure |
| LLM timeout | Adaptive timeout | Circuit breaker |
| Cache miss | Miss rate spike | Precognition prefetch |
| Strategy poor | Low reward signal | Bandit exploration |
| GC pause | Pause time metric | Freeze + background |
| Network partition | Health check failure | Retry with jitter |