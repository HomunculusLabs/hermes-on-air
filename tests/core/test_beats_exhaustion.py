"""Regression coverage for long-running quiet shows and seed-bank exhaustion."""

from onair.beats import SeedBank, _load_yaml
from onair.onboarding import STARTER_SEED_BANK
from onair.wordfilter import line_tiers
from onair.stage import StageConfig, StageController, render_turn_prompt


def bank_data(**settings):
    return {
        **settings,
        "segments": [
            {"name": "opening", "beats": [
                {"id": "open-one", "text": "Welcome everyone.", "provenance": "notes"},
                {"id": "open-two", "text": "Ask chat a question.", "provenance": "notes"},
            ]},
            {"name": "wrap", "beats": [
                {"id": "wrap-one", "text": "Recap the show.", "provenance": "notes"},
                {"id": "wrap-two", "text": "Thank the viewers.", "provenance": "notes"},
            ]},
        ],
    }


def test_quiet_show_keeps_offering_beats(clock, guard):
    c = StageController(StageConfig(dead_air_seconds=30, turn_gap_s=0),
                        clock=clock, guard=guard, seed_bank=SeedBank.from_data(bank_data()))
    c.set_mode("approval")
    offered = []
    for _ in range(360):  # three hours, at one offer per 30 seconds
        clock.advance(30)
        turn = c.tick()
        assert turn is not None
        offered.append(turn.beat.beat_id)
        c.turn_done(turn.turn_id)
    assert all(a != b for a, b in zip(offered, offered[1:]))


def test_off_air_rearm_resets_rundown(clock, guard):
    c = StageController(StageConfig(dead_air_seconds=30, turn_gap_s=0),
                        clock=clock, guard=guard, seed_bank=SeedBank.from_data(bank_data()))
    c.set_mode("approval")
    for _ in range(4):
        clock.advance(30)
        turn = c.tick()
        assert turn is not None
        c.turn_done(turn.turn_id)
    assert c.segment == "wrap"
    c.set_mode("off_air")
    snap = c.set_mode("approval")
    assert snap["segment"] == "opening"
    assert snap["beats_left"] == 4
    assert snap["rundown"] == "fresh"


def test_keep_rundown_on_rearm_and_restart(tmp_path, clock, guard):
    data = bank_data(reset_on_rearm=False, min_repeat_gap=2)
    def controller():
        return StageController(StageConfig(dead_air_seconds=30, turn_gap_s=0), data_dir=tmp_path,
                               clock=clock, guard=guard, seed_bank=SeedBank.from_data(data))
    c = controller()
    c.set_mode("approval")
    for _ in range(4):
        clock.advance(30)
        turn = c.tick()
        assert turn is not None
        c.turn_done(turn.turn_id)
    clock.advance(30)
    turn = c.tick()
    assert turn is not None
    c.turn_done(turn.turn_id)
    recent = c.bank.recent()
    c.set_mode("off_air")
    assert c.set_mode("approval")["beats_left"] == 0
    restarted = controller()
    restarted.set_mode("approval")
    clock.advance(30)
    turn = restarted.tick()
    assert turn is not None and turn.beat is not None
    assert turn.beat.beat_id not in recent


def test_snapshot_reports_exhaustion_and_warning(clock, guard):
    c = StageController(StageConfig(dead_air_seconds=30, turn_gap_s=0), clock=clock, guard=guard,
                        seed_bank=SeedBank.from_data(bank_data(exhaustion_policy="stop")))
    c.set_mode("approval")
    assert c.snapshot()["rundown"] == "fresh"
    for _ in range(4):
        clock.advance(30)
        turn = c.tick()
        assert turn is not None
        c.turn_done(turn.turn_id)
    snapshot = c.snapshot()
    assert snapshot["rundown"] == "exhausted"
    assert "No more prepared material" in snapshot["rundown_warning"]


def test_first_pass_order_and_loop_gap():
    bank = SeedBank.from_data(bank_data(min_repeat_gap=2))
    used = set()
    assert bank.rundown(used) == "fresh"
    segment = "opening"
    offered = []
    for _ in range(30):
        beat = bank.next_in_show(segment, used)
        assert beat is not None
        if beat.beat_id in used:
            assert "already covered earlier" in beat.text
            assert "fresh angle" in beat.text
        offered.append(beat.beat_id)
        used.add(beat.beat_id)
        segment = beat.segment
    assert offered[:4] == ["open-one", "open-two", "wrap-one", "wrap-two"]
    assert all(offered[i] not in offered[max(0, i - 2):i] for i in range(len(offered)))
    assert bank.rundown(used) == "looping"
    bank.reset()
    assert bank.rundown(set()) == "fresh"


