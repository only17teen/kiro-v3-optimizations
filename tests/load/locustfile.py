"""Locust load testing for Kiro v3 engine API."""

from locust import HttpUser, task, between
import random
import json


class KiroEngineUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.client.headers.update({
            "Content-Type": "application/json",
            "X-API-Version": "v3",
        })

    @task(5)
    def health_check(self):
        with self.client.get("/health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Health check failed: {resp.status_code}")

    @task(3)
    def submit_prompt(self):
        payload = {
            "prompt": random.choice([
                "Summarize quantum computing",
                "Explain neural networks",
                "Generate Python code for sorting",
                "Translate to French: hello world",
            ]),
            "model": random.choice(["gpt-4", "claude-3", "llama-3"]),
            "max_tokens": random.randint(50, 500),
            "temperature": round(random.uniform(0.1, 1.0), 2),
        }
        with self.client.post("/v3/prompt", json=payload, catch_response=True) as resp:
            if resp.status_code != 202:
                resp.failure(f"Prompt submit failed: {resp.status_code}")
            else:
                data = resp.json()
                if "job_id" not in data:
                    resp.failure("Missing job_id in response")

    @task(2)
    def get_job_status(self):
        job_id = f"job-{random.randint(1, 10000)}"
        self.client.get(f"/v3/jobs/{job_id}")

    @task(1)
    def get_metrics(self):
        with self.client.get("/metrics", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Metrics failed: {resp.status_code}")

    @task(1)
    def stream_events(self):
        job_id = f"job-{random.randint(1, 10000)}"
        with self.client.get(f"/v3/jobs/{job_id}/events", stream=True, catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Event stream failed: {resp.status_code}")
