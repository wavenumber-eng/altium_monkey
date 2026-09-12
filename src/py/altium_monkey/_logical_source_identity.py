"""Portable logical source-identity helpers."""

from __future__ import annotations

import re

_ASCII_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def _validate_relative_identity_boundary(identity: str) -> None:
    if (
        not identity
        or identity.startswith(("/", "\\"))
        or _ASCII_DRIVE_PREFIX.match(identity)
    ):
        raise ValueError(
            "logical source identity must be a nonempty project-relative path"
        )


def _normalize_project_document_identity(identity: str) -> str:
    """Normalize one raw project reference while retaining leading parents."""
    _validate_relative_identity_boundary(identity)
    components = _lexical_identity_components(identity)
    if not components:
        raise ValueError("logical source identity normalizes to an empty path")
    first_document_component = next(
        (component for component in components if component != ".."), None
    )
    if first_document_component is not None and _ASCII_DRIVE_PREFIX.match(
        first_document_component
    ):
        raise ValueError(
            "logical source identity must be a nonempty project-relative path"
        )
    return "/".join(components)


def _lexical_identity_components(identity: str) -> list[str]:
    components: list[str] = []
    for component in identity.replace("\\", "/").split("/"):
        if component in {"", "."}:
            continue
        if component == ".." and components and components[-1] != "..":
            components.pop()
        else:
            components.append(component)
    return components


def _normalize_logical_source_identity(identity: str) -> str:
    """Normalize one project-root-relative identity without host path behavior."""
    normalized = _normalize_project_document_identity(identity)
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError("logical source identity escapes the project root")
    return normalized


def _combine_project_document_identity(
    project_identity: str,
    document_reference: str,
) -> str:
    """Resolve a raw document reference against its logical project parent."""
    project = _normalize_logical_source_identity(project_identity)
    document = _normalize_project_document_identity(document_reference)
    parent = project.rpartition("/")[0]
    combined = f"{parent}/{document}" if parent else document
    return _normalize_logical_source_identity(combined)


def _logical_source_identity_key(identity: str) -> str:
    """Return the scalar-wise Unicode-lowercase, no-normalization key."""
    return "".join(character.lower() for character in identity)


def _logical_source_basename(identity: str) -> str:
    """Return the last normalized logical component without host path rules."""
    return identity.rsplit("/", 1)[-1]
