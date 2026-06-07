"""Kiro Protocol v3.0 Python SDK."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Union
from urllib.parse import urljoin

import aiohttp


@dataclass
class PromptRequest:
    prompt: str
    model: str = "gpt-4"
    max_tokens: int = 512
    temperature: float = 0.7
    top_p: float = 1.0
    stream: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JobStatus:
    job_id: str
    status: str  # queued, running, completed, failed, cancelled
    progress: float = 0.0
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class EngineMetrics:
    active_jobs: int
    queued_jobs: int
    gpu_utilization: float
    memory_usage_mb: float
    throughput_rps: float
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    phase_health: Dict[str, str]


class KiroClient:
    """Async Python client for Kiro Protocol v3.0 engine API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: Optional[str] = None,
        timeout: aiohttp.ClientTimeout = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout or aiohttp.ClientTimeout(total=30)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> KiroClient:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._session:
            await self._session.close()

    async def health(self) -> Dict[str, Any]:
        async with self._session.get(urljoin(self.base_url, "/health")) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def submit_prompt(self, request: PromptRequest) -> str:
        payload = {
            "prompt": request.prompt,
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": request.stream,
            "metadata": request.metadata,
        }
        async with self._session.post(
            urljoin(self.base_url, "/v3/prompt"), json=payload
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["job_id"]

    async def get_job(self, job_id: str) -> JobStatus:
        async with self._session.get(
            urljoin(self.base_url, f"/v3/jobs/{job_id}")
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return JobStatus(**data)

    async def cancel_job(self, job_id: str) -> bool:
        async with self._session.post(
            urljoin(self.base_url, f"/v3/jobs/{job_id}/cancel")
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("cancelled", False)

    async def stream_job(self, job_id: str) -> AsyncIterator[str]:
        async with self._session.get(
            urljoin(self.base_url, f"/v3/jobs/{job_id}/events"),
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("data: "):
                    yield line[6:]

    async def get_metrics(self) -> EngineMetrics:
        async with self._session.get(urljoin(self.base_url, "/metrics")) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return EngineMetrics(
                active_jobs=data["active_jobs"],
                queued_jobs=data["queued_jobs"],
                gpu_utilization=data["gpu_utilization"],
                memory_usage_mb=data["memory_usage_mb"],
                throughput_rps=data["throughput_rps"],
                avg_latency_ms=data["avg_latency_ms"],
                p95_latency_ms=data["p95_latency_ms"],
                p99_latency_ms=data["p99_latency_ms"],
                phase_health=data.get("phase_health", {}),
            )

    async def wait_for_completion(
        self,
        job_id: str,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
    ) -> JobStatus:
        start = asyncio.get_event_loop().time()
        while True:
            status = await self.get_job(job_id)
            if status.status in ("completed", "failed", "cancelled"):
                return status
            if asyncio.get_event_loop().time() - start > timeout:
                raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")
            await asyncio.sleep(poll_interval)


class KiroBatchClient:
    """Batch processing client with concurrency control."""

    def __init__(self, client: KiroClient, max_concurrency: int = 10):
        self.client = client
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def submit_batch(self, requests: List[PromptRequest]) -> List[str]:
        async with self.semaphore:
            return await asyncio.gather(*[
                self.client.submit_prompt(req) for req in requests
            ])

    async def wait_for_all(self, job_ids: List[str]) -> List[JobStatus]:
        return await asyncio.gather(*[
            self.client.wait_for_completion(jid) for jid in job_ids
        ])
