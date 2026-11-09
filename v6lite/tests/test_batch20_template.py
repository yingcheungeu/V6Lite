"""Batch 20 synthetic G3-N universe parity; no runtime configuration or state."""
from pathlib import Path
import tomllib
import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples"

@pytest.mark.parametrize("field", ["tradable", "scan", "etf", "protected"])
def test_g3n_universe_matches_formal_paper_template(field):
    normal = tomllib.loads((ROOT / "paper.toml").read_text(encoding="utf-8"))
    g3n = tomllib.loads((ROOT / "paper_g3n.toml").read_text(encoding="utf-8"))
    assert g3n["universe"][field] == normal["universe"][field]

def test_g3n_sector_labels_match_formal_universe():
    normal = tomllib.loads((ROOT / "paper.toml").read_text(encoding="utf-8"))
    g3n = tomllib.loads((ROOT / "paper_g3n.toml").read_text(encoding="utf-8"))
    assert g3n.get("sectors") == normal["sectors"]