def test_loop_prompt_reaches_stage(clock, guard):
    c = StageController(StageConfig(dead_air_seconds=30, turn_gap_s=0),
                        clock=clock, guard=guard, seed_bank=SeedBank.from_data(bank_data()))
    c.set_mode("approval")
    for _ in range(5):
        clock.advance(30)
        turn = c.tick()
        assert turn is not None
        prompt = render_turn_prompt(turn)
        c.turn_done(turn.turn_id)
    assert "already covered earlier" in prompt and "fresh angle" in prompt


def test_stop_exhausts_and_warns():
    bank = SeedBank.from_data(bank_data(exhaustion_policy="stop"))
    used = set()
    for _ in range(4):
        beat = bank.next_in_show(None, used)
        assert beat is not None
        used.add(beat.beat_id)
    assert bank.next_in_show("wrap", used) is None
    assert bank.rundown(used) == "exhausted"
    assert "No more prepared material" in bank.warning(used)


def test_wrap_only_and_non_evergreen():
    data = bank_data(exhaustion_policy="wrap_only")
    data["segments"][0]["beats"][0]["evergreen"] = False
    bank = SeedBank.from_data(data)
    used = set()
    offered = []
    for _ in range(12):
        beat = bank.next_in_show(None, used)
        assert beat is not None
        offered.append(beat.beat_id)
        used.add(beat.beat_id)
    assert set(offered[4:]) == {"wrap-one", "wrap-two"}
    assert all(a != b for a, b in zip(offered, offered[1:]))


def test_impossible_gap_is_honestly_exhausted():
    bank = SeedBank.from_data(bank_data(exhaustion_policy="wrap_only", min_repeat_gap=2))
    used = set()
    for _ in range(4):
        beat = bank.next_in_show(None, used)
        assert beat is not None
        used.add(beat.beat_id)
    assert bank.next_in_show(None, used) is None
    assert bank.rundown(used) == "exhausted"
    assert bank.warning(used)


def test_seed_bank_errors_do_not_leak_path_or_content(tmp_path, caplog):
    missing = tmp_path / 'private-folder' / 'secret-bank.yaml'
    bank = SeedBank(missing)
    state = StageController(seed_bank=bank).snapshot()
    assert state['seed_bank_error'] == 'Seed bank read error.'
    assert str(missing) not in str(state)
    path = tmp_path / 'bank.yaml'
    path.write_text('segments:\n  - name: [PRIVATE_BANK_LINE\n')
    bank = SeedBank(path)
    state = StageController(seed_bank=bank).snapshot()
    assert 'parse error' in state['seed_bank_error'] and 'line ' in state['seed_bank_error']
    assert 'PRIVATE_BANK_LINE' not in str(state) and str(path) not in str(state)
    invalid = SeedBank.from_data({'segments': [{'name': 'PRIVATE_NAME', 'beats': 'bad'}]})
    assert invalid.error == 'Seed bank validation error.'
    assert 'PRIVATE_NAME' not in str(invalid.error)
    assert 'PRIVATE_BANK_LINE' not in caplog.text and str(missing) not in caplog.text


def test_starter_bank_is_valid_and_substantial():
    bank = SeedBank.from_data(_load_yaml(STARTER_SEED_BANK))
    assert bank.error is None
    assert bank.segments() == ["opening", "middle", "wrap"]
    assert len(bank.beats()) >= 24
    assert all(b.provenance and len(b.text) <= 420 for b in bank.beats())
    assert all(not line_tiers(b.text) for b in bank.beats())


def test_repeat_history_can_be_restored():
    bank = SeedBank.from_data(bank_data(min_repeat_gap=2))
    used = set()
    for _ in range(4):
        beat = bank.next_in_show(None, used)
        assert beat is not None
        used.add(beat.beat_id)
    restarted = SeedBank.from_data(bank_data(min_repeat_gap=2))
    restarted.restore_recent(bank.recent())
    beat = restarted.next_in_show("wrap", used)
    assert beat is not None and beat.beat_id not in bank.recent()


def test_invalid_policy_settings_fail_closed():
    for settings in ({"exhaustion_policy": "end"}, {"min_repeat_gap": 0},
                     {"min_repeat_gap": True}, {"reset_on_rearm": "false"}):
        bank = SeedBank.from_data(bank_data(**settings))
        assert bank.error and not bank.beats()
