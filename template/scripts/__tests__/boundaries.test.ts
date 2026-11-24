import { resolve } from 'node:path';
import { ESLint } from 'eslint';
import { describe, expect, test } from 'vitest';

const root = resolve(import.meta.dirname, '../..');
const fixtureRoot = 'scripts/__tests__/fixtures/boundaries/';
interface EffectiveConfig {
  rules?: Record<string, readonly unknown[]>;
}
const eslint = new ESLint({
  cwd: root,
  ignore: false,
});

describe('architecture boundaries', () => {
  test.each([
    ['apps/service/src/domain', 10],
    ['apps/service/src/ports', 5],
    ['apps/service/src/adapters', 3],
    ['apps/service/src/http', 2],
    ['apps/web/src', 4],
    ['packages/cli/src', 2],
    ['packages/contracts/src', 3],
  ])('rejects every prohibited dependency in %s', async (zone, count) => {
    const [result] = await eslint.lintFiles(`${fixtureRoot}${zone}/negative.ts`);
    expect(result?.messages.filter((message) => message.ruleId === 'no-restricted-imports')).toHaveLength(count);
    expect(result?.fatalErrorCount).toBe(0);
  });

  test.each(['apps/service/src/domain', 'apps/service/src/ports', 'apps/service/src/http'])
  ('allows collaborators permitted in %s', async (zone) => {
    const [result] = await eslint.lintFiles(`${fixtureRoot}${zone}/positive.ts`);
    expect(result?.messages).toEqual([]);
  });

  test.each(['apps/service/src/domain', 'apps/service/src/ports', 'apps/service/src/adapters',
    'apps/service/src/http', 'apps/web/src', 'packages/cli/src', 'packages/contracts/src'])
  ('enforces the rule on the real %s directory', async (zone) => {
    const config = await eslint.calculateConfigForFile(`${zone}/boundary-probe.ts`) as EffectiveConfig | undefined;
    expect(config?.rules?.['no-restricted-imports']?.[0]).toBe(2);
  });

  test.each(['ports', 'adapters', 'http'])('allows integration collaborators in %s tests', async (zone) => {
    const config = await eslint.calculateConfigForFile(`apps/service/src/${zone}/store.test.ts`) as EffectiveConfig | undefined;
    expect(config?.rules?.['no-restricted-imports']).toBeUndefined();
    const nested = await eslint.calculateConfigForFile(`apps/service/src/${zone}/__tests__/helper.ts`) as EffectiveConfig | undefined;
    expect(nested?.rules?.['no-restricted-imports']).toBeUndefined();
  });

  test('domain tests retain the domain boundary', async () => {
    const config = await eslint.calculateConfigForFile('apps/service/src/domain/journal.test.ts') as EffectiveConfig | undefined;
    expect(config?.rules?.['no-restricted-imports']?.[0]).toBe(2);
  });

  test('normal lint ignores deliberately invalid witnesses', async () => {
    const normalLint = new ESLint({ cwd: root });
    expect(await normalLint.isPathIgnored(`${fixtureRoot}apps/web/src/negative.ts`)).toBe(true);
  });

  test('ports cannot escape into unlisted service modules through relative imports or exports', async () => {
    const [result] = await eslint.lintFiles(`${fixtureRoot}apps/service/src/ports/escape.ts`);
    expect(result?.messages.filter((message) => message.ruleId === 'boundaries/ports-imports')).toHaveLength(3);
    expect(result?.fatalErrorCount).toBe(0);
  });

  test('nested ports may import and re-export sibling ports through normalized relative paths', async () => {
    const [result] = await eslint.lintFiles(`${fixtureRoot}apps/service/src/ports/nested/positive.ts`);
    expect(result?.messages).toEqual([]);
  });
});
