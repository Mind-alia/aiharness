# Security

aiharness starts AI agents that write and run code on your machine. Read this before running it.

## What protects you

- Agents work in a container with the network off, on a copy of the project outside the main repository, within a
  scope written in the task sheet.
- Guard rails refuse writes outside the scope, deletions, pushes, and secret-looking variables in task sheets.
- Keys stay in `~/.config/workspace-ia/secrets.env` (mode 0600) on your machine; nothing in this repository contains one.
- The attack bench (`docs/security/`) tries known escapes against the sandbox; its reports show what held and what did
  not on the day it ran.

## Limits

- Outside a container, an agent can usually read files elsewhere on your disk; some tools' own sandboxes allow reads.
- The attack bench covers the escapes it knows, not every possible one. Run it again after changing the circuit or
  updating an agent tool.

## Reporting a problem

Please open a private security advisory on the repository rather than a public issue.
