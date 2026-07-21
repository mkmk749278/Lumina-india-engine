"""v2_calibration — the pre-activation monotonicity check for scoring v2."""

from __future__ import annotations

import sqlite3

from tools import v2_calibration as vc


def _make_db(path: str, rows: list[tuple]) -> None:
    """rows: (confidence, confidence_v2, outcome, pct)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE india_signals (
            signal_id TEXT, confidence REAL, confidence_v2 REAL
        );
        CREATE TABLE india_signal_outcomes (
            signal_id TEXT, outcome TEXT, pct REAL,
            created_at TEXT DEFAULT (DATE('now','localtime'))
        );
        """
    )
    for i, (c, c2, out, pct) in enumerate(rows):
        sid = f"s{i}"
        conn.execute(
            "INSERT INTO india_signals VALUES (?,?,?)", (sid, c, c2)
        )
        conn.execute(
            "INSERT INTO india_signal_outcomes (signal_id, outcome, pct)"
            " VALUES (?,?,?)",
            (sid, out, pct),
        )
    conn.commit()
    conn.close()


def test_excludes_not_triggered(tmp_path) -> None:
    db = str(tmp_path / "t.sqlite3")
    _make_db(db, [
        (60, 60, "TP1_HIT", 0.4),
        (60, 60, "NOT_TRIGGERED", 0.0),
    ])
    rows = vc.load_rows(db, 30)
    assert len(rows) == 1


def test_calibrate_bands_and_winrate(tmp_path) -> None:
    db = str(tmp_path / "t.sqlite3")
    _make_db(db, [
        (62, 62, "TP1_HIT", 0.5),
        (62, 62, "SL_HIT", -0.3),   # 62-65 band: 1/2 win
        (72, 72, "TP1_HIT", 0.6),   # 70-75 band: 1/1 win
    ])
    rows = vc.load_rows(db, 30)
    cal = vc.calibrate(rows, "confidence")
    bands = {b["band"]: b for b in cal}
    assert bands["60-65"]["n"] == 2
    assert bands["60-65"]["win_pct"] == 50.0
    assert bands["70-75"]["win_pct"] == 100.0


def test_monotonic_detection() -> None:
    up = [{"win_pct": 30.0}, {"win_pct": 40.0}, {"win_pct": 55.0}]
    inverted = [{"win_pct": 50.0}, {"win_pct": 30.0}]
    assert vc.is_monotonic(up) is True
    assert vc.is_monotonic(inverted) is False


def test_main_flags_inverted_v2_not_ready(tmp_path, capsys) -> None:
    db = str(tmp_path / "t.sqlite3")
    # v2 inverted: high band loses, low band wins -> NOT READY (exit 1).
    _make_db(db, [
        (58, 78, "SL_HIT", -0.3),
        (58, 78, "SL_HIT", -0.2),
        (78, 58, "TP1_HIT", 0.5),
        (78, 58, "TP1_HIT", 0.4),
    ])
    rc = vc.main(["--db", db, "--days", "30"])
    assert rc == 1
    assert "NOT READY" in capsys.readouterr().out


def test_main_ready_when_v2_monotonic(tmp_path) -> None:
    db = str(tmp_path / "t.sqlite3")
    _make_db(db, [
        (58, 58, "SL_HIT", -0.3),
        (58, 58, "SL_HIT", -0.2),
        (72, 72, "TP1_HIT", 0.5),
        (72, 72, "TP1_HIT", 0.4),
    ])
    rc = vc.main(["--db", db, "--days", "30"])
    assert rc == 0
