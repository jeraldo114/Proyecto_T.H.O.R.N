"""Regresiones del control previo a publicar: rutas, secretos y plantillas."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.check_repository import GitFile, audit, content_issues, private_path, staged_files


class RepositorySecurityTests(unittest.TestCase):
    def test_private_paths_are_rejected_even_when_forced_into_git(self) -> None:
        for path in (
            ".env", ".env.production", "core/.env", "credentials.env",
            ".venv/Scripts/python.exe", "logs/server.err.log", "backups/db.sql",
            "private.key", "certificate.pfx", "memory.sqlite3", "dump_thorn.sql",
        ):
            with self.subTest(path=path):
                self.assertTrue(private_path(path))
        for path in (".env.example", "core/db_core.py", "requirements.txt", "SECURITY.md"):
            with self.subTest(path=path):
                self.assertFalse(private_path(path))

    def test_template_must_not_contain_private_values(self) -> None:
        content = b"MYSQL_PASSWORD=\nTHORN_API_TOKEN=\nDJANGO_SECRET_KEY=\n"
        self.assertEqual(content_issues(".env.example", content, []), [])
        content += b"MYSQL_PASSWORD=private-test-value\n"
        self.assertTrue(content_issues(".env.example", content, []))

    def test_detected_values_are_not_returned_in_messages(self) -> None:
        secret = b"private-unit-test-value"
        issues = content_issues("core/file.py", b"config = " + secret, [secret])
        self.assertTrue(issues)
        self.assertNotIn(secret.decode(), " ".join(issues))

    def test_token_and_private_key_formats_are_detected(self) -> None:
        fixtures = [
            b"ghp" + b"_" + b"A" * 36,
            b"github" + b"_pat_" + b"A" * 50,
            b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----",
        ]
        for content in fixtures:
            with self.subTest(length=len(content)):
                self.assertTrue(content_issues("core/file.py", content, []))

    def test_symlink_is_not_read_as_a_regular_blob(self) -> None:
        entry = GitFile("120000", "a" * 40, "core/link.py")
        with patch("tools.check_repository.git") as git:
            findings = audit([entry], [])
        self.assertTrue(findings)
        git.assert_not_called()

    def test_index_parser_handles_spaces_in_names(self) -> None:
        record = b"100644 " + b"a" * 40 + b" 0\tdocs/My Notes.md\0"
        with patch("tools.check_repository.git", return_value=record):
            files = staged_files()
        self.assertEqual(files, [GitFile("100644", "a" * 40, "docs/My Notes.md")])

    def test_index_parser_rejects_merge_conflicts(self) -> None:
        record = b"100644 " + b"a" * 40 + b" 2\tcore/runtime.py\0"
        with patch("tools.check_repository.git", return_value=record):
            with self.assertRaises(ValueError):
                staged_files()
