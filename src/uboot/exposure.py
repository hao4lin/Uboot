"""Fixed-point object indexing and exposure-relation state comparison."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from uboot.candidate_graph import candidate_objects
from uboot.joint_continuation import Members
from uboot.joint_diagnostics import RawSlot, candidate_support_raw_slots


@dataclass(frozen=True, slots=True)
class FixedPointObject:
    object_id: str
    object_type: str
    members: Members


class FixedPointIndex:
    """Stable analysis-only object IDs and a strict raw-member reverse index."""

    def __init__(self, objects: Iterable[FixedPointObject]) -> None:
        self.objects = tuple(objects)
        self.by_id = {item.object_id: item for item in self.objects}
        self.by_members = {item.members: item for item in self.objects}
        reverse: dict[int, list[str]] = {}
        for item in self.objects:
            for member in item.members:
                reverse.setdefault(member, []).append(item.object_id)
        overlaps = {member: ids for member, ids in reverse.items() if len(ids) > 1}
        if overlaps:
            raise ValueError(f"fixed-point objects overlap: {overlaps}")
        self.raw_to_object_ids = {
            member: tuple(ids) for member, ids in reverse.items()
        }

    @classmethod
    def from_candidate_graph(
        cls, graph: list[tuple[int, ...]]
    ) -> "FixedPointIndex":
        recognized = candidate_objects(graph, include_singletons=False)
        pairs = sorted(item.members for item in recognized if len(item.members) == 2)
        triangles = sorted(
            item.members for item in recognized if len(item.members) == 3
        )
        objects = [
            FixedPointObject(f"P{index:06d}", "pair", members)
            for index, members in enumerate(pairs, start=1)
        ]
        objects.extend(
            FixedPointObject(f"T{index:06d}", "triangle", members)
            for index, members in enumerate(triangles, start=1)
        )
        return cls(objects)


@dataclass(frozen=True, order=True, slots=True)
class ExposedSlot:
    object_id: str
    object_type: str
    source_member_id: int
    source_slot_id: int
    slot_semantics: int
    target_raw_id: int
    target_object_ids: tuple[str, ...]
    target_object_member_ids: tuple[int, ...]
    target_member_roles: tuple[str, ...]


@dataclass(frozen=True, order=True, slots=True)
class DirectObjectLink:
    source_object_id: str
    source_member_id: int
    source_slot_id: int
    slot_semantics: int
    target_raw_id: int
    target_object_id: str
    target_member_id: int


@dataclass(frozen=True, order=True, slots=True)
class ObjectPath2:
    source_object_id: str
    source_member_id: int
    source_slot_id: int
    middle_object_id: str
    middle_entry_member_id: int
    middle_exit_member_id: int
    middle_exit_slot_id: int
    target_object_id: str
    target_member_id: int
    is_return_loop: bool


@dataclass(frozen=True, slots=True)
class ObjectExposureState:
    internal_support_slots: tuple[tuple[str, tuple[RawSlot, ...]], ...]
    exposed_slots: tuple[ExposedSlot, ...]
    direct_links: tuple[DirectObjectLink, ...]
    two_hop_paths: tuple[ObjectPath2, ...]
    scope_object_ids: tuple[str, ...]
    level1_exposure_hash: str
    level2_direct_link_hash: str
    level3_two_hop_hash: str


@dataclass(frozen=True, slots=True)
class ExposureDelta:
    level1_micro_exposure_changed: bool
    level2_object_link_changed: bool
    level3_two_hop_changed: bool
    change_classes: tuple[str, ...]
    raw_target_change_count: int
    same_target_object_micro_rewire_count: int
    source_slot_reassignment_count: int
    direct_link_create_count: int
    direct_link_break_count: int
    direct_neighbor_change_count: int
    multiplicity_change_count: int
    two_hop_path_create_count: int
    two_hop_path_break_count: int
    gap_transfer_candidate: bool
    affected_object_ids: tuple[str, ...]
    affected_direct_links_before: tuple[DirectObjectLink, ...]
    affected_direct_links_after: tuple[DirectObjectLink, ...]
    affected_two_hop_paths_before: tuple[ObjectPath2, ...]
    affected_two_hop_paths_after: tuple[ObjectPath2, ...]


class ExposureStateBuilder:
    """Incrementally recompute exposure slots while retaining a full verifier."""

    def __init__(
        self,
        targets: list[list[int]],
        index: FixedPointIndex,
        root_object_ids: Iterable[str],
        *,
        object_radius: int = 2,
    ) -> None:
        if object_radius != 2:
            raise ValueError("the first exposure experiment supports radius 2")
        self.index = index
        self.root_object_ids = tuple(root_object_ids)
        if not self.root_object_ids or any(
            item not in index.by_id for item in self.root_object_ids
        ):
            raise ValueError("root object IDs must exist in the fixed-point index")
        self.object_slots = {
            item.object_id: _object_slot_state(targets, index, item)
            for item in index.objects
        }
        complete = self._assemble()
        self.scope_object_ids = _radius_two_scope(
            self.root_object_ids, complete.direct_links
        )

    def state(self) -> ObjectExposureState:
        return self._assemble(self.scope_object_ids)

    def update(
        self,
        targets: list[list[int]],
        affected_object_ids: Iterable[str],
    ) -> ObjectExposureState:
        for object_id in affected_object_ids:
            item = self.index.by_id[object_id]
            self.object_slots[object_id] = _object_slot_state(
                targets, self.index, item
            )
        return self.state()

    def full_recompute(self, targets: list[list[int]]) -> ObjectExposureState:
        slots = self.object_slots
        try:
            self.object_slots = {
                item.object_id: _object_slot_state(targets, self.index, item)
                for item in self.index.objects
            }
            return self.state()
        finally:
            self.object_slots = slots

    def _assemble(
        self, scope: Iterable[str] | None = None
    ) -> ObjectExposureState:
        supports = tuple(
            (object_id, self.object_slots[object_id][0])
            for object_id in sorted(self.object_slots)
        )
        exposed = tuple(
            sorted(
                slot
                for _, slots in self.object_slots.values()
                for slot in slots
            )
        )
        links = tuple(sorted(_direct_links(exposed)))
        paths = tuple(sorted(_two_hop_paths(links)))
        scope_ids = tuple(sorted(scope or self.index.by_id))
        scope_set = set(scope_ids)
        scoped_exposed = tuple(
            item for item in exposed if item.object_id in scope_set
        )
        scoped_links = tuple(
            item for item in links if item.source_object_id in scope_set
        )
        scoped_paths = tuple(
            item for item in paths if item.source_object_id in scope_set
        )
        level2 = tuple(
            sorted(
                Counter(
                    (item.source_object_id, item.target_object_id)
                    for item in scoped_links
                ).items()
            )
        )
        return ObjectExposureState(
            supports,
            exposed,
            links,
            paths,
            scope_ids,
            _stable_hash(scoped_exposed),
            _stable_hash(level2),
            _stable_hash(scoped_paths),
        )


def compare_exposure_states(
    before: ObjectExposureState, after: ObjectExposureState
) -> ExposureDelta:
    scope = set(before.scope_object_ids) | set(after.scope_object_ids)
    old_slots = {
        _slot_key(item): item
        for item in before.exposed_slots
        if item.object_id in scope
    }
    new_slots = {
        _slot_key(item): item
        for item in after.exposed_slots
        if item.object_id in scope
    }
    raw_changes = [
        (old_slots[key], new_slots[key])
        for key in old_slots.keys() & new_slots.keys()
        if old_slots[key].target_raw_id != new_slots[key].target_raw_id
    ]
    same_object = sum(
        old.target_object_ids == new.target_object_ids
        and bool(old.target_object_ids)
        for old, new in raw_changes
    )
    old_links = tuple(
        item for item in before.direct_links if item.source_object_id in scope
    )
    new_links = tuple(
        item for item in after.direct_links if item.source_object_id in scope
    )
    old_counts = Counter(
        (item.source_object_id, item.target_object_id) for item in old_links
    )
    new_counts = Counter(
        (item.source_object_id, item.target_object_id) for item in new_links
    )
    old_pairs, new_pairs = set(old_counts), set(new_counts)
    broken, created = old_pairs - new_pairs, new_pairs - old_pairs
    multiplicity = {
        key for key in old_pairs & new_pairs if old_counts[key] != new_counts[key]
    }
    neighbor_sources = {
        source
        for source in {key[0] for key in old_pairs | new_pairs}
        if {target for left, target in old_pairs if left == source}
        != {target for left, target in new_pairs if left == source}
    }
    reassigned = _source_slot_reassignments(old_links, new_links, old_counts, new_counts)
    old_paths = tuple(
        item for item in before.two_hop_paths if item.source_object_id in scope
    )
    new_paths = tuple(
        item for item in after.two_hop_paths if item.source_object_id in scope
    )
    removed_paths = set(old_paths) - set(new_paths)
    added_paths = set(new_paths) - set(old_paths)
    gap = any(old_target == new_source for _, old_target in broken for new_source, _ in created)
    level1 = before.level1_exposure_hash != after.level1_exposure_hash
    level2 = before.level2_direct_link_hash != after.level2_direct_link_hash
    level3 = before.level3_two_hop_hash != after.level3_two_hop_hash
    classes = []
    if not (level1 or level2 or level3):
        classes.append("exposure_static")
    if same_object:
        classes.append("raw_target_changed_same_object")
    if reassigned:
        classes.append("source_slot_reassigned_same_target_object")
    if neighbor_sources:
        classes.append("direct_object_neighbor_changed")
    if broken:
        classes.append("direct_link_broken")
    if created:
        classes.append("direct_link_created")
    if multiplicity:
        classes.append("multiplicity_changed")
    if level3:
        classes.append("two_hop_path_changed")
        classes.extend(_two_hop_change_classes(removed_paths, added_paths))
    if gap:
        classes.append("gap_transfer_candidate")
    affected = _affected_objects(
        old_slots,
        new_slots,
        old_links,
        new_links,
        old_paths,
        new_paths,
    )
    return ExposureDelta(
        level1,
        level2,
        level3,
        tuple(dict.fromkeys(classes)),
        len(raw_changes),
        same_object,
        reassigned,
        len(created),
        len(broken),
        len(neighbor_sources),
        len(multiplicity),
        len(added_paths),
        len(removed_paths),
        gap,
        tuple(sorted(affected)),
        tuple(item for item in old_links if _link_touches(item, affected)),
        tuple(item for item in new_links if _link_touches(item, affected)),
        tuple(item for item in old_paths if _path_touches(item, affected)),
        tuple(item for item in new_paths if _path_touches(item, affected)),
    )


def affected_objects_for_candidate_nodes(
    index: FixedPointIndex, nodes: Iterable[int]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                object_id
                for node in nodes
                for object_id in index.raw_to_object_ids.get(node, ())
            }
        )
    )


def _object_slot_state(
    targets: list[list[int]],
    index: FixedPointIndex,
    item: FixedPointObject,
) -> tuple[tuple[RawSlot, ...], tuple[ExposedSlot, ...]]:
    supports = tuple(sorted(candidate_support_raw_slots(targets, item.members)))
    support_set = set(supports)
    members = set(item.members)
    exposed = []
    for member in item.members:
        for slot, target in enumerate(targets[member]):
            if (member, slot, target) in support_set or target in members:
                continue
            target_ids = index.raw_to_object_ids.get(target, ())
            roles = tuple(
                _member_role(index.by_id[object_id], target)
                for object_id in target_ids
            )
            exposed.append(
                ExposedSlot(
                    item.object_id,
                    item.object_type,
                    member,
                    slot,
                    slot + 1,
                    target,
                    target_ids,
                    tuple(target for _ in target_ids),
                    roles,
                )
            )
    return supports, tuple(sorted(exposed))


def _direct_links(slots: Iterable[ExposedSlot]) -> list[DirectObjectLink]:
    return [
        DirectObjectLink(
            slot.object_id,
            slot.source_member_id,
            slot.source_slot_id,
            slot.slot_semantics,
            slot.target_raw_id,
            target_id,
            slot.target_raw_id,
        )
        for slot in slots
        for target_id in slot.target_object_ids
        if target_id != slot.object_id
    ]


def _two_hop_paths(links: Iterable[DirectObjectLink]) -> list[ObjectPath2]:
    links = tuple(links)
    by_source: dict[str, list[DirectObjectLink]] = {}
    for item in links:
        by_source.setdefault(item.source_object_id, []).append(item)
    paths = []
    for first in links:
        for second in by_source.get(first.target_object_id, ()):
            if first.source_object_id == first.target_object_id:
                continue
            if first.target_object_id == second.target_object_id:
                continue
            paths.append(
                ObjectPath2(
                    first.source_object_id,
                    first.source_member_id,
                    first.source_slot_id,
                    first.target_object_id,
                    first.target_member_id,
                    second.source_member_id,
                    second.source_slot_id,
                    second.target_object_id,
                    second.target_member_id,
                    first.source_object_id == second.target_object_id,
                )
            )
    return paths


def _radius_two_scope(
    roots: Iterable[str], links: Iterable[DirectObjectLink]
) -> tuple[str, ...]:
    adjacency: dict[str, set[str]] = {}
    for item in links:
        adjacency.setdefault(item.source_object_id, set()).add(item.target_object_id)
    scope, frontier = set(roots), set(roots)
    for _ in range(2):
        frontier = {
            target for source in frontier for target in adjacency.get(source, ())
        } - scope
        scope.update(frontier)
    return tuple(sorted(scope))


def _source_slot_reassignments(
    old_links: tuple[DirectObjectLink, ...],
    new_links: tuple[DirectObjectLink, ...],
    old_counts: Counter[tuple[str, str]],
    new_counts: Counter[tuple[str, str]],
) -> int:
    total = 0
    for key in set(old_counts) & set(new_counts):
        if old_counts[key] != new_counts[key]:
            continue
        old_impl = {
            (item.source_member_id, item.source_slot_id)
            for item in old_links
            if (item.source_object_id, item.target_object_id) == key
        }
        new_impl = {
            (item.source_member_id, item.source_slot_id)
            for item in new_links
            if (item.source_object_id, item.target_object_id) == key
        }
        total += old_impl != new_impl
    return total


def _two_hop_change_classes(
    removed: set[ObjectPath2], added: set[ObjectPath2]
) -> tuple[str, ...]:
    result = set()
    for old in removed:
        for new in added:
            if (
                old.source_object_id,
                old.middle_object_id,
                old.target_object_id,
            ) == (
                new.source_object_id,
                new.middle_object_id,
                new.target_object_id,
            ):
                if old.middle_entry_member_id != new.middle_entry_member_id:
                    result.add("middle_entry_changed")
                if old.middle_exit_member_id != new.middle_exit_member_id:
                    result.add("middle_exit_changed")
                if old.middle_exit_slot_id != new.middle_exit_slot_id:
                    result.add("middle_internal_route_changed")
            elif (
                old.source_object_id,
                old.middle_object_id,
            ) == (new.source_object_id, new.middle_object_id):
                result.add("target_object_changed")
    return tuple(sorted(result))


def _affected_objects(
    old_slots: dict[tuple[str, int, int], ExposedSlot],
    new_slots: dict[tuple[str, int, int], ExposedSlot],
    old_links: tuple[DirectObjectLink, ...],
    new_links: tuple[DirectObjectLink, ...],
    old_paths: tuple[ObjectPath2, ...],
    new_paths: tuple[ObjectPath2, ...],
) -> set[str]:
    result = set()
    for key in set(old_slots) | set(new_slots):
        if old_slots.get(key) != new_slots.get(key):
            result.add(key[0])
            for slot in (old_slots.get(key), new_slots.get(key)):
                if slot is not None:
                    result.update(slot.target_object_ids)
    for item in set(old_links) ^ set(new_links):
        result.update((item.source_object_id, item.target_object_id))
    for item in set(old_paths) ^ set(new_paths):
        result.update(
            (item.source_object_id, item.middle_object_id, item.target_object_id)
        )
    return result


def _member_role(item: FixedPointObject, member: int) -> str:
    return f"member_{item.members.index(member) + 1}"


def _slot_key(item: ExposedSlot) -> tuple[str, int, int]:
    return item.object_id, item.source_member_id, item.source_slot_id


def _link_touches(item: DirectObjectLink, object_ids: set[str]) -> bool:
    return item.source_object_id in object_ids or item.target_object_id in object_ids


def _path_touches(item: ObjectPath2, object_ids: set[str]) -> bool:
    return bool(
        {item.source_object_id, item.middle_object_id, item.target_object_id}
        & object_ids
    )


def _stable_hash(value: object) -> str:
    return sha256(repr(value).encode()).hexdigest()
