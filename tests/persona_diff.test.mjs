import { test } from 'node:test';
import assert from 'node:assert/strict';
import { comparePersona, sameBody, rollbackBody } from '../assets/web-admin/src/personaDiff.ts';

const base = {
  name: '小林',
  identity: '我是一个明确标注的虚构 AI 人格。',
  voice: '自然、简洁',
  interests: '摄影',
  boundaries: '不谈股价',
  version: 4,
};

test('版本号不参与比对，内容相同的两版没有差异', () => {
  const result = comparePersona(base, { ...base, version: 9 });
  assert.deepEqual(result.changed, []);
  assert.equal(result.unchanged.length, 5);
});

test('只改一个字段时差异里只出现这一个字段', () => {
  const after = { ...base, voice: '更直接，少用感叹号', version: 5 };
  const result = comparePersona(base, after);
  assert.deepEqual(result.changed.map((item) => item.field), ['voice']);
  assert.equal(result.changed[0].label, '说话的方式');
  assert.equal(result.changed[0].before, '自然、简洁');
  assert.equal(result.changed[0].after, '更直接，少用感叹号');
  assert.deepEqual(result.unchanged, ['name', 'identity', 'interests', 'boundaries']);
});

test('同时改多个字段时按固定顺序列出，且记录空值与缺失', () => {
  const after = { ...base, name: '小满', interests: '', boundaries: undefined, version: 6 };
  const result = comparePersona(base, after);
  assert.deepEqual(result.changed.map((item) => item.field), ['name', 'interests', 'boundaries']);
  assert.equal(result.changed[1].after, '');
  assert.equal(result.changed[2].after, '');
  assert.equal(result.changed[2].before, '不谈股价');
});

test('回退正文保留目标版本内容并带上当前版本号', () => {
  const target = { ...base, voice: '旧的说话方式', version: 2 };
  const current = { ...base, voice: '新的说话方式', version: 7 };
  const body = rollbackBody(target, current);
  assert.equal(body.version, 7);
  assert.equal(body.voice, '旧的说话方式');
  assert.ok(sameBody(body, target));
  assert.equal(sameBody(body, current), false);
  // The saved response is what the server returns; it becomes v8 with the target content.
  const saved = { ...body, version: 8 };
  assert.ok(sameBody(saved, target));
  assert.deepEqual(comparePersona(target, saved).changed, []);
});
