"""Comprueba el limite entre recursos publicos y archivos privados."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api.hud import _read_asset


class HudAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        _read_asset.cache_clear()
        self.addCleanup(_read_asset.cache_clear)
        override = patch("api.hud.DIST", self.dist)
        override.start()
        self.addCleanup(override.stop)
        (self.dist / "manifest.json").write_text(
            json.dumps(["allowed.css", "private.env", "../outside.html"]), encoding="utf-8",
        )
        (self.dist / "allowed.css").write_text("body{}", encoding="utf-8")

    def test_registered_asset_is_cached_and_has_correct_mime(self) -> None:
        self.assertEqual(_read_asset("allowed.css"), (b"body{}", "text/css; charset=utf-8"))
        (self.dist / "allowed.css").unlink()
        self.assertEqual(_read_asset("allowed.css")[0], b"body{}")

    def test_unknown_private_and_traversal_names_are_rejected(self) -> None:
        for name in ("not-built.js", "private.env", "../outside.html", ".env"):
            with self.subTest(name=name), self.assertRaises(FileNotFoundError):
                _read_asset(name)

    def test_symbolic_link_is_rejected_before_read(self) -> None:
        with patch("pathlib.Path.is_symlink", return_value=True):
            with self.assertRaises(FileNotFoundError):
                _read_asset("allowed.css")

    def test_invalid_manifest_is_rejected(self) -> None:
        (self.dist / "manifest.json").write_text('{"allowed.css":true}', encoding="utf-8")
        with self.assertRaises(ValueError):
            _read_asset("allowed.css")

    def test_vad_assets_need_manifest_membership_and_correct_mime(self) -> None:
        assets = {"vad.onnx": "application/octet-stream", "ort.wasm": "application/wasm",
                  "ort.mjs": "text/javascript; charset=utf-8", "font.pfb": "application/octet-stream",
                  "font.ttf": "font/ttf", "map.bcmap": "application/octet-stream"}
        (self.dist / "manifest.json").write_text(json.dumps(list(assets)), encoding="utf-8")
        for name, mime in assets.items():
            (self.dist / name).write_bytes(b"fixture")
            self.assertEqual(_read_asset(name), (b"fixture", mime))
        with self.assertRaises(FileNotFoundError):
            _read_asset("private_model.onnx")
