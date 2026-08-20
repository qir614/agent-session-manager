# Security Policy

## Scope

Codex Local Session Cleaner works with private local conversation records. It does not require network access and should only be downloaded from a source you trust.

## Safe use

- Review the per-store deletion preview before confirming. Persistent pre-delete state snapshots are disabled by default; session archives are export-only and do not restore complete state.
- Completely quit Codex before cleanup, state restore, backup deletion, or database maintenance.
- Keep session archives and cleanup snapshots private. Archives contain selected rollouts; snapshots can contain complete local state and log databases.
- Import only trusted manifests. Version 0.6.0 rejects absolute, drive, UNC, traversal, out-of-root, symlink, and junction targets before restore or managed deletion.
- Do not publish real `.codex` data, manifests, or cleanup summaries in bug reports.
- Reproduce issues with synthetic fixtures whenever possible.

## Reporting vulnerabilities

Open a GitHub security advisory rather than a public issue when a report involves path traversal, unintended deletion, backup disclosure, confirmation bypass, or unsafe state mutation. Remove all conversation content, usernames, paths, and session IDs from the report.

## Compatibility failures

An unfamiliar Codex schema is treated as a safety event: the tool should stop before mutation. Please report the tool version, Python version, operating system, table/field names, and a synthetic reproduction. Do not attach the real database.
