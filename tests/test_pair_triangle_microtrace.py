from random import Random

from uboot.candidate_graph import candidate_objects
from uboot.checkpoint import dynamics_state_hash
from uboot.edge_response import EdgeResponseEngine, distinct_random_network, policy_profile
from uboot.microtrace import AtomicTouch, MicroContinuationTracker, replay_with_microtrace


PAIR = (1, 2)
TRIANGLE = (3, 4, 5)


def _tracker() -> MicroContinuationTracker:
    return MicroContinuationTracker(PAIR, TRIANGLE)


def _observe(
    tracker: MicroContinuationTracker,
    index: int,
    *,
    pairs: tuple[tuple[int, int], ...] = (PAIR,),
    triangles: tuple[tuple[int, int, int], ...] = (TRIANGLE,),
    touch: AtomicTouch | None = None,
):
    return tracker.observe(
        pairs,
        triangles,
        step_before=index - 1,
        step_after=index,
        touch=touch or AtomicTouch(),
    )


def test_atomically_static_summary() -> None:
    tracker = _tracker()
    for index in range(1, 4):
        _observe(tracker, index)

    summary = tracker.summary()
    assert summary["classification"] == "atomically_static"
    assert summary["ever_moved"] is False
    assert summary["ever_broken"] is False
    assert summary["restored"] is False


def test_pair_transient_move_and_return() -> None:
    tracker = _tracker()
    moved = _observe(tracker, 1, pairs=((2, 6),))
    restored = _observe(tracker, 2, pairs=(PAIR,))

    assert moved.micro_state == "pair_moved_triangle_static"
    assert restored.pair_restored_to_initial
    assert tracker.summary()["transient_pair_move"] is True


def test_triangle_transient_move_and_return() -> None:
    tracker = _tracker()
    _observe(tracker, 1, triangles=((4, 5, 6),))
    restored = _observe(tracker, 2, triangles=(TRIANGLE,))

    assert restored.triangle_restored_to_initial
    assert tracker.summary()["transient_triangle_move"] is True


def test_pair_break_and_restore_is_not_migration() -> None:
    tracker = _tracker()
    broken = _observe(tracker, 1, pairs=())
    restored = _observe(tracker, 2, pairs=(PAIR,))

    summary = tracker.summary()
    assert broken.micro_state == "pair_broken_triangle_valid"
    assert restored.pair_restored_to_initial
    assert summary["pair_break_and_restore"] is True
    assert summary["transient_pair_move"] is False


def test_one_sided_pair_move_remains_jointly_valid() -> None:
    tracker = _tracker()
    event = _observe(tracker, 1, pairs=((2, 6),))

    assert event.micro_state == "pair_moved_triangle_static"
    assert event.current_pair_after == (2, 6)
    assert event.current_triangle_after == TRIANGLE


def test_double_static_is_exact_static() -> None:
    event = _observe(_tracker(), 1)

    assert event.micro_state == "exact_static"
    assert not event.pair_changed_this_update
    assert not event.triangle_changed_this_update


def test_ambiguity_is_recorded_and_does_not_stop_recovery() -> None:
    tracker = _tracker()
    ambiguous = _observe(tracker, 1, pairs=(PAIR, (2, 6)))
    restored = _observe(tracker, 2, pairs=(PAIR,))

    assert ambiguous.micro_state == "pair_ambiguous"
    assert ambiguous.current_pair_after == PAIR
    assert restored.micro_state == "exact_static"
    assert tracker.summary()["ever_ambiguous"] is True


def _engine(seed: int = 7) -> EdgeResponseEngine:
    rng = Random(seed)
    engine = EdgeResponseEngine(
        distinct_random_network(20, rng),
        policy_profile("M2"),
        rng,
        mode="M1",
        candidate_rule="endogenous_in_out2",
    )
    engine.run(1000)
    return engine


def _objects(engine: EdgeResponseEngine):
    objects = candidate_objects(engine.candidate_graph(), include_singletons=False)
    pairs = [item.members for item in objects if len(item.members) == 2]
    triangles = [item.members for item in objects if len(item.members) == 3]
    return next(
        (pair, triangle)
        for pair in pairs
        for triangle in triangles
        if not set(pair) & set(triangle)
    )


def test_microtrace_final_hash_and_counters_match_direct_run() -> None:
    direct, observed = _engine(), _engine()
    pair, triangle = _objects(observed)
    end_step = observed.tick + 10

    for _ in range(10):
        direct.step_tick()
    replay_with_microtrace(
        observed,
        initial_pair=pair,
        initial_triangle=triangle,
        start_snapshot_id=0,
        end_snapshot_id=1,
        end_step=end_step,
    )

    assert dynamics_state_hash(observed) == dynamics_state_hash(direct)
    assert observed.counters == direct.counters
    assert observed.active_attempts == direct.active_attempts


def test_microtrace_preserves_rng_state() -> None:
    direct, observed = _engine(), _engine()
    pair, triangle = _objects(observed)

    direct.step_tick()
    replay_with_microtrace(
        observed,
        initial_pair=pair,
        initial_triangle=triangle,
        start_snapshot_id=0,
        end_snapshot_id=1,
        end_step=observed.tick + 1,
    )

    assert observed.rng.getstate() == direct.rng.getstate()


def test_zero_observation_side_effects() -> None:
    engine = _engine()
    pair, triangle = _objects(engine)
    targets_before = tuple(tuple(row) for row in engine.targets)
    rng_before = engine.rng.getstate()
    tracker = MicroContinuationTracker(pair, triangle)
    objects = candidate_objects(engine.candidate_graph(), include_singletons=False)

    tracker.observe(
        tuple(item.members for item in objects if len(item.members) == 2),
        tuple(item.members for item in objects if len(item.members) == 3),
        step_before=engine.tick,
        step_after=engine.tick,
        touch=AtomicTouch(),
    )

    assert tuple(tuple(row) for row in engine.targets) == targets_before
    assert engine.rng.getstate() == rng_before
