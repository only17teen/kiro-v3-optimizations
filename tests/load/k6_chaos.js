"""k6 chaos testing scenario for Kiro v3 resilience."""

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '1m', target: 100 },
    { duration: '2m', target: 500 },
    { duration: '1m', target: 1000 },
    { duration: '30s', target: 2000 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<<0.10'],
    errors: ['rate<<0.15'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  group('Spike Load - Prompt Flood', () => {
    const payload = JSON.stringify({
      prompt: 'Stress test prompt ' + Math.random(),
      model: 'gpt-4',
      max_tokens: 1000,
    });
    const res = http.post(`${BASE_URL}/v3/prompt`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    errorRate.add(res.status >= 500);
  });

  group('Invalid Payloads', () => {
    const badPayloads = [
      JSON.stringify({}),
      JSON.stringify({ prompt: null }),
      'not-json',
      JSON.stringify({ prompt: 'x', model: 'unknown-model' }),
    ];
    const payload = badPayloads[Math.floor(Math.random() * badPayloads.length)];
    const res = http.post(`${BASE_URL}/v3/prompt`, payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    check(res, {
      'bad request handled': (r) => r.status === 400 || r.status === 422,
    });
  });

  group('Concurrent Status Checks', () => {
    const requests = Array.from({ length: 10 }, (_, i) => ({
      method: 'GET',
      url: `${BASE_URL}/v3/jobs/job-${i}`,
    }));
    const responses = http.batch(requests);
    responses.forEach((res) => {
      check(res, {
        'status check valid': (r) => r.status === 200 || r.status === 404,
      });
    });
  });

  sleep(0.1);
}
