import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from swingbot import research

ROOT = Path(__file__).resolve().parent.parent


def months(values, start="2020-01-31"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="ME"))


def test_after_tax_carries_a_loss_year_into_the_next():
    x = months([0.0] * 11 + [-0.2] + [0.0] * 11 + [0.5])  # 2020: -20%, 2021: +50%
    # 1 -> 0.8, gain 0.4 of which 0.2 is offset by the carried loss: tax 20.8% on 0.2
    assert research.after_tax_cagr(x) == pytest.approx((1.2 - 0.208 * 0.2) ** 0.5 - 1)


def test_hold_pays_long_term_tax_once_at_the_end():
    x = months([0.01] * 24)
    end = 1.01 ** 24
    assert research.hold_after_tax_cagr(x) == pytest.approx((end - 0.13 * (end - 1)) ** 0.5 - 1)
    assert research.hold_after_tax_cagr(x) > research.after_tax_cagr(x)  # deferral and the lower rate


def test_tax_matches_the_frozen_study_copy():
    path = ROOT / "scripts" / "research" / "weekly_rules_study.py"
    spec = importlib.util.spec_from_file_location("weekly_rules_study", path)
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    x = months(np.random.default_rng(0).normal(0.01, 0.06, 120))
    assert research.after_tax_cagr(x, 12) == pytest.approx(study.after_tax_cagr(x, 12))


def test_more_trials_deflate_the_same_sharpe():
    rng = np.random.default_rng(1)
    x = pd.Series(rng.normal(0.01, 0.04, 150))
    trial_srs = pd.Series(rng.normal(0.1, 0.05, 50))
    few, many = research.deflated_sharpe(x, trial_srs, 5)[0], research.deflated_sharpe(x, trial_srs, 500)[0]
    assert 0 < many < few < 1


def test_ledger_missing_names_unlisted_preregs(tmp_path):
    (tmp_path / "a-prereg.md").write_text("")
    (tmp_path / "b-prereg.md").write_text("")
    (tmp_path / "trials-ledger.md").write_text("| x | [A](a-prereg.md) | fail |\n")
    assert research.ledger_missing(tmp_path) == ["b-prereg.md"]
