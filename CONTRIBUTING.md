# Contributing

Contributions are welcome, especially synthetic compatibility fixtures for newer Codex storage formats.

## Development

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python run_cli.py --help
```

Keep runtime dependencies in the Python standard library unless a dependency provides a clear safety benefit. New destructive paths must include a dry-run plan, exact target validation, explicit backup behavior, failure handling, process guards, and isolated tests. UI-only changes should remain usable with keyboard navigation and must not read every complete rollout during the initial scan.

Never commit real `.codex` rollouts, SQLite databases, global state, session IDs, project paths, or conversation excerpts. Use UUID-shaped synthetic IDs and temporary directories.
