import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const root = fileURLToPath(new URL('../../', import.meta.url));
const builds = new Map<string, Promise<void>>();
const execute = promisify(execFile);
// Run the TypeScript compiler with this Node: `npx` is a `.cmd` shim on Windows that execFile cannot start.
const tscBin = fileURLToPath(new URL('../../node_modules/typescript/bin/tsc', import.meta.url));
// A repository-wide lock also serializes packages sharing project references.
const buildLock = join(tmpdir(), `wsc-tsc-${createHash('sha256').update(root).digest('hex').slice(0, 20)}.lock`);

async function build(project: string): Promise<void> {
  const started = Date.now();
  for (;;) {
    try { await mkdir(buildLock); break; }
    catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'EEXIST')) throw error;
      if (Date.now() - started > 120_000) throw new Error('Timed out waiting for the test build lock');
      const info = await stat(buildLock).catch(() => undefined);
      if (info && Date.now() - info.mtimeMs > 180_000) await rm(buildLock, { recursive: true, force: true });
      else await setTimeout(25);
    }
  }
  try { await execute(process.execPath, [tscBin, '-b', project], { cwd: root, timeout: 120_000 }); }
  finally { await rm(buildLock, { recursive: true, force: true }); }
}

export function buildOnce(project: string): Promise<void> {
  const key = resolve(root, project);
  let pending = builds.get(key);
  if (!pending) {
    pending = build(key).catch((error: unknown) => { builds.delete(key); throw error; });
    builds.set(key, pending);
  }
  return pending;
}
