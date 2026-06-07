"""Tests for Kiro Python SDK."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sdk.python.kiro_client import KiroClient, PromptRequest, JobStatus


@pytest.fixture
async def client():
    async with KiroClient(base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_submit_prompt():
    client = KiroClient(base_url="http://test")
    client._session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.__aenter__.return_value = mock_resp
    mock_resp.json.return_value = {"job_id": "job-123"}
    mock_resp.raise_for_status = MagicMock()
    client._session.post.return_value = mock_resp

    req = PromptRequest(prompt="Hello", model="gpt-4")
    job_id = await client.submit_prompt(req)
    assert job_id == "job-123"


@pytest.mark.asyncio
async def test_get_job():
    client = KiroClient(base_url="http://test")
    client._session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.__aenter__.return_value = mock_resp
    mock_resp.json.return_value = {
        "job_id": "job-123",
        "status": "completed",
        "progress": 1.0,
        "result": "Hello world",
    }
    mock_resp.raise_for_status = MagicMock()
    client._session.get.return_value = mock_resp

    status = await client.get_job("job-123")
    assert status.status == "completed"
    assert status.result == "Hello world"


@pytest.mark.asyncio
async def test_cancel_job():
    client = KiroClient(base_url="http://test")
    client._session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.__aenter__.return_value = mock_resp
    mock_resp.json.return_value = {"cancelled": True}
    mock_resp.raise_for_status = MagicMock()
    client._session.post.return_value = mock_resp

    result = await client.cancel_job("job-123")
    assert result is True


@pytest.mark.asyncio
async def test_get_metrics():
    client = KiroClient(base_url="http://test")
    client._session = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.__aenter__.return_value = mock_resp
    mock_resp.json.return_value = {
        "active_jobs": 5,
        "queued_jobs": 2,
        "gpu_utilization": 0.85,
        "memory_usage_mb": 4096.0,
        "throughput_rps": 120.5,
        "avg_latency_ms": 45.0,
        "p95_latency_ms": 120.0,
        "p99_latency_ms": 200.0,
        "phase_health": {"phase_1": "healthy"},
    }
    mock_resp.raise_for_status = MagicMock()
    client._session.get.return_value = mock_resp

    metrics = await client.get_metrics()
    assert metrics.active_jobs == 5
    assert metrics.gpu_utilization == 0.85
    assert metrics.phase_health["phase_1"] == "healthy"
