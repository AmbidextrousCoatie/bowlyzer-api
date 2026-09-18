"""Batched tournament standings: podiums + player history vs per-event KO ranks."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bowlyzerapi.warehouse import warehouse_path

GOLD = Path(__file__).with_name("_tournament_places_baseline.json")


def test_podiums_and_player_history_match_previous_places() -> None:
    from bowlyzerapi.queries.player import player_tournaments
    from bowlyzerapi.queries.tournament import tournament_podiums

    if not warehouse_path().is_file():
        pytest.skip("no warehouse")
    if not GOLD.is_file():
        pytest.skip("no gold dump")

    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    podiums = tournament_podiums(n=3)
    podium_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    history = player_tournaments("Feller, Christian")
    hist_ms = (time.perf_counter() - t0) * 1000
    print(f"podiums-all {podium_ms:.0f} ms groups={len(podiums['podiums'])}")
    print(f"player_tournaments Feller {hist_ms:.0f} ms results={len(history['results'])}")
    assert podium_ms < 1200, f"podiums still too slow: {podium_ms:.0f} ms"
    assert hist_ms < 400, f"player_tournaments still too slow: {hist_ms:.0f} ms"

    got_podiums = [
        {"season": row["season"], "tournament": row["tournament"], "finishers": row["finishers"]}
        for row in podiums["podiums"]
    ]
    assert got_podiums == gold["podiums"]
    got_hist = [
        {"season": row["season"], "tournament": row["tournament"], "position": row["position"]}
        for row in history["results"]
    ]
    assert got_hist == gold["feller"]
