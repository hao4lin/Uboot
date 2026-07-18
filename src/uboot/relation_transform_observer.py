"""Order-neutral relation slices and same/different transformation graphs."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
import json
from random import Random
from typing import Any, Callable, Iterable

from uboot.kernel import RawNetwork


BODY_SAME_NEIGHBOR_DIFFERENT = "BODY_SAME_NEIGHBOR_DIFFERENT"
NEIGHBOR_SAME_BODY_DIFFERENT = "NEIGHBOR_SAME_BODY_DIFFERENT"
MEMBERS_SAME_RELATION_DIFFERENT = "MEMBERS_SAME_RELATION_DIFFERENT"
RELATION_SAME_MEMBERS_DIFFERENT = "RELATION_SAME_MEMBERS_DIFFERENT"
ONE_MEMBER_OVERLAP_ROLE_CHANGED = "ONE_MEMBER_OVERLAP_ROLE_CHANGED"
CANONICAL_SAME_RAW_DISJOINT = "CANONICAL_SAME_RAW_DISJOINT"


@dataclass(frozen=True, slots=True)
class RelationSliceIdentity:
    relation_kind: str
    slot_semantics: tuple[int, ...]
    left_raw_id: int
    right_raw_id: int

    @property
    def raw_members(self) -> frozenset[int]:
        return frozenset((self.left_raw_id, self.right_raw_id))

    @property
    def canonical_signature(self) -> str:
        canonical_slots = (
            self.slot_semantics
            if self.relation_kind == "directed"
            else tuple(sorted(self.slot_semantics))
        )
        slots = "|".join(map(str, canonical_slots))
        roles = "source,target" if self.relation_kind == "directed" else "peer,peer"
        return f"{self.relation_kind};slots={slots};roles={roles}"

    def role_of(self, raw_id: int) -> str:
        if self.relation_kind != "directed":
            if raw_id in self.raw_members:
                return "peer"
            raise KeyError(raw_id)
        if raw_id == self.left_raw_id:
            return "body"
        if raw_id == self.right_raw_id:
            return "neighbor"
        raise KeyError(raw_id)


@dataclass(slots=True)
class SliceRecord:
    slice_id: str
    identity: RelationSliceIdentity
    anchor_ids: set[int] = field(default_factory=set)
    observation_locators: set[str] = field(default_factory=set)

    @property
    def observation_count(self) -> int:
        return len(self.observation_locators)


@dataclass(frozen=True, slots=True)
class SliceTransformation:
    slice_a_id: str
    slice_b_id: str
    transformation_type: str
    shared_raw_ids: tuple[int, ...]
    changed_raw_ids: tuple[int, ...]
    same_relation_kind: bool
    same_slot_semantics: bool
    role_mapping: tuple[tuple[int, str, str], ...]


class RelationTransformationObserver:
    """Collect relation content; observation locators never define graph semantics."""

    def __init__(
        self,
        size: int,
        observer_seed: int,
        tracked_anchor_count: int,
        max_path_length: int = 4,
    ) -> None:
        if not 1 <= tracked_anchor_count <= size:
            raise ValueError("tracked anchor count must be between one and N")
        if not 2 <= max_path_length <= 4:
            raise ValueError("maximum transformation path length must be 2--4")
        self.observer_seed = observer_seed
        anchor_permutation = Random(observer_seed).sample(range(size), size)
        self.anchor_ids = tuple(sorted(anchor_permutation[:tracked_anchor_count]))
        self.anchor_set = set(self.anchor_ids)
        self.max_path_length = max_path_length
        self.records: dict[str, SliceRecord] = {}
        self._identity_to_id: dict[RelationSliceIdentity, str] = {}

    def observe(self, network: RawNetwork, *, atom_step: int, snapshot: int) -> None:
        locator = _locator(atom_step, snapshot)
        pairs: set[tuple[int, int]] = set()
        for source, row in enumerate(network.targets):
            for target in row:
                if source in self.anchor_set or target in self.anchor_set:
                    pairs.add(tuple(sorted((source, target))))
        for left, right in sorted(pairs):
            anchors = {member for member in (left, right) if member in self.anchor_set}
            left_slots = tuple(
                slot
                for slot, target in enumerate(network.targets[left])
                if target == right
            )
            right_slots = tuple(
                slot
                for slot, target in enumerate(network.targets[right])
                if target == left
            )
            for slot in left_slots:
                self._record(
                    RelationSliceIdentity("directed", (slot,), left, right),
                    anchors,
                    locator,
                )
            for slot in right_slots:
                self._record(
                    RelationSliceIdentity("directed", (slot,), right, left),
                    anchors,
                    locator,
                )
            for left_slot in left_slots:
                for right_slot in right_slots:
                    kind = (
                        "reciprocal_same_slot"
                        if left_slot == right_slot
                        else "reciprocal_cross_slot"
                    )
                    self._record(
                        RelationSliceIdentity(
                            kind, (left_slot, right_slot), left, right
                        ),
                        anchors,
                        locator,
                    )

    def _record(
        self,
        identity: RelationSliceIdentity,
        anchors: set[int],
        locator: str,
    ) -> None:
        slice_id = self._identity_to_id.get(identity)
        if slice_id is None:
            slice_id = _slice_id(identity)
            existing = self.records.get(slice_id)
            if existing is not None and existing.identity != identity:
                raise RuntimeError("relation slice identity hash collision")
            self._identity_to_id[identity] = slice_id
            self.records[slice_id] = SliceRecord(slice_id, identity)
        record = self.records[slice_id]
        record.anchor_ids.update(anchors)
        record.observation_locators.add(locator)

    def transformations(self) -> tuple[SliceTransformation, ...]:
        by_member: dict[int, list[str]] = defaultdict(list)
        for slice_id, record in self.records.items():
            for member in record.identity.raw_members:
                by_member[member].append(slice_id)
        candidate_pairs: set[tuple[str, str]] = set()
        for slice_ids in by_member.values():
            ordered = sorted(slice_ids)
            for index, left in enumerate(ordered):
                candidate_pairs.update((left, right) for right in ordered[index + 1 :])
        rows = []
        for left_id, right_id in sorted(candidate_pairs):
            item = classify_transformation(
                self.records[left_id], self.records[right_id]
            )
            if item is not None:
                rows.append(item)
        return tuple(rows)

    def equivalence_groups(self) -> tuple[dict[str, Any], ...]:
        groups: dict[str, list[SliceRecord]] = defaultdict(list)
        for record in self.records.values():
            groups[record.identity.canonical_signature].append(record)
        rows = []
        for signature, records in sorted(groups.items()):
            slice_ids = sorted(record.slice_id for record in records)
            raw_sets = sorted(
                "|".join(map(str, sorted(record.identity.raw_members)))
                for record in records
            )
            disjoint_pairs = sum(
                records[left].identity.raw_members.isdisjoint(
                    records[right].identity.raw_members
                )
                for left in range(len(records))
                for right in range(left + 1, len(records))
            )
            rows.append(
                {
                    "equivalence_group_id": "E_"
                    + sha256(signature.encode()).hexdigest()[:12],
                    "canonical_signature": signature,
                    "slice_count": len(records),
                    "slice_ids": "|".join(slice_ids),
                    "raw_member_sets": ";".join(raw_sets),
                    "canonical_same_raw_disjoint_pair_count": disjoint_pairs,
                }
            )
        return tuple(rows)

    def paths(
        self,
        transformations: tuple[SliceTransformation, ...],
        progress: Callable[[int, int], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        edge_by_pair = {
            (row.slice_a_id, row.slice_b_id): row for row in transformations
        }
        adjacency: dict[str, set[str]] = defaultdict(set)
        for left, right in edge_by_pair:
            adjacency[left].add(right)
            adjacency[right].add(left)
        paths: set[tuple[str, ...]] = set()

        def extend(path: tuple[str, ...]) -> None:
            edge_length = len(path) - 1
            if edge_length >= 2:
                canonical = min(path, tuple(reversed(path)))
                paths.add(canonical)
            if edge_length == self.max_path_length:
                return
            for candidate in sorted(adjacency[path[-1]]):
                if candidate in path:
                    continue
                if any(candidate in adjacency[prior] for prior in path[:-1]):
                    continue
                extend((*path, candidate))

        starts = sorted(adjacency)
        for index, start in enumerate(starts, start=1):
            extend((start,))
            if progress is not None:
                progress(index, len(starts))

        rows = []
        for path in sorted(paths, key=lambda value: (len(value), value)):
            edges = [
                edge_by_pair.get(tuple(sorted((left, right))))
                for left, right in zip(path, path[1:])
            ]
            if any(edge is None for edge in edges):
                raise AssertionError("transformation path references a missing edge")
            typed_edges = [edge for edge in edges if edge is not None]
            members = sorted(
                set().union(
                    *(self.records[slice_id].identity.raw_members for slice_id in path)
                )
            )
            rows.append(
                {
                    "path_length": len(path) - 1,
                    "slice_ids": "|".join(path),
                    "transformation_types": "|".join(
                        edge.transformation_type for edge in typed_edges
                    ),
                    "shared_body_sequence": _shared_sequence(
                        typed_edges, BODY_SAME_NEIGHBOR_DIFFERENT
                    ),
                    "shared_neighbor_sequence": _shared_sequence(
                        typed_edges, NEIGHBOR_SAME_BODY_DIFFERENT
                    ),
                    "raw_member_union": "|".join(map(str, members)),
                    "canonical_pattern": "--".join(
                        edge.transformation_type for edge in typed_edges
                    ),
                }
            )
        return tuple(rows)

    def slice_rows(self) -> tuple[dict[str, Any], ...]:
        rows = []
        for record in sorted(self.records.values(), key=lambda value: value.slice_id):
            identity = record.identity
            rows.append(
                {
                    "slice_id": record.slice_id,
                    "anchor_id": "|".join(map(str, sorted(record.anchor_ids))),
                    "relation_kind": identity.relation_kind,
                    "slot_semantics": "|".join(map(str, identity.slot_semantics)),
                    "left_raw_id": identity.left_raw_id,
                    "right_raw_id": identity.right_raw_id,
                    "canonical_signature": identity.canonical_signature,
                    "observation_locator": json.dumps(
                        sorted(record.observation_locators), separators=(",", ":")
                    ),
                    "observation_count": record.observation_count,
                }
            )
        return tuple(rows)

    def anchor_summary(
        self,
        transformations: tuple[SliceTransformation, ...],
        paths: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        transformation_by_anchor: dict[int, list[SliceTransformation]] = defaultdict(
            list
        )
        for item in transformations:
            common_anchors = (
                self.records[item.slice_a_id].anchor_ids
                & self.records[item.slice_b_id].anchor_ids
            )
            for anchor in common_anchors:
                transformation_by_anchor[anchor].append(item)
        rows = []
        for anchor in self.anchor_ids:
            records = [
                record
                for record in self.records.values()
                if anchor in record.anchor_ids
            ]
            items = transformation_by_anchor[anchor]
            anchor_slice_ids = {record.slice_id for record in records}
            anchor_paths = [
                path
                for path in paths
                if set(path["slice_ids"].split("|")) & anchor_slice_ids
            ]
            counts = Counter(item.transformation_type for item in items)
            rows.append(
                {
                    "anchor_id": anchor,
                    "unique_relation_slices": len(records),
                    "unique_raw_neighbor_ids": len(
                        set().union(
                            *(record.identity.raw_members for record in records)
                        )
                        - {anchor}
                        if records
                        else set()
                    ),
                    "unique_canonical_relations": len(
                        {record.identity.canonical_signature for record in records}
                    ),
                    "body_same_neighbor_different_count": counts[
                        BODY_SAME_NEIGHBOR_DIFFERENT
                    ],
                    "neighbor_same_body_different_count": counts[
                        NEIGHBOR_SAME_BODY_DIFFERENT
                    ],
                    "members_same_relation_different_count": counts[
                        MEMBERS_SAME_RELATION_DIFFERENT
                    ],
                    "relation_same_members_different_count": (
                        _same_relation_different_members_count(records)
                    ),
                    "role_changed_overlap_count": counts[
                        ONE_MEMBER_OVERLAP_ROLE_CHANGED
                    ],
                    "max_body_preserving_family_size": _max_role_family(
                        records, "body"
                    ),
                    "max_neighbor_preserving_family_size": _max_role_family(
                        records, "neighbor"
                    ),
                    "transformation_path_count_length_2": sum(
                        path["path_length"] == 2 for path in anchor_paths
                    ),
                    "transformation_path_count_length_3": sum(
                        path["path_length"] == 3 for path in anchor_paths
                    ),
                    "transformation_path_count_length_4": sum(
                        path["path_length"] == 4 for path in anchor_paths
                    ),
                }
            )
        return tuple(rows)


def classify_transformation(
    left: SliceRecord, right: SliceRecord
) -> SliceTransformation | None:
    first, second = sorted((left, right), key=lambda record: record.slice_id)
    a = first.identity
    b = second.identity
    shared = tuple(sorted(a.raw_members & b.raw_members))
    if not shared:
        return None
    same_members = a.raw_members == b.raw_members
    same_kind = a.relation_kind == b.relation_kind
    same_slots = a.slot_semantics == b.slot_semantics
    roles = tuple((raw_id, a.role_of(raw_id), b.role_of(raw_id)) for raw_id in shared)
    if same_members:
        kind = MEMBERS_SAME_RELATION_DIFFERENT
    elif len(shared) == 1:
        _, role_a, role_b = roles[0]
        if role_a != role_b:
            kind = ONE_MEMBER_OVERLAP_ROLE_CHANGED
        elif role_a in {"body", "peer"}:
            kind = BODY_SAME_NEIGHBOR_DIFFERENT
        elif role_a == "neighbor":
            kind = NEIGHBOR_SAME_BODY_DIFFERENT
        else:
            raise AssertionError(role_a)
    else:
        return None
    changed = tuple(sorted(a.raw_members ^ b.raw_members))
    return SliceTransformation(
        first.slice_id,
        second.slice_id,
        kind,
        shared,
        changed,
        same_kind,
        same_slots,
        roles,
    )


def transformation_rows(
    transformations: Iterable[SliceTransformation],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "slice_a_id": item.slice_a_id,
            "slice_b_id": item.slice_b_id,
            "transformation_type": item.transformation_type,
            "shared_raw_ids": "|".join(map(str, item.shared_raw_ids)),
            "changed_raw_ids": "|".join(map(str, item.changed_raw_ids)),
            "same_relation_kind": item.same_relation_kind,
            "same_slot_semantics": item.same_slot_semantics,
            "role_mapping": json.dumps(item.role_mapping, separators=(",", ":")),
        }
        for item in transformations
    )


def _same_relation_different_members_count(records: list[SliceRecord]) -> int:
    return sum(
        left.identity.canonical_signature == right.identity.canonical_signature
        and left.identity.raw_members != right.identity.raw_members
        for index, left in enumerate(records)
        for right in records[index + 1 :]
    )


def _slice_id(identity: RelationSliceIdentity) -> str:
    payload = json.dumps(
        (
            identity.relation_kind,
            identity.slot_semantics,
            identity.left_raw_id,
            identity.right_raw_id,
        ),
        separators=(",", ":"),
    )
    return "S_" + sha256(payload.encode()).hexdigest()[:16]


def _locator(atom_step: int, snapshot: int) -> str:
    return json.dumps(
        {"atom_step": atom_step, "snapshot": snapshot},
        sort_keys=True,
        separators=(",", ":"),
    )


def _shared_sequence(
    edges: Iterable[SliceTransformation], transformation_type: str
) -> str:
    return ";".join(
        "|".join(map(str, edge.shared_raw_ids))
        for edge in edges
        if edge.transformation_type == transformation_type
    )


def _max_role_family(records: Iterable[SliceRecord], role: str) -> int:
    families: Counter[int] = Counter()
    for record in records:
        identity = record.identity
        for member in identity.raw_members:
            member_role = identity.role_of(member)
            if member_role == role or (member_role == "peer" and role == "body"):
                families[member] += 1
    return max(families.values(), default=0)
