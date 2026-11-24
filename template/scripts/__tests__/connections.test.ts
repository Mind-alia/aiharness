import { createServer } from 'node:http';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawn } from 'node:child_process';
import { afterEach, expect, test } from 'vitest';
const dirs: string[] = [];
afterEach(async () => { await Promise.all(dirs.splice(0).map(dir => rm(dir, { recursive: true, force: true }))); });
function run(args: string[], input = ''): Promise<{ code: number | null; output: string }> {
  return new Promise((resolveResult, reject) => {
    const child = spawn('python3', [resolve('scripts/connections.py'), ...args], { stdio: ['pipe', 'pipe', 'pipe'] });
    let output = ''; child.stdout.on('data', (data: Buffer) => { output += data.toString(); });
    child.stderr.on('data', (data: Buffer) => { output += data.toString(); });
    child.on('error', reject); child.on('close', code => resolveResult({ code, output })); child.stdin.end(input);
  });
}
test('forwards an explicit Codex transcript to authenticated local HTTP without text, arguments or tool outputs', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wsc-bridge-')); dirs.push(root);
  const bodies: Record<string, unknown>[] = [];
  const server = createServer((req, res) => {
    expect(req.headers.authorization).toBe('Bearer test-only-secret');
    expect(['/api/v1/hooks/codex', '/api/v1/hooks/google']).toContain(req.url);
    let raw = ''; req.on('data', (chunk: Buffer) => { raw += chunk.toString(); });
    req.on('end', () => { bodies.push(JSON.parse(raw) as Record<string, unknown>); res.writeHead(202); res.end('{"accepted":true,"duplicate":false}'); });
  });
  await new Promise<void>(r => server.listen(0, '127.0.0.1', r));
  try {
    const address = server.address(); if (!address || typeof address === 'string') throw Error('no port');
    await mkdir(join(root, '.workspace/run'), { recursive: true });
    await mkdir(join(root, 'home'));
    await writeFile(join(root, '.workspace/run/token'), 'test-only-secret');
    await writeFile(join(root, 'home/service.lock'), JSON.stringify({ port: address.port, projectDir: root, pid: process.pid }));
    const transcript = join(root, 'session.jsonl');
    await writeFile(transcript, [
      { type: 'session_meta', timestamp: '2026-10-13T12:00:00Z', payload: { id: 'current', cwd: root } },
      { type: 'response_item', timestamp: '2026-10-13T12:00:01Z', payload: { type: 'custom_tool_call', name: 'exec', call_id: 'c1', input: 'PRIVATE-CODE' } },
      { type: 'response_item', timestamp: '2026-10-13T12:00:02Z', payload: { type: 'custom_tool_call_output', call_id: 'c1', output: 'PRIVATE-OUTPUT' } },
      { type: 'response_item', timestamp: '2026-10-13T12:00:03Z', payload: { type: 'reasoning', content: 'PRIVATE-THOUGHT' } },
    ].map(row => JSON.stringify(row)).join('\n') + '\n');
    const result = await run(['codex', '--project', root, '--home', join(root, 'home'), '--transcript', transcript, '--once']);
    expect(result.code).toBe(0); expect(bodies).toHaveLength(3);
    expect(JSON.stringify(bodies)).not.toContain('PRIVATE-');
    expect(result.output).not.toContain('test-only-secret');
    await writeFile(transcript, '');
    expect((await run(['codex', '--project', root, '--home', join(root, 'home'), '--transcript', transcript, '--once'])).code).toBe(1);
    await writeFile(transcript, JSON.stringify({ type: 'session_meta', payload: { id: 'sibling', cwd: '/unrelated-project' } }) + '\n');
    expect((await run(['codex', '--project', root, '--home', join(root, 'home'), '--transcript', transcript, '--once'])).code).toBe(1);
    expect(bodies).toHaveLength(3);
    const hook = { conversationId: 'native', workspacePaths: [root], stepIdx: 7,
      toolCall: { name: 'run_command', args: { CommandLine: 'PRIVATE-COMMAND', AbsolutePath: `${root}/docs/STATUS.md` } } };
    const capture = join(root, 'captured.jsonl');
    expect(await run(['google-hook', '--project', root, '--home', join(root, 'home'), '--capture', capture, 'PostToolUse'], JSON.stringify(hook))).toEqual({ code: 0, output: '{}\n' });
    expect(bodies).toHaveLength(4);
    expect(bodies[3]).toMatchObject({ provider: 'google', event: 'PostToolUse', payload: { conversationId: 'native' } });
    expect(await readFile(capture, 'utf8')).not.toContain('PRIVATE-COMMAND');
    await run(['google-hook', '--project', root, '--home', join(root, 'home'), 'PostToolUse'], JSON.stringify({ ...hook, workspacePaths: ['/unrelated-project'] }));
    expect(bodies).toHaveLength(4);
  } finally { await new Promise<void>((r, reject) => server.close(error => error ? reject(error) : r())); }
});

test('Antigravity observation never changes permissions or blocks an agent when the console is stopped', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wsc-hook-offline-')); dirs.push(root);
  const result = await run(['google-hook', '--project', root, 'PostToolUse'], JSON.stringify({ conversationId: 'x', workspacePaths: [root], toolCall: { name: 'view_file' } }));
  expect(result).toEqual({ code: 0, output: '{}\n' });
  expect(JSON.parse((await readFile(join(root, '.workspace/run/spool.jsonl'), 'utf8')).trim())).toMatchObject({ provider: 'google', event: 'PostToolUse', payload: { conversationId: 'x' } });
});


test('offline Antigravity spool stays below the service replay limit without affecting agent permissions', async () => {
  const root = await mkdtemp(join(tmpdir(), 'wsc-spool-bound-')); dirs.push(root);
  await mkdir(join(root, '.workspace/run'), { recursive: true });
  const path = join(root, '.workspace/run/spool.jsonl');
  const full = 'x'.repeat(10 * 1024 * 1024);
  await writeFile(path, full);
  const result = await run(['google-hook', '--project', root, 'Stop'], JSON.stringify({ conversationId: 'x', workspacePaths: [root] }));
  expect(result).toEqual({ code: 0, output: '{}\n' });
  expect((await readFile(path, 'utf8')).length).toBe(full.length);
});
