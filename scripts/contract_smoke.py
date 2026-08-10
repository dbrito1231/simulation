"""Deterministic smoke for Emergence Breakthroughs F3.2 (contracts & escrow).

Asserts coin conservation across offer/fulfill/default/expiry and open-contract
save/restore round-trip. Ollama-free.

Run: uv run python scripts/contract_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


def _coin_setup(engine, offerer, acceptor, offer_coin=10, pay_coin=5):
    offerer.setdefault("resources", {})["coin"] = offer_coin
    acceptor.setdefault("resources", {})["coin"] = 0
    engine.civilization.setdefault("stockpile", {})["coin"] = 0
    engine.civilization["contractEscrow"] = 0
    engine.civilization["contracts"] = []
    return engine._total_tracked_coin()


def _offer(engine, offerer, acceptor, **overrides):
    body = {
        "want": "wood",
        "qty": 2,
        "pay_coin": 5,
        "deadline_frames": 300,
    }
    body.update(overrides)
    return engine.apply_decision(offerer, {
        "action": "offer_contract",
        "target": acceptor["name"],
        "contract": body,
        "reasoning": "smoke",
    })


def test_flag_off_noop():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = False
    try:
        engine = make_engine(4)
        agent = engine.agents[0]
        before = _coin_setup(engine, agent, engine.agents[1])
        summary = engine.apply_decision(agent, {
            "action": "offer_contract",
            "target": "open",
            "contract": {"want": "wood", "qty": 1, "pay_coin": 1, "deadline_frames": 60},
        })
        assert_true("disabled" in summary.lower(), summary)
        assert_true(not engine.civilization.get("contracts"), "flag off should not create contracts")
        assert_true(engine._total_tracked_coin() == before, "flag off should not move coin")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK flag-off noop")


def test_offer_conserves_coin():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor)
        assert_true(engine.civilization.get("contractEscrow") == 5, "escrow not funded")
        assert_true(offerer["resources"]["coin"] == 5, "offerer not debited")
        assert_true(len(engine.civilization["contracts"]) == 1, "contract not created")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on offer")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK offer conservation")


def test_fulfill_conserves_coin():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor)
        ct = engine.civilization["contracts"][0]
        engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        acceptor["resources"]["wood"] = 2
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "fulfilled contract should clear")
        assert_true(engine.civilization["contractEscrow"] == 0, "escrow should empty")
        assert_true(acceptor["resources"]["coin"] == 5, "acceptor should receive escrow")
        assert_true(offerer["resources"]["wood"] == 2, "offerer should receive goods")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on fulfill")
        assert_true(engine.civilization["contractsFulfilled"] == 1, "fulfill counter")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK fulfill conservation")


def test_accept_rejected_after_deadline():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine.frameTick = ct["createdFrame"] + 11
        summary = engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        assert_true("past deadline" in summary.lower(), summary)
        assert_true(ct["status"] == "open", "expired contract must stay open")
        assert_true(ct.get("acceptor") is None, "acceptor must not bind after deadline")
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "expired open contract should clear")
        assert_true(offerer["resources"]["coin"] == 10, "offerer refunded on expiry")
        assert_true(offerer["relationships"].get(acceptor["name"]) != "rival",
                    "late accept path must not default or mark rival")
        assert_true(engine.civilization["contractDefaults"] == 0, "no default on open expiry")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK accept rejected after deadline")


def test_exact_deadline_frame_expired():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        deadline = ct["createdFrame"] + ct["deadline_frames"]
        engine.frameTick = deadline
        summary = engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        assert_true("past deadline" in summary.lower(), summary)
        assert_true(ct["status"] == "open", "accept on deadline frame must fail")
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "open contract expires on deadline frame")
        assert_true(offerer["resources"]["coin"] == 10, "offerer refunded on deadline frame")

        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        acceptor["resources"]["wood"] = 2
        engine.frameTick = ct["createdFrame"] + ct["deadline_frames"]
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "accepted contract defaults on deadline frame")
        assert_true(engine.civilization["contractsFulfilled"] == 0, "no fulfill on deadline frame")
        assert_true(offerer["relationships"].get(acceptor["name"]) == "rival",
                    "living default on deadline frame still marks rival")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on deadline default")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK exact-deadline frame expired")


def test_dead_offerer_default_no_rival():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        engine._agent_dies(offerer, cause="smoke")
        assert_true(offerer.get("deathFrame") is not None, "offerer should be dead")
        engine.frameTick = ct["createdFrame"] + 11
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "defaulted contract should clear")
        assert_true(offerer["relationships"].get(acceptor["name"]) != "rival",
                    "dead offerer must not get rival relationship mutation")
        assert_true(engine.civilization["contractDefaults"] == 1, "default counter")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on dead-offerer default")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK dead-offerer default skips rival")


def test_default_refunds_and_rival():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        engine.frameTick = ct["createdFrame"] + 11
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "defaulted contract should clear")
        assert_true(engine.civilization["contractEscrow"] == 0, "escrow refunded")
        assert_true(offerer["resources"]["coin"] == 10, "offerer refunded")
        assert_true(offerer["relationships"].get(acceptor["name"]) == "rival",
                    "default should mark rival")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on default")
        assert_true(engine.civilization["contractDefaults"] == 1, "default counter")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK default refund + rival")


def test_open_expiry_refunds():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine.frameTick = ct["createdFrame"] + 11
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "expired open contract should clear")
        assert_true(offerer["resources"]["coin"] == 10, "offerer refunded on expiry")
        assert_true(offerer["relationships"].get(acceptor["name"]) != "rival",
                    "unaccepted expiry should not mark rival")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on expiry")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK open expiry refund")


def _heir_coin_total(engine, deceased):
    return sum(
        int((a.get("resources") or {}).get("coin") or 0)
        for a in engine.agents
        if a is not deceased
    )


def test_dead_offerer_expiry_refunds_to_heirs():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine._agent_dies(offerer, cause="smoke")
        assert_true(offerer["resources"].get("coin", 0) == 0,
                    "inheritance should clear corpse coin")
        heir_coin_after_death = _heir_coin_total(engine, offerer)
        engine.frameTick = ct["createdFrame"] + 11
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "expired contract should clear")
        assert_true(engine.civilization["contractEscrow"] == 0, "escrow refunded")
        assert_true(offerer["resources"].get("coin", 0) == 0,
                    "refund must not credit corpse")
        assert_true(_heir_coin_total(engine, offerer) == heir_coin_after_death + 5,
                    "living heirs should receive escrow refund")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on dead-offerer expiry")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK dead-offerer expiry refund to heirs")


def test_dead_offerer_default_refunds_to_heirs():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        engine.apply_decision(acceptor, {
            "action": "accept_contract",
            "target": ct["id"],
            "reasoning": "smoke",
        })
        engine._agent_dies(offerer, cause="smoke")
        heir_coin_after_death = _heir_coin_total(engine, offerer)
        engine.frameTick = ct["createdFrame"] + 11
        engine._tick_contract_settlement()
        assert_true(not engine.civilization["contracts"], "defaulted contract should clear")
        assert_true(engine.civilization["contractEscrow"] == 0, "escrow refunded on default")
        assert_true(offerer["resources"].get("coin", 0) == 0,
                    "refund must not credit corpse")
        assert_true(_heir_coin_total(engine, offerer) == heir_coin_after_death + 5,
                    "living heirs should receive escrow refund on default")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on dead-offerer default")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK dead-offerer default refund to heirs")


def test_missing_offerer_refund_to_stockpile():
    old = se.CONTRACTS_ENABLED
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        before = _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor, deadline_frames=10)
        ct = engine.civilization["contracts"][0]
        ct["offerer"] = "MissingVillager"
        stock_before = engine.civilization["stockpile"].get("coin", 0)
        engine.frameTick = ct["createdFrame"] + 11
        engine._tick_contract_settlement()
        assert_true(engine.civilization["contractEscrow"] == 0, "escrow refunded")
        assert_true(engine.civilization["stockpile"].get("coin", 0) == stock_before + 5,
                    "missing offerer refund should credit stockpile")
        assert_true(engine._total_tracked_coin() == before, "coin not conserved on missing offerer")
    finally:
        se.CONTRACTS_ENABLED = old
    print("  OK missing-offerer refund to stockpile")


def test_restore_round_trip():
    import sim_engine as se_mod

    old = se.CONTRACTS_ENABLED
    old_db = se_mod.DB_PATH
    se.CONTRACTS_ENABLED = True
    try:
        engine = make_engine(4)
        offerer, acceptor = engine.agents[0], engine.agents[1]
        _coin_setup(engine, offerer, acceptor)
        _offer(engine, offerer, acceptor)
        ct_id = engine.civilization["contracts"][0]["id"]
        escrow = engine.civilization["contractEscrow"]
        tmpdir = tempfile.mkdtemp()
        se_mod.DB_PATH = str(Path(tmpdir) / "state_contract.db")
        engine.save_state(force=True)
        assert_true(engine.restore_state(), "restore should succeed")
        assert_true(engine.civilization.get("contractEscrow") == escrow, "escrow not restored")
        restored = engine._find_contract(ct_id)
        assert_true(restored and restored["status"] == "open", "contract not restored")
    finally:
        se_mod.DB_PATH = old_db
        se.CONTRACTS_ENABLED = old
    print("  OK restore round-trip")


def main():
    print("contract_smoke")
    test_flag_off_noop()
    test_offer_conserves_coin()
    test_fulfill_conserves_coin()
    test_accept_rejected_after_deadline()
    test_exact_deadline_frame_expired()
    test_default_refunds_and_rival()
    test_dead_offerer_default_no_rival()
    test_open_expiry_refunds()
    test_dead_offerer_expiry_refunds_to_heirs()
    test_dead_offerer_default_refunds_to_heirs()
    test_missing_offerer_refund_to_stockpile()
    test_restore_round_trip()
    print("ALL OK")


if __name__ == "__main__":
    main()
