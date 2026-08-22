"""Deterministic review projection for manufacturing materialization."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import cast

import msgspec

from .generated import (
    CapsuleGeometry,
    CircleGeometry,
    Geometry,
    HoleFeature,
    ManufacturingDocument,
    MaterialFeature,
    OrientedRectangleGeometry,
    PathGeometry,
    ProfileFeature,
    RegionGeometry,
    RoundedRectangleGeometry,
    StrokeGeometry,
)

_UNORDERED_ID_COLLECTIONS = (
    "board_occurrences",
    "child_board_requests",
    "stack_regions",
    "layers",
    "nets",
    "variant_selections",
    "component_occurrences",
    "drill_spans",
    "projections",
    "diagnostics",
)


def manufacturing_review_json(document: ManufacturingDocument) -> bytes:
    """Return stable normalized JSON for review and behavior signatures."""

    built = msgspec.to_builtins(document)
    if not isinstance(built, dict):
        raise TypeError("manufacturing document did not lower to an object")
    payload = cast(dict[str, object], built)
    for name in _UNORDERED_ID_COLLECTIONS:
        payload[name] = _sorted_id_rows(payload[name], name)
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def manufacturing_review_sha256(document: ManufacturingDocument) -> str:
    """Return the SHA-256 behavior signature of the normalized review JSON."""

    return hashlib.sha256(manufacturing_review_json(document)).hexdigest()


def manufacturing_semantic_summary(
    document: ManufacturingDocument,
) -> dict[str, object]:
    """Return a compact deterministic summary for oracle comparisons."""

    material_features = _material_features(document)
    hole_features = _hole_features(document)
    profile_features = _profile_features(document)
    features_by_layer = Counter(feature.layer_ref for feature in material_features)
    polarity_by_layer = Counter(
        (feature.layer_ref, feature.polarity) for feature in material_features
    )
    features_by_net = _features_by_net(material_features)
    geometry_counts = _geometry_counts(document)
    diagnostic_counts = _diagnostic_counts(document)
    return {
        "type": document.type,
        "version": document.version,
        "strictness": document.strictness,
        "review_sha256": manufacturing_review_sha256(document),
        "counts": {
            "board_occurrences": len(document.board_occurrences),
            "expanded_child_board_occurrences": sum(
                row.child_request_ref is not msgspec.UNSET
                for row in document.board_occurrences
            ),
            "child_board_requests": len(document.child_board_requests),
            "loaded_child_board_requests": sum(
                row.disposition == "loaded" for row in document.child_board_requests
            ),
            "unavailable_child_board_requests": sum(
                row.disposition == "unavailable"
                for row in document.child_board_requests
            ),
            "stack_regions": len(document.stack_regions),
            "layers": len(document.layers),
            "nets": len(document.nets),
            "variant_selections": len(document.variant_selections),
            "component_occurrences": len(document.component_occurrences),
            "fitted_component_occurrences": sum(
                row.fitted for row in document.component_occurrences
            ),
            "not_fitted_component_occurrences": sum(
                not row.fitted for row in document.component_occurrences
            ),
            "drill_spans": len(document.drill_spans),
            "material_features": len(material_features),
            "hole_features": len(hole_features),
            "profile_features": len(profile_features),
            "diagnostics": len(document.diagnostics),
        },
        "geometry_counts": dict(sorted(geometry_counts.items())),
        "net_feature_counts": dict(sorted(features_by_net.items())),
        "diagnostic_counts": {
            f"{severity}:{code}": count
            for (severity, code), count in sorted(diagnostic_counts.items())
        },
        "layers": _layer_summaries(document, features_by_layer, polarity_by_layer),
    }


def _material_features(document: ManufacturingDocument) -> list[MaterialFeature]:
    return [
        feature for feature in document.features if isinstance(feature, MaterialFeature)
    ]


def _hole_features(document: ManufacturingDocument) -> list[HoleFeature]:
    return [
        feature for feature in document.features if isinstance(feature, HoleFeature)
    ]


def _profile_features(document: ManufacturingDocument) -> list[ProfileFeature]:
    return [
        feature for feature in document.features if isinstance(feature, ProfileFeature)
    ]


def _features_by_net(features: list[MaterialFeature]) -> Counter[str]:
    return Counter(
        str(feature.source_net_ref)
        for feature in features
        if feature.source_net_ref is not msgspec.UNSET
    )


def _geometry_counts(document: ManufacturingDocument) -> Counter[str]:
    return Counter(_geometry_kind(feature.geometry) for feature in document.features)


def _diagnostic_counts(
    document: ManufacturingDocument,
) -> Counter[tuple[str, str]]:
    return Counter(
        (diagnostic.severity, diagnostic.code) for diagnostic in document.diagnostics
    )


def _layer_summaries(
    document: ManufacturingDocument,
    features_by_layer: Counter[str],
    polarity_by_layer: Counter[tuple[str, str]],
) -> list[dict[str, object]]:
    return [
        {
            "id": layer.id,
            "pcb_layer_ref": layer.pcb_layer_ref,
            "material_role": layer.material_role,
            "side": layer.side,
            "film_baseline": layer.film_baseline,
            "feature_count": features_by_layer[layer.id],
            "add_count": polarity_by_layer[(layer.id, "add")],
            "subtract_count": polarity_by_layer[(layer.id, "subtract")],
        }
        for layer in sorted(document.layers, key=lambda row: row.id)
    ]


def _geometry_kind(geometry: Geometry) -> str:
    if isinstance(geometry, PathGeometry):
        return "path"
    if isinstance(geometry, StrokeGeometry):
        return "stroke"
    if isinstance(geometry, CircleGeometry):
        return "circle"
    if isinstance(geometry, CapsuleGeometry):
        return "capsule"
    if isinstance(geometry, OrientedRectangleGeometry):
        return "oriented_rectangle"
    if isinstance(geometry, RoundedRectangleGeometry):
        return "rounded_rectangle"
    if isinstance(geometry, RegionGeometry):
        return "region"
    raise TypeError(f"unknown manufacturing geometry: {type(geometry).__name__}")


def _sorted_id_rows(value: object, collection: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{collection} is not an array")
    rows = cast(list[object], value)
    if any(
        not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows
    ):
        raise TypeError(f"{collection} contains a row without a string id")
    return sorted(rows, key=_row_id)


def _row_id(row: object) -> str:
    value = cast(dict[str, object], row)["id"]
    return cast(str, value)
