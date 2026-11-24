const PACKAGES: Readonly<Record<string, readonly string[]>> = {
  'Node.js': ['node'], TypeScript: ['typescript'],
  'ESLint + typescript-eslint': ['eslint', 'typescript-eslint'], Fastify: ['fastify'],
  'better-sqlite3': ['better-sqlite3'], zod: ['zod'], ulid: ['ulid'],
  '@modelcontextprotocol/sdk': ['@modelcontextprotocol/sdk'], pino: ['pino'],
  React: ['react', 'react-dom'], 'Vite + @vitejs/plugin-react': ['vite', '@vitejs/plugin-react'],
  'Tailwind CSS': ['tailwindcss', '@tailwindcss/vite'], 'shadcn (CLI)': ['shadcn'],
  'React Router': ['react-router'], 'TanStack Query': ['@tanstack/react-query'],
  'react-markdown + remark-gfm': ['react-markdown', 'remark-gfm'], Mermaid: ['mermaid'],
  Vitest: ['vitest', '@vitest/coverage-v8'], Playwright: ['@playwright/test'],
};

export function parsePile(markdown: string): Readonly<Record<string, string>> {
  const section = markdown.split(/^## Pile\s*$/m)[1]?.split(/^## /m)[0] ?? '';
  const result: Record<string, string> = {};
  for (const line of section.split('\n')) {
    const cells = line.split('|').map((s) => s.trim());
    const name = cells[1] ?? '';
    const packages = PACKAGES[name];
    if (!packages) continue;
    const versions: string[] = (cells[2] ?? '').match(/\d+\.\d+\.\d+/g) ?? [];
    if (name === 'Node.js') versions.push((cells[2] ?? '').match(/^\d+/)?.[0] ?? '');
    packages.forEach((pkg, index) => {
      const version = versions[index] ?? versions[0];
      if (version) result[pkg] = version;
    });
  }
  return Object.freeze(result);
}
