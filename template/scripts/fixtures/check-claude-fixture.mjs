#!/usr/bin/env node
// Checks the recorded Claude Code hook fixture (Slice 01, CA-21). No dependency.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const dir = join(root, 'apps/service/fixtures/claude-code');
const fixturePath = join(dir, 'hooks-session-basique.jsonl');
const readmePath = join(dir, 'README.md');
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const errors = [];
const fail = (message) => errors.push(message);

if (!existsSync(fixturePath)) {
  fail(`missing fixture: ${fixturePath}`);
} else {
  const lines = readFileSync(fixturePath, 'utf8').split('\n').filter((l) => l.trim() !== '');
  const rows = [];
  lines.forEach((line, index) => {
    try {
      const row = JSON.parse(line);
      if (typeof row.event !== 'string' || typeof row.payload !== 'object' || row.payload === null) {
        fail(`line ${index + 1}: expected { event, payload }`);
      } else {
        rows.push(row);
      }
    } catch {
      fail(`line ${index + 1}: not valid JSON`);
    }
  });

  for (const [index, row] of rows.entries()) {
    const id = row.payload.session_id;
    if (typeof id !== 'string' || !UUID.test(id)) fail(`line ${index + 1}: session_id is not a UUID`);
  }

  const has = (event, predicate = () => true) =>
    rows.some((r) => r.event === event && predicate(r.payload));
  const tool = (name) => (p) => p.tool_name === name;
  const writer = (p) => p.tool_name === 'Write' || p.tool_name === 'Edit';
  const delegation = (p) => p.tool_name === 'Task' || p.tool_name === 'Agent';

  const required = [
    ['SessionStart', has('SessionStart')],
    ['UserPromptSubmit', has('UserPromptSubmit')],
    ['PreToolUse Read', has('PreToolUse', tool('Read'))],
    ['PostToolUse Read', has('PostToolUse', tool('Read'))],
    ['PreToolUse Bash', has('PreToolUse', tool('Bash'))],
    ['PostToolUse Bash', has('PostToolUse', tool('Bash')) || has('PostToolUseFailure', tool('Bash'))],
    ['PreToolUse Write/Edit', has('PreToolUse', writer)],
    ['PostToolUse Write/Edit', has('PostToolUse', writer)],
    ['tool failure', has('PostToolUseFailure') || rows.some((r) => r.event === 'PostToolUse' && isFailure(r.payload))],
    ['sub-agent delegation (Task/Agent)', has('PreToolUse', delegation)],
    ['SubagentStop', has('SubagentStop')],
    ['Stop', has('Stop')],
  ];
  for (const [name, ok] of required) if (!ok) fail(`missing required hook: ${name}`);
}

function isFailure(payload) {
  const response = payload.tool_response;
  if (response === null || typeof response !== 'object') return false;
  return response.is_error === true || response.isError === true || response.success === false || (typeof response.exit_code === 'number' && response.exit_code !== 0) || (typeof response.exitCode === 'number' && response.exitCode !== 0) || typeof response.error === 'string';
}

if (!existsSync(readmePath)) {
  fail(`missing README: ${readmePath}`);
} else {
  const readme = readFileSync(readmePath, 'utf8');
  if (!readme.includes('2.1.296')) fail('README does not mention version 2.1.296');
  if (!/\d{4}-\d{2}-\d{2}/.test(readme)) fail('README does not mention the recording date');
  if (!readme.includes('claude -p')) fail('README does not mention the recording command (claude -p)');
}

if (errors.length > 0) {
  for (const e of errors) console.error(`check-claude-fixture: ${e}`);
  process.exit(1);
}
console.log('check-claude-fixture: ok');
