#!/usr/bin/env node
// Recorder for real Claude Code hook payloads (Slice 01, T02).
// Usage as a hook command: node record-claude-hooks.mjs <HookEventName>
// Appends {"event": <argv[2]>, "payload": <stdin JSON>} as one line to $WSC_FIXTURE_OUT.
// Writes nothing to stdout and always exits 0 so it never disturbs the session.
import { appendFileSync } from 'node:fs';

const event = process.argv[2] ?? 'unknown';
const out = process.env.WSC_FIXTURE_OUT;

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  input += chunk;
});
process.stdin.on('end', () => {
  try {
    if (out) {
      const payload = JSON.parse(input);
      appendFileSync(out, `${JSON.stringify({ event, payload })}\n`);
    }
  } catch {
    // Recorder must stay silent; an unreadable payload is simply not recorded.
  }
  process.exit(0);
});
