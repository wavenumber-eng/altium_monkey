"""
Typed, queryable object collections for Altium containers.

`ObjectCollection` is the authoritative mutable store for container membership.
Filtered query results stay live, but are intentionally read-only for
structural mutation. This mirrors the split between container-owned
mutation and iterator/query-based selection.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Iterator, TypeVar, overload

T = TypeVar("T")


def _matches_attributes(obj: Any, attrs: dict[str, Any]) -> bool:
    """
    Return True when the object matches all requested Python-level attrs.
    """
    for attr_name, attr_value in attrs.items():
        if not hasattr(obj, attr_name):
            return False
        if getattr(obj, attr_name) != attr_value:
            return False
    return True


def _query_view_mutation_error(operation: str) -> TypeError:
    """
    Return the standard hard-fail error for query-view membership changes.
    """
    return TypeError(
        f"Cannot {operation} on an ObjectCollection query view; mutate returned "
        "objects in place or use the owning container's add_*()/add_object()/"
        "insert_object()/remove_object() APIs to change membership."
    )


class ObjectCollection:
    """
    List-like collection with typed query support.

        Stores objects in a flat list. Supports iteration, indexing, length,
        append, extend, and remove. Additionally provides of_type(), where(),
        and first() for filtered access.

        Query methods return live read-only views over this authoritative store.
    """

    __slots__ = ("_items",)

    def __init__(self, items: list[Any] | None = None) -> None:
        self._items: list[Any] = items if items is not None else []

    # -- List-like interface --

    def __iter__(self) -> Iterator:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, index: int) -> Any:
        return self._items[index]

    def __setitem__(self, index: int, value: Any) -> None:
        self._items[index] = value

    def __delitem__(self, index: int) -> None:
        del self._items[index]

    def __contains__(self, item: Any) -> bool:
        return item in self._items

    def __bool__(self) -> bool:
        return len(self._items) > 0

    def __repr__(self) -> str:
        return f"ObjectCollection({len(self._items)} objects)"

    def append(self, item: Any) -> None:
        """
        Add an object to the collection.
        """
        self._items.append(item)

    def extend(self, items: Iterable[Any]) -> None:
        """
        Add multiple objects to the collection.
        """
        self._items.extend(items)

    def insert(self, index: int, item: Any) -> None:
        """
        Insert an object at the given index.
        """
        self._items.insert(index, item)

    def remove(self, item: Any) -> None:
        """
        Remove the first occurrence of an object.
        """
        self._items.remove(item)

    def pop(self, index: int = -1) -> Any:
        """
        Remove and return an object at the given index.
        """
        return self._items.pop(index)

    def clear(self) -> None:
        """
        Remove all objects.
        """
        self._items.clear()

    def index(self, item: Any) -> int:
        """
        Return the index of the first occurrence of an object.
        """
        return self._items.index(item)

    # -- Query interface --

    def of_type(self, cls: type[T]) -> ObjectCollection:
        """
        Return a live query view containing only objects of the given type.

                Args:
                    cls: The type to filter by (isinstance check).

                Returns:
                    A live read-only ObjectCollection view with matching objects.
        """
        return ObjectCollectionView(self, lambda obj: isinstance(obj, cls))

    def where(self, **attrs: Any) -> ObjectCollection:
        """
        Return a live query view matching all given attributes.

                Matches against Python property names (not raw Altium field names).

                Args:
                    **attrs: Attribute name=value pairs. All must match.

                Returns:
                    A live read-only ObjectCollection view with matching objects.
        """
        return ObjectCollectionView(self, lambda obj: _matches_attributes(obj, attrs))

    @overload
    def first(self) -> Any | None: ...

    @overload
    def first(self, cls: type[T], **attrs: Any) -> T | None: ...

    def first(self, cls: type | None = None, **attrs: Any) -> Any | None:
        """
        Return the first object matching the optional type and attribute filters.

                Args:
                    cls: Optional type filter (isinstance check).
                    **attrs: Optional attribute name=value pairs.

                Returns:
                    The first matching object, or None if no match.
        """
        for obj in self:
            if cls is not None and not isinstance(obj, cls):
                continue
            if attrs and not _matches_attributes(obj, attrs):
                continue
            return obj
        return None

    def count(self, cls: type | None = None, **attrs: Any) -> int:
        """
        Count objects matching the optional type and attribute filters.

                Args:
                    cls: Optional type filter.
                    **attrs: Optional attribute name=value pairs.

                Returns:
                    Number of matching objects.
        """
        if cls is None and not attrs:
            return len(self._items)

        n = 0
        for obj in self._items:
            if cls is not None and not isinstance(obj, cls):
                continue
            if attrs and not _matches_attributes(obj, attrs):
                continue
            n += 1
        return n

    def to_list(self) -> list[Any]:
        """
        Return a plain list copy of the objects.
        """
        return list(self._items)


class ObjectCollectionView(ObjectCollection):
    """
    Read-only live filtered view over a parent ObjectCollection.
    """

    __slots__ = ("_parent", "_predicate")

    def __init__(
        self, parent: ObjectCollection, predicate: Callable[[Any], bool]
    ) -> None:
        super().__init__([])
        self._parent = parent
        self._predicate = predicate

    def _matching_items(self) -> list[Any]:
        return [item for item in self._parent if self._predicate(item)]

    def __iter__(self) -> Iterator:
        return iter(self._matching_items())

    def __len__(self) -> int:
        return len(self._matching_items())

    def __getitem__(self, index: int) -> Any:
        return self._matching_items()[index]

    def __setitem__(self, index: int, value: Any) -> None:
        raise _query_view_mutation_error("replace items")

    def __delitem__(self, index: int) -> None:
        raise _query_view_mutation_error("delete items")

    def __contains__(self, item: Any) -> bool:
        return item in self._matching_items()

    def extend(self, items: Iterable[Any]) -> None:
        raise _query_view_mutation_error("extend items")

    def __bool__(self) -> bool:
        return any(True for _ in self)

    def __repr__(self) -> str:
        return f"ObjectCollection({len(self)} objects)"

    def append(self, item: Any) -> None:
        raise _query_view_mutation_error("append")

    def extend(self, items: Iterable[Any]) -> None:
        raise _query_view_mutation_error("extend")

    def insert(self, index: int, item: Any) -> None:
        raise _query_view_mutation_error("insert")

    def remove(self, item: Any) -> None:
        raise _query_view_mutation_error("remove")

    def pop(self, index: int = -1) -> Any:
        raise _query_view_mutation_error("pop")

    def clear(self) -> None:
        raise _query_view_mutation_error("clear")

    def index(self, item: Any) -> int:
        return self._matching_items().index(item)

    def of_type(self, cls: type[T]) -> ObjectCollection:
        return ObjectCollectionView(self, lambda obj: isinstance(obj, cls))

    def where(self, **attrs: Any) -> ObjectCollection:
        return ObjectCollectionView(self, lambda obj: _matches_attributes(obj, attrs))

    def count(self, cls: type | None = None, **attrs: Any) -> int:
        if cls is None and not attrs:
            return len(self)

        n = 0
        for obj in self:
            if cls is not None and not isinstance(obj, cls):
                continue
            if attrs and not _matches_attributes(obj, attrs):
                continue
            n += 1
        return n

    def to_list(self) -> list:
        return self._matching_items()
