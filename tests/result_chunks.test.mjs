import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { encodeResultFrames } from '../browser-extension/result-chunks.js';

globalThis.crypto ||= webcrypto;

test('large Unicode result is framed below the local WebSocket limit', async () => {
  const result = { type: 'result', id: 'analytics-1', data: { text: '中文😊'.repeat(70_000) } };
  const frames = await encodeResultFrames(result);
  assert.ok(frames.length > 5);
  const parsed = frames.map(JSON.parse);
  assert.ok(parsed.every(frame => frame.id === result.id && frame.total === frames.length));
  assert.ok(frames.every(frame => frame.length < 950_000));
  const bytes = Buffer.concat(parsed.map(frame => Buffer.from(frame.body, 'base64')));
  assert.deepEqual(JSON.parse(bytes.toString('utf8')), result);
});

test('small result keeps the existing single-message protocol', async () => {
  const result = { type: 'result', id: 'one', data: { state: 'ready' } };
  assert.deepEqual((await encodeResultFrames(result)).map(JSON.parse), [result]);
});
