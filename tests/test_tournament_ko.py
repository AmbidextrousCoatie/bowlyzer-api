"""KO brackets from tournament_line-shaped rows + publish-dir config."""

from __future__ import annotations

from bowlyzerapi.queries.tournament_ko import (
    apply_ko_ranks,
    build_ko_bracket,
    config_entry,
    format_ko_fields,
)

SEASON = "25/26"
CLUB = "Clubmeisterschaft Donaubowler 2026"
BM = "Bayerische Meisterschaft - Männer Einzel"


def _row(
    *,
    round_name: str,
    round_number: int,
    game: int,
    player: str,
    score: int,
    handicap: int = 0,
    club: str = "Donaubowler",
    player_id: str = "",
    stage_rank: int | None = None,
) -> dict:
    return {
        "player_name": player,
        "player_id": player_id or player[:3].upper(),
        "club": club,
        "round_number": round_number,
        "round_name": round_name,
        "game_number": game,
        "score": score,
        "handicap": handicap,
        "stage_rank": stage_rank,
    }


def _finale_games() -> list[dict]:
    return [
        _row(round_name="KO Eliminierung", round_number=7, game=0, player="Dan", score=200, handicap=10),
        _row(round_name="KO Eliminierung", round_number=7, game=0, player="Eva", score=200, handicap=11),
        _row(round_name="KO Eliminierung", round_number=7, game=0, player="Finn", score=181, handicap=24),
        _row(round_name="KO Eliminierung", round_number=7, game=1, player="Dan", score=258, handicap=10),
        _row(round_name="KO Eliminierung", round_number=7, game=1, player="Eva", score=188, handicap=11),
        _row(round_name="KO Stepladder", round_number=8, game=2, player="Cara", score=213, handicap=4),
        _row(round_name="KO Stepladder", round_number=8, game=2, player="Dan", score=225, handicap=10),
        _row(round_name="KO Stepladder", round_number=8, game=3, player="Ben", score=215, handicap=4),
        _row(round_name="KO Stepladder", round_number=8, game=3, player="Dan", score=222, handicap=10),
        _row(round_name="KO-Finale", round_number=9, game=4, player="Anna", score=201, handicap=5),
        _row(round_name="KO-Finale", round_number=9, game=4, player="Dan", score=190, handicap=10),
        _row(round_name="KO-Finale", round_number=9, game=5, player="Anna", score=235, handicap=5),
        _row(round_name="KO-Finale", round_number=9, game=5, player="Dan", score=160, handicap=10),
    ]


def test_clubmeisterschaft_config() -> None:
    cfg = config_entry(SEASON, CLUB)
    assert cfg.get("ko_bracket_format") == "seeded_elim_stepladder"
    assert cfg.get("ko_decision_basis") == "handicap"


def test_elim_stepladder_bracket_payload() -> None:
    bracket = build_ko_bracket(SEASON, CLUB, _finale_games())
    assert bracket["ko_bracket_format"] == "seeded_elim_stepladder"
    assert bracket["ko_decision_basis"] == "handicap"

    by_key = {m["key"]: m for m in bracket["matches"]}
    assert {"ELIM", "SL1", "SL2", "F"} <= by_key.keys()

    elim = by_key["ELIM"]
    rounds = {r["key"]: r for r in elim["rounds"]}
    field1 = {p["name"]: p for p in rounds["ELIM1"]["field"]}
    assert field1["Finn"]["eliminated"] is True
    assert field1["Finn"]["place"] == 6
    assert field1["Finn"]["games"] == [205]
    field2 = {p["name"]: p for p in rounds["ELIM2"]["field"]}
    assert field2["Eva"]["eliminated"] is True
    assert field2["Eva"]["place"] == 5
    assert field2["Dan"]["games"] == [268]
    assert rounds["ELIM2"]["advancer"] == "Dan"

    assert by_key["SL1"]["pin_games"] in ([[217, 235]], [[235, 217]])
    sl1_winner = by_key["SL1"]["side_a"]["name"] if by_key["SL1"]["winner"] == "a" else by_key["SL1"]["side_b"]["name"]
    assert sl1_winner == "Dan"
    sl1_loser = by_key["SL1"]["side_b"] if by_key["SL1"]["winner"] == "a" else by_key["SL1"]["side_a"]
    assert sl1_loser["name"] == "Cara"
    assert sl1_loser["place"] == 4

    sl2_winner = by_key["SL2"]["side_a"]["name"] if by_key["SL2"]["winner"] == "a" else by_key["SL2"]["side_b"]["name"]
    assert sl2_winner == "Dan"
    sl2_loser = by_key["SL2"]["side_b"] if by_key["SL2"]["winner"] == "a" else by_key["SL2"]["side_a"]
    assert sl2_loser["place"] == 3

    fin_winner = by_key["F"]["side_a"]["name"] if by_key["F"]["winner"] == "a" else by_key["F"]["side_b"]["name"]
    assert fin_winner == "Anna"
    fin_loser = by_key["F"]["side_b"] if by_key["F"]["winner"] == "a" else by_key["F"]["side_a"]
    assert fin_loser["name"] == "Dan"
    assert fin_loser["place"] == 2

    places = {p["place"]: p["player"] for p in bracket["placements"]}
    assert places == {1: "Anna", 2: "Dan", 3: "Ben", 4: "Cara", 5: "Eva", 6: "Finn"}


