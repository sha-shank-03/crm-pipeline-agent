"""Pure source export and static release controls, never clean-copy build/demo execution."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import clean_copy, release

class SourceExportTests(unittest.TestCase):
    def setUp(self):
        work = release.ROOT / "work"; work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"; self.root.mkdir()
        (self.root / "README.md").write_text("Invented standalone source")
        for directory in ["work", "build", ".deps", ".git", "__pycache__"]:
            (self.root / directory).mkdir(); (self.root / directory / "generated.txt").write_text("ignored")
        (self.root / "AGENTS.md").write_text("local-only instructions")
        (self.root / ".env").write_text("invented local configuration")
        patcher = patch("tools.clean_copy.ROOT", self.root); patcher.start(); self.addCleanup(patcher.stop)

    def test_export_contains_source_and_excludes_private_generated_directories(self):
        target = clean_copy.export(Path(self.temp.name) / "export")
        self.assertEqual([path.name for path in target.iterdir()], ["README.md"])

    def test_existing_output_and_source_ancestor_are_preserved(self):
        for target in [self.root, self.root.parent]:
            with self.assertRaises(ValueError): clean_copy.export(target)
        self.assertEqual((self.root / "README.md").read_text(), "Invented standalone source")

    def test_bad_cached_digest_has_no_output_or_download_fallback(self):
        target = Path(self.temp.name) / "bad-export"
        with patch("tools.build.verify_dependency", side_effect=ValueError("Pinned artifact corrupt")):
            with self.assertRaises(ValueError): clean_copy.export(target, cached=True)
        self.assertFalse(target.exists())

    def make_release_tree(self):
        for relative in release.REQUIRED:
            path = self.root / relative; path.parent.mkdir(parents=True, exist_ok=True)
            original = release.ROOT / relative
            path.write_text(original.read_text(encoding="utf-8") if original.exists() else "Invented release documentation", encoding="utf-8")

    def test_static_release_works_without_git_java_or_dependency_cache(self):
        self.make_release_tree()
        with patch("tools.release.ROOT", self.root), patch("evaluation.freeze.check", return_value={"splits": {"dev": {"case_count": 8}, "heldout": {"case_count": 32}}}):
            self.assertEqual(release.check()["ci_actions_pinned"], 3)

    def test_accidental_binary_secret_and_mutable_action_are_rejected(self):
        self.make_release_tree()
        with patch("tools.release.ROOT", self.root), patch("evaluation.freeze.check", return_value={"splits": {"dev": {"case_count": 8}, "heldout": {"case_count": 32}}}):
            binary = self.root / "accidental.jar"; binary.write_bytes(b"invented")
            with self.assertRaises(ValueError): release.check()
            binary.unlink()
            private = self.root / "credential.txt"; private.write_text("gh" + "p_" + "a" * 24)
            with self.assertRaises(ValueError): release.check()
            private.unlink()
            workflow = self.root / ".github/workflows/ci.yml"
            workflow.write_text(workflow.read_text().replace("actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", "actions/checkout@v7"))
            with self.assertRaises(ValueError): release.check()
