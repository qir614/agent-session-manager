"""Existing Codex behavior behind the shared application interface."""
from pathlib import Path

class CodexApplication:
    id = "codex"
    label = "Codex"
    def default_home(self):
        from ..scanner import default_codex_home
        return default_codex_home()
    def scan(self, home, session_root=None):
        if session_root is not None:
            raise ValueError("Codex does not support --sessions-root; configure CODEX_HOME instead.")
        from ..scanner import SessionScanner
        return SessionScanner(home).scan()
    def read(self, record, include_internal=False):
        from ..conversation_reader import read_conversation
        return read_conversation(record.paths, include_internal=include_internal)
    def plan(self, scan, selected):
        from ..planner import build_plan
        return build_plan(scan, selected)
    def impact(self, scan, plan):
        from ..impact_analyzer import analyze_deletion
        return analyze_deletion(scan, plan)
    def execute(self, plan, **kwargs):
        from ..executor import CleanupExecutor
        return CleanupExecutor().execute(plan, **kwargs)
    def running(self, home=None):
        from ..process_guard import running_codex_processes
        return running_codex_processes()
    def export(self, scan, selected_ids, destination):
        from ..backup import create_manual_backup
        return create_manual_backup(scan, selected_ids, destination, include_state=False)
