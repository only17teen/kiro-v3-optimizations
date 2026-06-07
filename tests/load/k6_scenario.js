"""k6 load testing scenarios for Kiro v3."""

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

const errorRate = new Rate('errors');
const latencyTrend = new Trend('latency');
const promptCounter = new Counter('prompts_submitted');

export const options = {
  stages: [
    { duration: '2m', target: 50 },
    { duration: '5m', target: 200 },
    { duration: '5m', target: 500 },
    { duration: '3m', target: 700 },
    { duration: '2m', target: 1000 },
    { duration: '5m', target: 1000 },
    { duration: '3m', target: 500 },
    { duration: '2m', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    http_req_failed: ['rate<<0.01'],
    errors: ['rate<<0.05'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  group('Health & Readiness', () => {
    const res = http.get(`${BASE_URL}/health`);
    const success = check(res, {
      'health status 200': (r) => r.status === 200,
      'health response time < 100ms': (r) => r.timings.duration < 100,
    });
    errorRate.add(!success);
    latencyTrend.add(res.timings.duration);
  });

  group('Prompt Submission', () => {
    const payload = JSON.stringify({
      prompt: randomChoice([
        'Summarize quantum computing',
        'Explain neural networks',
        'Generate Python code for sorting',
        'Translate to French: hello world',
      ]),
      model: randomChoice(['gpt-4', 'claude-3', 'llama-3']),
      max_tokens: randomInt(50, 500),
      temperature: Math.random(),
    });

    const res = http.post(`${BASE_URL}/v3/prompt`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });

    const success = check(res, {
      'prompt status 202': (r) => r.status === 202,
      'prompt has job_id': (r) => r.json('job_id') !== undefined,
    });

    errorRate.add(!success);
    latencyTrend.add(res.timings.duration);
    if (success) promptCounter.add(1);
  });

  group('Job Status Polling', () => {
    const jobId = `job-${randomInt(1, 10000)}`;
    const res = http.get(`${BASE_URL}/v3/jobs/${jobId}`);
    check(res, {
      'job status valid': (r) => r.status === 200 || r.status === 404,
    });
  });

  group('Metrics Endpoint', () => {
    const res = http.get(`${BASE_URL}/metrics`);
    check(res, {
      'metrics status 200': (r) => r.status === 200,
    });
  });

  sleep(Math.random() * 2 + 0.5);
}

function randomChoice(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}
