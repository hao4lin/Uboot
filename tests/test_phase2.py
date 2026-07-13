from uboot.phase2 import classify_transition, detect_open_reclose


def _state(snapshot: int, objects: dict[frozenset[int], tuple[str, str]], *, cycles=(), raw=()):
    return {
        "snapshot_id": snapshot,
        "members": {members: {"internal_candidate_edges": values[0], "internal_raw_mutual_edges": values[1]} for members, values in objects.items()},
        "candidate_cycles": frozenset(cycles), "raw_mutual": frozenset(raw),
    }


def test_object_internal_rewire_detection() -> None:
    members = frozenset({1, 2, 3})
    rows = classify_transition(_state(0, {members: ("1>2", "1>2")}), _state(1, {members: ("2>1", "1>2")}))
    assert rows[0]["state_change"] == "internal_rewire"


def test_single_member_replacement_requires_support() -> None:
    before = _state(0, {frozenset({1, 2, 3}): ("", "")}, cycles=((1, 2),))
    after = _state(1, {frozenset({1, 2, 4}): ("", "")}, cycles=((1, 2),))
    assert classify_transition(before, after)[0]["state_change"] == "reclosed_member_replacement"


def test_independent_birth_is_not_forced_to_match_death() -> None:
    before = _state(0, {frozenset({1, 2}): ("", "")})
    after = _state(1, {frozenset({7, 8}): ("", "")})
    labels = [row["state_change"] for row in classify_transition(before, after)]
    assert labels == ["death", "independent_birth"]


def test_temporary_open_reclose_detection() -> None:
    members = frozenset({1, 2})
    snapshots = [
        {"snapshot_id": 0, "members": {members: {}}},
        {"snapshot_id": 1, "members": {}},
        {"snapshot_id": 2, "members": {members: {}}},
    ]
    events = detect_open_reclose(snapshots, max_gap_snapshots=2)
    assert events[0]["state_change"] == "reclosed_same_members"
