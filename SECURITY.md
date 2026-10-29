# Security Policy

## Scope

Agent Session Manager works with private local conversation records. It does not require network access and should only be downloaded from a source you trust.

## Safe use

- Review the per-store deletion preview before confirming. Persistent pre-delete state snapshots are disabled by default; session archives are export-only and do not restore complete state.
- Completely quit Codex before cleanup, state restore, backup deletion, or database maintenance.
- Keep session archives and cleanup snapshots private. Archives contain selected rollouts; snapshots can contain complete local state and log databases.
- Import only trusted manifests. Rolling builds reject absolute, drive, UNC, traversal, out-of-root, symlink, and junction targets before restore or managed deletion.
- Conversation deletion removes only uniquely attributable artifacts inside managed `.codex` roots. Real projects and uploaded originals are never implicit deletion targets.
- Shared artifacts and recoverable backups are reported as retained; their presence prevents a misleading full-purge result.
- Historical residue cleanup uses a seven-day grace period, exact surviving-rollout references, strict managed-root containment, reparse-point refusal, preview fingerprinting, explicit `CLEAN-N` confirmation, and a temporary rollback snapshot. Recent or ambiguous items and all backups are report-only.
- Multiple rollout files with one conversation ID are not inherently duplicates. Compatible segments are grouped; conflicting project, source, or parent metadata blocks mutation and reports every involved path.
- SQLite secure deletion removes logical-file remnants but cannot guarantee physical erasure from SSD firmware, filesystem snapshots, or external backups.
- Do not publish real `.codex` data, manifests, or cleanup summaries in bug reports.
- Reproduce issues with synthetic fixtures whenever possible.

## Reporting vulnerabilities

Open a GitHub security advisory rather than a public issue when a report involves path traversal, unintended deletion, backup disclosure, confirmation bypass, or unsafe state mutation. Remove all conversation content, usernames, paths, and session IDs from the report.

## Compatibility failures

An unfamiliar Codex schema is treated as a safety event: the tool should stop before mutation. Please report the tool version, Python version, operating system, table/field names, and a synthetic reproduction. Do not attach the real database.