def test_handicap_can_flip_elim_vs_scratch() -> None:
    games = [
        _row(round_name="KO Eliminierung", round_number=7, game=0, player="Dan", score=200, handicap=10),
        _row(round_name="KO Eliminierung", round_number=7, game=0, player="Eva", score=199, handicap=0),
        _row(round_name="KO Eliminierung", round_number=7, game=0, player="Finn", score=198, handicap=24),
        _row(round_name="KO Eliminierung", round_number=7, game=1, player="Dan", score=200, handicap=10),
        _row(round_name="KO Eliminierung", round_number=7, game=1, player="Finn", score=220, handicap=24),
        _row(round_name="KO Stepladder", round_number=8, game=2, player="Cara", score=200, handicap=4),
        _row(round_name="KO Stepladder", round_number=8, game=2, player="Finn", score=210, handicap=24),
        _row(round_name="KO Stepladder", round_number=8, game=3, player="Ben", score=200, handicap=4),
        _row(round_name="KO Stepladder", round_number=8, game=3, player="Finn", score=210, handicap=24),
        _row(round_name="KO-Finale", round_number=9, game=4, player="Anna", score=220, handicap=5),
        _row(round_name="KO-Finale", round_number=9, game=4, player="Finn", score=180, handicap=24),
        _row(round_name="KO-Finale", round_number=9, game=5, player="Anna", score=220, handicap=5),
        _row(round_name="KO-Finale", round_number=9, game=5, player="Finn", score=180, handicap=24),
    ]
    bracket = build_ko_bracket(SEASON, CLUB, games)
    elim = next(m for m in bracket["matches"] if m["key"] == "ELIM")
    elim1 = next(r for r in elim["rounds"] if r["key"] == "ELIM1")
    field = {p["name"]: p for p in elim1["field"]}
    assert field["Eva"]["place"] == 6
    elim2 = next(r for r in elim["rounds"] if r["key"] == "ELIM2")
    assert elim2["advancer"] == "Finn"


def test_format_info_stepladder_label() -> None:
    info = format_ko_fields(SEASON, CLUB, _finale_games())
    assert info["ko_bracket_format"] == "seeded_elim_stepladder"
    assert info["ko_decision_basis"] == "handicap"
    assert "Handicap" in info["ko_finale_series_label_de"]
    assert info["config"].get("ko_decision_basis") == "handicap"


def test_apply_ko_ranks_stepladder_orders_places_first() -> None:
    bracket = {
        "ko_bracket_format": "seeded_elim_stepladder",
        "placements": [
            {"place": 1, "player": "Anna"},
            {"place": 2, "player": "Dan"},
            {"place": 3, "player": "Ben"},
            {"place": 4, "player": "Cara"},
            {"place": 5, "player": "Eva"},
            {"place": 6, "player": "Finn"},
        ],
        "matches": [{"key": "F"}],
    }
    rows = [
        {"rank": 7, "player": "Gus", "avg_net": 200.0, "avg_scratch": 220.0},
        {"rank": 2, "player": "Dan", "avg_net": 210.0, "avg_scratch": 200.0},
        {"rank": 6, "player": "Finn", "avg_net": 205.0, "avg_scratch": 190.0},
        {"rank": 1, "player": "Anna", "avg_net": 200.0, "avg_scratch": 195.0},
        {"rank": 3, "player": "Ben", "avg_net": 199.0, "avg_scratch": 192.0},
        {"rank": 8, "player": "Hal", "avg_net": 212.0, "avg_scratch": 180.0},
        {"rank": 4, "player": "Cara", "avg_net": 190.0, "avg_scratch": 185.0},
        {"rank": 5, "player": "Eva", "avg_net": 180.0, "avg_scratch": 175.0},
    ]
    out = apply_ko_ranks(rows, bracket)
    assert [r["player"] for r in out][:6] == ["Anna", "Dan", "Ben", "Cara", "Eva", "Finn"]
    assert [r["rank"] for r in out][:6] == [1, 2, 3, 4, 5, 6]
    assert [r["player"] for r in out][6:] == ["Hal", "Gus"]
    assert [r["rank"] for r in out][6:] == [7, 8]


def test_tree_bo3_and_group_name_config() -> None:
    cfg = config_entry(SEASON, "Bayerische Meisterschaft Einzel")
    assert cfg.get("ko_finale_series") == "bo3_pins"
    games = [
        _row(round_name="KO Viertelfinale", round_number=3, game=1, player="A", score=200, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=1, player="B", score=150, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=2, player="A", score=210, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=2, player="B", score=140, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=3, player="C", score=220, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=3, player="D", score=100, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=4, player="C", score=200, club="X"),
        _row(round_name="KO Viertelfinale", round_number=3, game=4, player="D", score=110, club="X"),
        _row(round_name="KO Halbfinale", round_number=4, game=5, player="A", score=180, club="X"),
        _row(round_name="KO Halbfinale", round_number=4, game=5, player="C", score=170, club="X"),
        _row(round_name="KO Halbfinale", round_number=4, game=6, player="A", score=190, club="X"),
        _row(round_name="KO Halbfinale", round_number=4, game=6, player="C", score=160, club="X"),
        _row(round_name="KO-Finale", round_number=5, game=7, player="A", score=200, club="X"),
        _row(round_name="KO-Finale", round_number=5, game=7, player="E", score=150, club="X"),
        _row(round_name="KO-Finale", round_number=5, game=8, player="A", score=210, club="X"),
        _row(round_name="KO-Finale", round_number=5, game=8, player="E", score=140, club="X"),
    ]
    bracket = build_ko_bracket(SEASON, BM, games)
    assert bracket["ko_finale_series"] == "bo3_pins"
    keys = [m["key"] for m in bracket["matches"]]
    assert "F" in keys
    assert "SF2" in keys  # inferred walkover for E
    places = {p["place"]: p["player"] for p in bracket["placements"]}
    assert places[1] == "A"
    assert places[2] == "E"
