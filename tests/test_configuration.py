"""Configuration scopes, schema upgrade, and the config CLI surface."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_session_manager import __version__
from agent_session_manager import cli
from agent_session_manager.configuration import (
    SCHEMA_VERSION,
    AppSettings,
    CodexSettings,
    DshSettings,
    GeneralSettings,
    load_settings,
    save_settings,
)
from agent_session_manager.configuration import store
from agent_session_manager.settings import AppSettings as ShimAppSettings


class ConfigurationMigrationTests(unittest.TestCase):
    def test_defaults_use_general_sections(self):
        settings = AppSettings()
        general, codex, dsh = settings.sections()
        self.assertEqual(general.language, "zh-CN")
        self.assertEqual(general.confirmation_text, "DELETE")
        self.assertEqual(codex.codex_binary, "")
        self.assertEqual(dsh.dsh_home, "")
        self.assertEqual(dsh.session_root, "")

    def test_legacy_flat_document_is_read_and_migrated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "settings.json"
            legacy = root / "LegacyCleaner" / "settings.json"
            legacy.parent.mkdir()
            legacy.write_text(json.dumps({
                "backup_before_delete": True,
                "codex_binary": "C:/bin/codex.exe",
                "confirmation_text": "REMOVE {count}",
                "language": "fr",
                "backup_retention_days": -3,
            }), encoding="utf-8")
            platform = SimpleNamespace(
                settings_path=lambda: target,
                legacy_settings_paths=lambda: (legacy,),
            )
            with patch.object(store, "get_platform", return_value=platform):
                settings = load_settings()
                self.assertTrue(settings.backup_before_delete)
                self.assertEqual(settings.codex_binary, "C:/bin/codex.exe")
                self.assertEqual(settings.confirmation_text, "REMOVE {count}")
                self.assertEqual(settings.language, "zh-CN")
                self.assertEqual(settings.backup_retention_days, 0)
                self.assertTrue(target.is_file(), "legacy file must be migrated eagerly")
                document = json.loads(target.read_text(encoding="utf-8"))
                self.assertEqual(document["schema_version"], SCHEMA_VERSION)
                self.assertEqual(
                    sorted(document["applications"]), ["codex", "dsh"],
                )

    def test_scopes_are_written_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "settings.json"
            settings = AppSettings(
                show_internal_messages=True,
                codex_binary="C:/codex.exe",
                dsh_home="D:/dsh-home",
                dsh_session_root="D:/dsh-sessions",
            )
            save_settings(settings, target)
            document = json.loads(target.read_text(encoding="utf-8"))
            self.assertNotIn("codex_binary", document["general"])
            self.assertNotIn("dsh_home", document["general"])
            self.assertEqual(document["applications"]["codex"]["codex_binary"], "C:/codex.exe")
            self.assertEqual(document["applications"]["dsh"]["dsh_home"], "D:/dsh-home")
            restored = load_settings(target)
            self.assertEqual(restored.dsh_session_root, "D:/dsh-sessions")
            self.assertTrue(restored.show_internal_messages)

    def test_sectioned_document_is_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "settings.json"
            target.write_text(json.dumps({
                "schema_version": SCHEMA_VERSION,
                "general": {"language": "en", "backup_retention_count": 4, "theme": "dark"},
                "applications": {
                    "codex": {"codex_binary": "/usr/bin/codex"},
                    "dsh": {"dsh_home": "/home/u/.dsh", "session_root": "/home/u/.dsh/sessions"},
                },
            }), encoding="utf-8")
            settings = load_settings(target)
            self.assertEqual(settings.language, "en")
            self.assertEqual(settings.theme, "dark")
            self.assertEqual(settings.backup_retention_count, 4)
            self.assertEqual(settings.codex_binary, "/usr/bin/codex")
            self.assertEqual(settings.dsh_home, "/home/u/.dsh")
            self.assertEqual(settings.dsh_session_root, "/home/u/.dsh/sessions")

    def test_invalid_section_values_fall_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "settings.json"
            target.write_text(json.dumps({
                "schema_version": SCHEMA_VERSION,
                "general": {"language": "de", "theme": "neon", "backup_before_delete": "yes"},
                "applications": {"codex": {"codex_binary": 7}},
            }), encoding="utf-8")
            settings = load_settings(target)
            self.assertEqual(settings.language, "zh-CN")
            self.assertEqual(settings.theme, "system")
            self.assertFalse(settings.backup_before_delete)
            self.assertEqual(settings.codex_binary, "")

    def test_corrupt_document_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "settings.json"
            target.write_text("{not json", encoding="utf-8")
            self.assertEqual(load_settings(target), AppSettings())

    def test_flat_settings_facade_reexports_store_types(self):
        self.assertIs(ShimAppSettings, AppSettings)

    def test_sections_roundtrip(self):
        settings = AppSettings.from_sections(
            GeneralSettings(theme="light"),
            CodexSettings(codex_binary="codex"),
            DshSettings(dsh_home="/h", session_root="/h/sessions"),
        )
        self.assertEqual(settings.theme, "light")
        self.assertEqual(settings.codex_binary, "codex")
        self.assertEqual(settings.dsh_session_root, "/h/sessions")


class ConfigurationCliTests(unittest.TestCase):
    def test_version_reports_product_and_release(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        text = output.getvalue()
        self.assertIn("agent-session-manager", text)
        self.assertIn(__version__, text)

    def test_config_command_reports_paths_and_values(self):
        platform = SimpleNamespace(legacy_settings_paths=lambda: ())
        output = io.StringIO()
        with patch.object(cli, "load_settings", return_value=AppSettings(codex_binary="c:/codex.exe")), \
             patch.object(cli, "settings_path", return_value=Path("C:/settings.json")), \
             patch.object(cli, "get_platform", return_value=platform), \
             redirect_stdout(output):
            code = cli.main(["config", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["settings_path"], "C:\\settings.json")
        self.assertEqual(payload["settings"]["codex_binary"], "c:/codex.exe")


if __name__ == "__main__":
    unittest.main()
