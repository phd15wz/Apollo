"""Synthetic checks for observation changes, sharp_move, and the 1 bp spread."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tools.macro.series import (
    DXY_TICKER,
    FRED_DEFAULT_OBSERVATION_START,
    FRED_SERIES,
    LIVE_HARD_GATE_SERIES,
    SHARP_PRIOR_MAX,
    SHARP_PRIOR_MIN,
    SHARP_SIGMA,
    SPREAD_SERIES,
    SPREAD_TOLERANCE_PCT,
)
from tools.macro.transforms import (
    SpreadMismatch,
    assert_published_spread,
    format_spread_skip,
    live_hard_gate,
    observation_changes,
    sharp_move,
    spread_gaps,
)

NY = ZoneInfo("America/New_York")


def test_locked_thresholds() -> None:
    assert FRED_SERIES == ("DGS2", "DGS10", "T10Y2Y", "DFII10")
    assert LIVE_HARD_GATE_SERIES == ("DGS10", "DFII10")
    assert DXY_TICKER == "DX-Y.NYB"
    assert SHARP_PRIOR_MAX == 60
    assert SHARP_PRIOR_MIN == 40
    assert SHARP_SIGMA == 2.0
    assert SPREAD_TOLERANCE_PCT == 0.01
    assert SPREAD_SERIES == ("DGS10", "DGS2", "T10Y2Y")
    assert FRED_DEFAULT_OBSERVATION_START == date(1992, 1, 1)


def test_changes_are_one_and_five_prints() -> None:
    levels = np.arange(7, dtype=np.float64)
    changes = observation_changes(levels)
    assert np.isnan(changes.change_1d[0])
    assert changes.change_1d[1:].tolist() == [1, 1, 1, 1, 1, 1]
    assert np.isnan(changes.change_5d[:5]).all()
    assert changes.change_5d[5] == 5
    assert changes.change_5d[6] == 5


def test_changes_skip_missing_prints() -> None:
    # Holiday slots are NaN. The window counts the prints that exist.
    levels = np.array([1.0, np.nan, np.nan, 1.5, 1.25, 1.75, 2.0, 2.5])
    changes = observation_changes(levels)
    assert np.isnan(changes.change_1d[1])
    assert changes.change_1d[3] == pytest.approx(0.5)
    assert changes.change_1d[4] == pytest.approx(-0.25)
    assert np.isnan(changes.change_5d[6])
    assert changes.change_5d[7] == pytest.approx(1.5)


def test_changes_reject_a_matrix() -> None:
    with pytest.raises(ValueError, match="1-d"):
        observation_changes(np.ones((2, 2)))


def _alternating(count: int) -> np.ndarray:
    return np.resize(np.array([1.0, -1.0]), count)


def test_sharp_move_needs_forty_priors_and_uses_signed_sigma() -> None:
    priors = _alternating(40)
    assert np.std(priors, ddof=0) == pytest.approx(1.0)

    quiet = sharp_move(np.concatenate([priors, [0.5]]))
    assert quiet.sigma[-1] == pytest.approx(1.0)
    assert not quiet.sharp_move[-1]
    assert not quiet.short_sample[-1]

    exact = sharp_move(np.concatenate([priors, [2.0]]))
    assert not exact.sharp_move[-1]
    assert sharp_move(np.concatenate([priors, [-2.0]])).sharp_move[-1] == np.False_

    up = sharp_move(np.concatenate([priors, [2.05]]))
    down = sharp_move(np.concatenate([priors, [-2.05]]))
    assert up.sharp_move[-1] and down.sharp_move[-1]
    assert up.sigma[-1] == pytest.approx(1.0)


def test_current_change_is_excluded_from_its_sigma() -> None:
    priors = _alternating(40)
    current = np.array([50.0])
    result = sharp_move(np.concatenate([priors, current]))
    assert result.sigma[-1] == pytest.approx(1.0)
    included = float(np.std(np.concatenate([priors, current]), ddof=0))
    assert included != pytest.approx(1.0)


def test_sharp_window_is_the_last_sixty_priors() -> None:
    priors = np.concatenate([np.array([100.0]), _alternating(60)])
    assert priors.size == 61
    result = sharp_move(np.concatenate([priors, [2.05]]))
    assert result.sigma[-1] == pytest.approx(1.0)
    assert result.sharp_move[-1]
    whole = float(np.std(priors, ddof=0))
    assert whole != pytest.approx(1.0)


def test_short_sample_under_forty_priors() -> None:
    priors = np.concatenate([_alternating(38), [1.0]])
    assert priors.size == 39
    result = sharp_move(np.concatenate([priors, [5.0]]))
    assert result.short_sample[-1]
    assert not result.sharp_move[-1]
    assert np.isnan(result.sigma[-1])


def test_missing_change_is_not_a_short_sample() -> None:
    result = sharp_move(np.array([np.nan, 0.1]))
    assert not result.sharp_move[0]
    assert not result.short_sample[0]
    assert result.short_sample[1]
    assert not result.sharp_move[1]


def test_nan_prior_does_not_count_toward_forty() -> None:
    priors = _alternating(40).astype(np.float64)
    priors[5] = np.nan
    result = sharp_move(np.concatenate([priors, [2.05]]))
    assert result.short_sample[-1]
    assert not result.sharp_move[-1]


def test_zero_sigma_flags_any_nonzero_change() -> None:
    priors = np.zeros(40)
    flat = sharp_move(np.concatenate([priors, [0.0]]))
    assert flat.sigma[-1] == pytest.approx(0.0)
    assert not flat.sharp_move[-1]
    assert sharp_move(np.concatenate([priors, [0.01]])).sharp_move[-1]
    assert sharp_move(np.concatenate([priors, [-0.01]])).sharp_move[-1]


def test_live_gate_is_dgs10_or_dfii10_only() -> None:
    assert live_hard_gate({"DGS10": True, "DFII10": False})
    assert live_hard_gate({"DFII10": True})
    assert not live_hard_gate({"DGS10": False, "DFII10": False, "DGS2": True, DXY_TICKER: True})
    assert not live_hard_gate({})


def test_spread_within_one_basis_point_passes() -> None:
    day = date(2025, 11, 26)
    assert_published_spread([day], [4.25], [4.00], [0.25])
    # Gap of 1 bp is still inside the band. 0.01 is not a binary integer.
    assert_published_spread([day], [4.25], [4.00], [0.25 + 0.01])


def test_spread_past_one_basis_point_stops_on_that_date() -> None:
    first = date(2025, 11, 26)
    second = date(2025, 11, 28)
    with pytest.raises(SpreadMismatch) as exc:
        assert_published_spread(
            [first, second],
            [4.25, 4.25],
            [4.00, 4.00],
            [0.25 + 0.02, 0.25],
        )
    assert exc.value.obs_date == first
    assert exc.value.gap == pytest.approx(0.02)
    assert "exceeds 0.01" in str(exc.value)
    assert "1 bp" in str(exc.value)


def test_spread_gaps_lists_every_date_past_one_basis_point() -> None:
    first = date(1990, 11, 21)
    second = date(1990, 11, 23)
    gaps = spread_gaps(
        [first, second],
        [8.40, 8.40],
        [7.50, 7.50],
        [0.92, 0.88],
    )
    assert [gap.obs_date for gap in gaps] == [first, second]
    assert gaps[0].dgs10 == pytest.approx(8.40)
    assert gaps[0].dgs2 == pytest.approx(7.50)
    assert gaps[0].published == pytest.approx(0.92)
    assert gaps[0].recomputed == pytest.approx(0.90)
    assert gaps[0].gap == pytest.approx(0.02)
    assert gaps[0].tolerance == 0.01
    assert gaps[0].series_ids == ("DGS10", "DGS2", "T10Y2Y")
    line = format_spread_skip(gaps[0])
    assert "1990-11-21: skipping obs_date" in line
    assert "series DGS10=8.400000 DGS2=7.500000 T10Y2Y=0.920000" in line
    assert "observed T10Y2Y=0.920000" in line
    assert "expected DGS10-DGS2=0.900000" in line
    assert "tolerance=0.01 (1 bp)" in line
    # A gap of exactly 1 bp stays inside the fail-closed band.
    assert spread_gaps([first], [8.40], [7.50], [0.91]) == []


def test_spread_skips_dates_missing_a_level() -> None:
    assert_published_spread(
        [date(2025, 11, 26), date(2025, 11, 28)],
        [4.25, 4.25],
        [4.00, np.nan],
        [0.25, 50.0],
    )


def test_spread_accepts_an_iso_date_on_failure() -> None:
    with pytest.raises(SpreadMismatch) as exc:
        assert_published_spread(["2025-11-26"], [4.0], [3.0], [1.5])
    assert exc.value.obs_date == date(2025, 11, 26)


def test_spread_rejects_a_length_mismatch() -> None:
    with pytest.raises(ValueError, match="same length"):
        assert_published_spread([date(2025, 11, 26)], [4.0, 4.1], [3.0], [1.0])


def test_visible_rows_are_the_only_change_inputs() -> None:
    from tools.macro.availability import select_visible_indices

    levels = np.array([1.0, 2.0, 10.0])
    available_at = [
        datetime(2025, 11, 26, 9, 30, tzinfo=NY),
        datetime(2025, 12, 1, 9, 30, tzinfo=NY),
        datetime(2025, 11, 28, 9, 30, tzinfo=NY),
    ]
    decision = datetime(2025, 11, 28, 9, 30, tzinfo=NY)
    visible = select_visible_indices(available_at, decision)
    assert visible.tolist() == [0, 2]
    changes = observation_changes(levels[visible])
    assert changes.change_1d[-1] == pytest.approx(9.0)
    leaked = observation_changes(levels)
    assert leaked.change_1d[-1] == pytest.approx(8.0)
