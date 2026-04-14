"""Helpers for marking stable public API surfaces."""

from typing import Any, TypeVar

T = TypeVar("T")


def _set_public_api_flag(target: Any) -> None:
    """Best-effort marker attachment for public API discovery."""
    try:
        setattr(target, "__public_api__", True)
    except (AttributeError, TypeError):
        return


def public_api(obj: T) -> T:
    """Mark a class, function, method, or descriptor as public API."""
    _set_public_api_flag(obj)

    if isinstance(obj, (classmethod, staticmethod)):
        _set_public_api_flag(obj.__func__)
    elif isinstance(obj, property):
        for accessor in (obj.fget, obj.fset, obj.fdel):
            if accessor is not None:
                _set_public_api_flag(accessor)

    return obj


__all__ = ["public_api"]
