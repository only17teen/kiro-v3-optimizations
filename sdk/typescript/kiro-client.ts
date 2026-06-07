export interface PromptRequest {
  prompt: string;
  model?: string;
  max_tokens?: number;
  temperature?: number;
  top_p?: number;
  stream?: boolean;
  metadata?: Record<string, unknown>;
}

export interface JobStatus {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  progress: number;
  result?: string;
  error?: string;
  created_at?: string;
  completed_at?: string;
}

export interface EngineMetrics {
  active_jobs: number;
  queued_jobs: number;
  gpu_utilization: number;
  memory_usage_mb: number;
  throughput_rps: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  p99_latency_ms: number;
  phase_health: Record<string, string>;
}

export interface KiroClientConfig {
  baseUrl?: string;
  apiKey?: string;
  timeoutMs?: number;
}

export class KiroClient {
  private baseUrl: string;
  private apiKey?: string;
  private timeoutMs: number;

  constructor(config: KiroClientConfig = {}) {
    this.baseUrl = (config.baseUrl || 'http://localhost:8080').replace(/\/$/, '');
    this.apiKey = config.apiKey;
    this.timeoutMs = config.timeoutMs || 30000;
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (this.apiKey) {
      h['Authorization'] = `Bearer ${this.apiKey}`;
    }
    return h;
  }

  private async fetch(path: string, init?: RequestInit): Promise<Response> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const response = await fetch(url, {
        ...init,
        headers: { ...this.headers(), ...(init?.headers || {}) },
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${await response.text()}`);
      }
      return response;
    } finally {
      clearTimeout(timeout);
    }
  }

  async health(): Promise<<Record<string, unknown>> {
    const resp = await this.fetch('/health');
    return resp.json();
  }

  async submitPrompt(request: PromptRequest): Promise<string> {
    const resp = await this.fetch('/v3/prompt', {
      method: 'POST',
      body: JSON.stringify(request),
    });
    const data = await resp.json();
    return data.job_id;
  }

  async getJob(jobId: string): Promise<<JobStatus> {
    const resp = await this.fetch(`/v3/jobs/${jobId}`);
    return resp.json();
  }

  async cancelJob(jobId: string): Promise<boolean> {
    const resp = await this.fetch(`/v3/jobs/${jobId}/cancel`, { method: 'POST' });
    const data = await resp.json();
    return data.cancelled ?? false;
  }

  async *streamJob(jobId: string): AsyncGenerator<string> {
    const resp = await this.fetch(`/v3/jobs/${jobId}/events`, {
      headers: { Accept: 'text/event-stream' },
    });
    const reader = resp.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed.startsWith('data: ')) {
          yield trimmed.slice(6);
        }
      }
    }
  }

  async getMetrics(): Promise<<EngineMetrics> {
    const resp = await this.fetch('/metrics');
    return resp.json();
  }

  async waitForCompletion(
    jobId: string,
    pollIntervalMs: number = 1000,
    timeoutMs: number = 300000,
  ): Promise<<JobStatus> {
    const start = Date.now();
    while (true) {
      const status = await this.getJob(jobId);
      if (['completed', 'failed', 'cancelled'].includes(status.status)) {
        return status;
      }
      if (Date.now() - start > timeoutMs) {
        throw new Error(`Job ${jobId} did not complete within ${timeoutMs}ms`);
      }
      await new Promise((r) => setTimeout(r, pollIntervalMs));
    }
  }
}

export class KiroBatchClient {
  constructor(
    private client: KiroClient,
    private maxConcurrency: number = 10,
  ) {}

  async submitBatch(requests: PromptRequest[]): Promise<string[]> {
    const semaphore = new Semaphore(this.maxConcurrency);
    return Promise.all(
      requests.map((req) =>
        semaphore.acquire().then(async () => {
          try {
            return await this.client.submitPrompt(req);
          finally {
            semaphore.release();
          }
        }),
      ),
    );
  }

  async waitForAll(jobIds: string[]): Promise<<JobStatus[]> {
    return Promise.all(jobIds.map((id) => this.client.waitForCompletion(id)));
  }
}

class Semaphore {
  private permits: number;
  private queue: Array<<() => void> = [];

  constructor(initial: number) {
    this.permits = initial;
  }

  acquire(): Promise<void> {
    return new Promise((resolve) => {
      if (this.permits > 0) {
        this.permits--;
        resolve();
      } else {
        this.queue.push(resolve);
      }
    });
  }

  release(): void {
    if (this.queue.length > 0) {
      const next = this.queue.shift()!;
      next();
    } else {
      this.permits++;
    }
  }
}
