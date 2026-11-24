import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, test } from 'vitest';

const REPO_ROOT = resolve(import.meta.dirname, '..', '..');
const EXPECTED_WORKSPACE_LOCATIONS = [
  'packages/contracts',
  'packages/cli',
  'apps/service',
  'apps/web',
];

interface WorkspaceNode {
  readonly location: string;
}

function listWorkspaceLocations(): string[] {
  // On Windows `npm` is a `.cmd` shim that only a shell can start; the arguments are constant.
  const output = execFileSync('npm', ['query', '.workspace'], {
    cwd: REPO_ROOT,
    shell: process.platform === 'win32',
    encoding: 'utf8',
  });
  const nodes = JSON.parse(output) as WorkspaceNode[];
  return nodes.map((node) => node.location);
}

describe('npm workspaces', () => {
  test('lists the four workspace locations of the slice', () => {
    // Arrange
    // (repository root resolved above)

    // Act
    const locations = listWorkspaceLocations();

    // Assert
    for (const expected of EXPECTED_WORKSPACE_LOCATIONS) {
      expect(locations).toContain(expected);
    }
  });
});
