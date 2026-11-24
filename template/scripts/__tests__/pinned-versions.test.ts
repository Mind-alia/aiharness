import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { parsePile } from '../lib/pile.js';

const text = (path: string): string => readFileSync(path, 'utf8');
type Manifest = { engines?: { node?: string }; scripts?: { e2e?: string }; dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
type Override = { package: string; expected: string; actual: string };
const manifest = (path: string): Manifest => JSON.parse(text(path)) as Manifest;

describe('pinned toolchain', () => {
  const pile = (): Readonly<Record<string, string>> => parsePile(text('docs/ARCHITECTURE-SPINE.md'));
  it('parses real and compound Pile entries without silently returning an empty map', () => {
    const versions = pile();
    expect(Object.keys(versions).length).toBeGreaterThanOrEqual(15);
    expect(versions['zod']).toBe('4.6.7');
    expect(versions['vite']).toBe('8.3.4');
    expect(versions['@vitejs/plugin-react']).toBe('6.1.2');
    expect(versions['typescript-eslint']).toBe('8.71.2');
    expect(versions['node']).toBe('24');
  });
  it('requires exact dependency versions and documented overrides for Pile packages', () => {
    const overrides = JSON.parse(text('scripts/pile-overrides.json')) as Override[];
    const decisions = text('docs/decisions.md');
    for (const dir of ['.', 'packages/contracts', 'packages/cli', 'apps/service', 'apps/web']) {
      const pkg = manifest(`${dir}/package.json`);
      for (const [name, version] of Object.entries({ ...pkg.dependencies, ...pkg.devDependencies })) {
        if (name.startsWith('@wsc/')) continue;
        expect(version, name).toMatch(/^\d+\.\d+\.\d+$/);
        const expected = pile()[name];
        if (!expected || expected === version) continue;
        expect(overrides).toContainEqual({ package: name, expected, actual: version });
        expect(decisions).toContain(name);
        expect(decisions).toContain(version);
      }
    }
  });
  it('retains the explicitly authorized Node runtime exception and LTS target', () => {
    expect(manifest('package.json').engines?.node).toBe('>=22.12');
    expect(text('.nvmrc').trim()).toBe('24');
    expect(text('docs/decisions.md')).toContain('Node 23.11.0');
  });
  it('does not allow Playwright to skip all journeys once specs exist', () => {
    const specs = existsSync('apps/web/e2e') && readdirSync('apps/web/e2e').some((f) => f.endsWith('.spec.ts'));
    if (specs) expect(manifest('package.json').scripts?.e2e).not.toContain('--pass-with-no-tests');
  });
});
