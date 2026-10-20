"""The demo-story page is built from a real run and carries its provenance."""

from __future__ import annotations

import json
from pathlib import Path

from fraudsim.demo.story import run_story


def test_demo_story_page(tmp_path: Path) -> None:
    page = run_story(seed=7, out_dir=tmp_path, model_path=tmp_path / "no_model.joblib")
    html = page.read_text(encoding="utf-8")
    assert "/*__DATA__*/null" not in html  # data was injected
    data_file = next(tmp_path.glob("story_*.json"))
    data = json.loads(data_file.read_text(encoding="utf-8"))
    meta = data["meta"]
    assert meta["run_id"] in html and meta["stream_digest"] in html
    assert meta["seed"] == 7 and meta["scenario"] == "demo_story"
    assert [a["key"] for a in data["approaches"]] == ["cap10", "cap100", "system"]
    truth_ids = {t["ring_id"] for t in data["truth"]}
    assert "hidden_office_ring" in truth_ids
    for a in data["approaches"]:
        assert set(a["missed"]) | {c["true_ring"] for c in a["caught"]} == truth_ids
    # The page is self-contained: no external scripts or stylesheets.
    assert "<script src" not in html and "<link" not in html

    again = run_story(seed=7, out_dir=tmp_path / "b", model_path=tmp_path / "no_model.joblib")
    data_b = json.loads(next((tmp_path / "b").glob("story_*.json")).read_text(encoding="utf-8"))
    assert data_b["meta"]["stream_digest"] == meta["stream_digest"]
    assert data_b["approaches"] == data["approaches"]
    assert again.exists()
