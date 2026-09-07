"""
Offline tests for forward-only ML model path joining.

Does not relocate, symlink, or activate models. Run:

    python -m scripts.test_model_path
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.models.versioning import (  # noqa: E402
    canonical_model_dir,
    join_model_dir,
    nested_model_fallback_paths,
)


class ModelPathJoinTests(unittest.TestCase):
    def test_join_uses_basename_only(self) -> None:
        model_dir = Path("data/models")
        out = join_model_dir(model_dir, "btcusdt_buy_v_1.joblib")
        self.assertEqual(out, Path("data/models/btcusdt_buy_v_1.joblib"))

    def test_join_does_not_double_nest_models(self) -> None:
        model_dir = Path("data/models")
        already = Path("data/models/btcusdt_buy_v_1.joblib")
        out = join_model_dir(model_dir, already)
        self.assertEqual(out, Path("data/models/btcusdt_buy_v_1.joblib"))
        self.assertNotIn(Path("models") / "models", out.parents)
        self.assertEqual(out.as_posix(), "data/models/btcusdt_buy_v_1.joblib")

    def test_join_strips_models_prefix(self) -> None:
        out = join_model_dir(Path("data/models"), Path("models/btcusdt_buy_v_1.joblib"))
        self.assertEqual(out, Path("data/models/btcusdt_buy_v_1.joblib"))

    def test_canonical_collapses_models_models(self) -> None:
        self.assertEqual(
            canonical_model_dir(Path("data/models/models")),
            Path("data/models"),
        )
        self.assertEqual(
            canonical_model_dir(Path("data/models")),
            Path("data/models"),
        )

    def test_nested_fallback_candidates(self) -> None:
        cands = nested_model_fallback_paths(
            Path("data/models"), "data/models/btcusdt_buy_v_1.joblib"
        )
        self.assertEqual(cands, [Path("data/models/models/btcusdt_buy_v_1.joblib")])

    def test_get_current_model_path_missing_stays_none_without_fallback(self) -> None:
        from app.models.versioning import get_current_model_path

        missing = {
            "file_path": "/definitely/not/here/btcusdt_buy_v_1.joblib",
        }
        fake_cfg = SimpleCfg(Path("data/models"), fallback=False)
        with patch("app.models.versioning.get_active_model", return_value=missing), \
             patch("app.models.versioning.get_settings", return_value=fake_cfg):
            self.assertIsNone(get_current_model_path("BTCUSDT", "buy"))

    def test_nested_fallback_off_does_not_find_nested_file(self) -> None:
        from app.models.versioning import get_current_model_path

        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "models"
            nested = model_dir / "models"
            nested.mkdir(parents=True)
            joblib = nested / "btcusdt_buy_v_1.joblib"
            joblib.write_bytes(b"dummy")
            registered = {
                "file_path": str(model_dir / "btcusdt_buy_v_1.joblib"),
            }
            fake_cfg = SimpleCfg(model_dir, fallback=False)
            with patch("app.models.versioning.get_active_model", return_value=registered), \
                 patch("app.models.versioning.get_settings", return_value=fake_cfg):
                self.assertIsNone(get_current_model_path("BTCUSDT", "buy"))

    def test_nested_fallback_on_can_find_nested_file(self) -> None:
        from app.models.versioning import get_current_model_path

        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "models"
            nested = model_dir / "models"
            nested.mkdir(parents=True)
            joblib = nested / "btcusdt_buy_v_1.joblib"
            joblib.write_bytes(b"dummy")
            registered = {
                "file_path": str(model_dir / "btcusdt_buy_v_1.joblib"),
            }
            fake_cfg = SimpleCfg(model_dir, fallback=True)
            with patch("app.models.versioning.get_active_model", return_value=registered), \
                 patch("app.models.versioning.get_settings", return_value=fake_cfg):
                found = get_current_model_path("BTCUSDT", "buy")
            self.assertEqual(found, joblib)


class SimpleCfg:
    def __init__(self, model_dir: Path, fallback: bool) -> None:
        self.model_dir = model_dir
        self.model_path_fallback_nested = fallback


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
