import { copyFileSync, existsSync, mkdirSync, mkdtempSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { describe, it, expect } from 'vitest';

function withRelayProject<T>(
  check: (run: (result: string, mode?: string, exit?: string) => { status: number | null; output: string; events: string }) => T,
  inheritedEnv = process.env,
): T {
  const dir = mkdtempSync(join(tmpdir(), 'wsc-relay-'));
  try {
    mkdirSync(join(dir, 'scripts/lib'), { recursive: true });
    for (const script of ['gemini.sh', 'lib/consumption.py', 'lib/context.py', 'lib/secrets.py', 'lib/safety.py']) {
      copyFileSync(resolve('scripts', script), join(dir, 'scripts', script));
    }
    const fake = join(dir, 'fake agy');
    const prompt = join(dir, 'prompt.txt');
    const answer = join(dir, 'answer.txt');
    const cwd = join(dir, 'child-cwd.txt');
    writeFileSync(join(dir, 'date'), '#!/bin/sh\nprintf "20000101-000000\\n"\n', { mode: 0o700 });
    writeFileSync(fake, '#!/bin/sh\npwd > "$AGY_STUB_CWD"\nprintf "%s\\n" "$AGY_STUB_RESULT"\nexit "$AGY_STUB_EXIT"\n', { mode: 0o700 });
    writeFileSync(prompt, 'A controlled relay task');
    let calls = 0;
    return check((result, mode = 'write', exit = '0') => {
      const run = spawnSync('bash', [join(dir, 'scripts/gemini.sh'), mode, prompt, answer], {
        cwd: dir,
        encoding: 'utf8',
        env: {
          ...inheritedEnv,
          WORKSPACE_AGENT_ROOT: dir,
          WORKSPACE_TASK_ID: 'relay-fixture',
          TASK_ID: 'relay-fixture',
          WORKSPACE_ATTEMPT: '3',
          WORKSPACE_ROLE: 'execution',
          PATH: `${dir}:${inheritedEnv.PATH ?? ''}`,
          AGY_BIN: fake,
          AGY_STUB_RESULT: result,
          AGY_STUB_EXIT: exit,
          AGY_STUB_CWD: cwd,
        },
      });
      expect(run.error).toBeUndefined();
      expect(readFileSync(cwd, 'utf8').trim()).toBe(dir);
      // Exercise the real collector, including rejected envelopes, before deleting its journal.
      const rows = readFileSync(join(dir, '.workspace/metrics/consumption.jsonl'), 'utf8')
        .trim().split('\n').map((line: string) => JSON.parse(line) as Record<string, unknown>);
      expect(rows).toHaveLength(++calls);
      expect(rows.at(-1)).toMatchObject({
        task_id: 'relay-fixture', attempt: 3, role: 'execution', provider: 'antigravity', exit: Number(exit),
      });
      const events = `${run.stdout}\n${run.stderr}`.match(/events in (.+)$/m)?.[1];
      expect(events).toBeDefined();
      expect(events).toContain(join(dir, '.workspace/run/gemini/20000101-000000-'));
      expect(readFileSync(events!, 'utf8')).toBe(`${result}\n`);
      return { status: run.status, output: readFileSync(answer, 'utf8'), events: events! };
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
    expect(existsSync(dir)).toBe(false);
  }
}

function runRelay(result: string, mode = 'write', exit = '0') {
  return withRelayProject((run) => run(result, mode, exit));
}

describe('Gemini relay envelope verification', () => {
  it('keeps synthetic consumption out of the source journal', () => {
    const journal = resolve('.workspace/metrics/consumption.jsonl');
    const before = existsSync(journal) ? readFileSync(journal, 'utf8') : undefined;
    const run = runRelay(JSON.stringify({ status: 'SUCCESS', response: 'Done', denied_actions: [] }));
    expect(run.status).toBe(0);
    expect(existsSync(journal) ? readFileSync(journal, 'utf8') : undefined).toBe(before);
  });
  it('overrides inherited mission roots and attribution with fixture values', () => {
    withRelayProject((run) => {
      expect(run(JSON.stringify({ status: 'SUCCESS', response: 'Done', denied_actions: [] })).status).toBe(0);
    }, {
      ...process.env,
      WORKSPACE_AGENT_ROOT: '/inherited-mission-must-not-be-used',
      WORKSPACE_TASK_ID: 'inherited-mission',
      TASK_ID: 'inherited-mission',
      WORKSPACE_ATTEMPT: '99',
      WORKSPACE_ROLE: 'orchestration',
    });
  });
  it('accepts a completed response after a non-JSON warning', () => {
    const run = runRelay('warning\n'+JSON.stringify({ status: 'SUCCESS', response: 'Written', denied_actions: [] }));
    expect(run.status).toBe(0);
    expect(run.output).toBe('Written\n');
  });
  it.each([
    { status: 'SUCCESS', response: '', denied_actions: [{ display_name: 'RunCommand' }] },
    { status: 'SUCCESS', response: 'Partial', denied_actions: [{ display_name: 'RunCommand' }] },
    { status: 'SUCCESS', response: '  ', denied_actions: [] },
    { status: 'ERROR', response: 'Failed', denied_actions: [] },
  ])('rejects unfinished or denied work: %j', (envelope) => {
    expect(runRelay(JSON.stringify(envelope)).status).toBe(2);
  });
  it('rejects malformed output and nonzero child exit', () => {
    expect(runRelay('not JSON').status).toBe(2);
    expect(runRelay(JSON.stringify({ status: 'SUCCESS', response: 'Done' }), 'review', '1').status).toBe(2);
  });
  it('keeps different log files for calls started in the same second', () => {
    const result = JSON.stringify({ status: 'SUCCESS', response: 'Done', denied_actions: [] });
    withRelayProject((run) => {
      const first = run(result);
      const second = run(result);
      expect(first.status).toBe(0);
      expect(second.status).toBe(0);
      expect(first.events).not.toBe(second.events);
    });
  });
});
