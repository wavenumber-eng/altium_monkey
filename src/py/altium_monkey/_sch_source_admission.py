"""Immutable request-local admission for schematic producer inputs."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class _SourceAdmission:
    ignored_ids: frozenset[int] = frozenset()
    source_objects: tuple[object, ...] = ()
    parent_by_source_id: Mapping[int, object | None] | None = None
    children_by_owner_id: Mapping[int, tuple[object, ...]] | None = None
    unattached_ids: frozenset[int] = frozenset()

    @classmethod
    def prepare(cls, objects: Iterable[object]) -> _SourceAdmission:
        from ._sch_source_projection import _ignored_source_object_ids

        sources = tuple(objects)
        ignored_ids = _ignored_source_object_ids(
            sources, cycle_error="cyclic harness-layout ownership"
        )
        return cls(ignored_ids, sources)

    @classmethod
    def for_rendering(
        cls,
        objects: Iterable[object],
        *,
        parents: Mapping[int, object | None],
        document_root: object | None = None,
        unattached_ids: Collection[int] = (),
    ) -> _SourceAdmission:
        from ._sch_source_projection import (
            _ignored_source_object_ids,
            _parameter_source_exclusions,
            _parameter_projected_parents,
        )

        sources = tuple(objects)
        ignored = _ignored_source_object_ids(
            sources, cycle_error="cyclic harness-layout ownership", parents=parents
        )
        unattached = frozenset(unattached_ids) | _parameter_source_exclusions(
            sources, parents, ignored
        )
        projected = _parameter_projected_parents(sources, parents)
        if document_root is not None:
            projected = {
                source_id: None if parent is document_root else parent
                for source_id, parent in projected.items()
            }
        return cls.from_projection(
            sources,
            {id(source) for source in sources} - ignored,
            projected,
            unattached_ids=unattached,
        )

    @classmethod
    def from_projection(
        cls,
        objects: Iterable[object],
        eligible_ids: Collection[int],
        parents: Mapping[int, object | None],
        *,
        unattached_ids: frozenset[int] = frozenset(),
    ) -> _SourceAdmission:
        sources = tuple(objects)
        eligible = frozenset(eligible_ids)
        children: dict[int, list[object]] = {}
        for source in sources:
            parent = parents.get(id(source))
            if parent is not None:
                children.setdefault(id(parent), []).append(source)
        return cls(
            frozenset(id(source) for source in sources if id(source) not in eligible),
            sources,
            MappingProxyType(dict(parents)),
            MappingProxyType(
                {owner: tuple(items) for owner, items in children.items()}
            ),
            unattached_ids,
        )

    def children(self, owner: object, fallback: Iterable[object]) -> Iterator[object]:
        if (
            self.parent_by_source_id is not None
            and id(owner) in self.parent_by_source_id
        ):
            assert self.children_by_owner_id is not None
            return self.admitted(self.children_by_owner_id.get(id(owner), ()))
        return self.admitted(fallback)

    def children_with_legacy(
        self, owner: object, fallback: Iterable[object]
    ) -> list[object]:
        legacy = tuple(fallback)
        known = self.parent_by_source_id
        if known is None:
            return list(self.admitted(legacy))
        assert self.children_by_owner_id is not None
        children = list(self.admitted(self.children_by_owner_id.get(id(owner), ())))
        seen = {id(child) for child in children}
        for child in self.admitted(legacy):
            # Category-only authored children have no normalized source identity.
            # A known identity must never regain its stale owner through fallback.
            if id(child) not in known and id(child) not in seen:
                children.append(child)
                seen.add(id(child))
        return children

    def admits(self, record: object) -> bool:
        return (
            id(record) not in self.ignored_ids and id(record) not in self.unattached_ids
        )

    def parent(self, record: object) -> object | None:
        if (
            self.parent_by_source_id is not None
            and id(record) in self.parent_by_source_id
        ):
            return self.parent_by_source_id[id(record)]
        return getattr(record, "parent", None)

    def admitted[T](self, records: Iterable[T]) -> Iterator[T]:
        return (record for record in records if self.admits(record))

    def descendant_ids_of_type(
        self, records: Iterable[object], ancestor_type: type[object]
    ) -> frozenset[int]:
        """Resolve descendants of an owner type once with cycle-safe path caching."""
        sources = tuple(records)
        resolved: dict[int, bool] = {}
        for source in sources:
            source_id = id(source)
            if source_id in resolved:
                continue
            path = [source_id]
            seen = {source_id}
            current = source
            inherited = False
            while (parent := self.parent(current)) is not None:
                if isinstance(parent, ancestor_type):
                    inherited = True
                    break
                parent_id = id(parent)
                if parent_id in resolved:
                    inherited = resolved[parent_id]
                    break
                if parent_id in seen:
                    break
                seen.add(parent_id)
                path.append(parent_id)
                current = parent
            for path_id in path:
                resolved[path_id] = inherited
        return frozenset(id(source) for source in sources if resolved[id(source)])
