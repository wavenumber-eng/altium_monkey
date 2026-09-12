"""Built-in vector resources for schematic harness covering patterns."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HarnessCoveringPatternResource:
    """One immutable covering-pattern tile bundled with the renderer."""

    resource_id: str
    source_width: int
    source_height: int
    view_box_width: int
    view_box_height: int
    path_data: str
    fill_rule: str | None = None
    clip_rule: str | None = None


_TUBING = HarnessCoveringPatternResource(
    resource_id="altium.harness.covering.tubing.v1",
    source_width=183,
    source_height=300,
    view_box_width=183,
    view_box_height=300,
    path_data="M0 0h183v300H0z",
)

_RESOURCES = (
    _TUBING,
    HarnessCoveringPatternResource(
        resource_id="altium.harness.covering.corrugated_tubing.v1",
        source_width=171,
        source_height=300,
        view_box_width=171,
        view_box_height=300,
        path_data="M135 42h36v216h-36v42H38v-42H0V42h38V0h97v42z",
    ),
    HarnessCoveringPatternResource(
        resource_id="altium.harness.covering.spiral_wrap.v1",
        source_width=287,
        source_height=300,
        view_box_width=287,
        view_box_height=300,
        path_data="M115 0h172L173 300H0L115 0z",
    ),
    HarnessCoveringPatternResource(
        resource_id="altium.harness.covering.tape.v1",
        source_width=209,
        source_height=300,
        view_box_width=209,
        view_box_height=300,
        path_data="M18 0h173v300H18z",
    ),
    HarnessCoveringPatternResource(
        resource_id="altium.harness.covering.braiding.v1",
        source_width=300,
        source_height=300,
        view_box_width=14,
        view_box_height=14,
        path_data=(
            "M7.185-4.166l-.667.666.09.09-4.75 4.75-.002-.002-5.334 5.335-.59-.59"
            "h.001l-2.445-2.445 10.75-10.75 2.947 2.946zm6.22 2.887h-.002L15.35.669"
            "l.001-.001.59.589-5.417 5.417-.09-.09h-.001L7.486 3.638l-.088-.089"
            " 5.417-5.416.59.589zM3.536.499L13.62 10.582l-3.123 3.123L.414 3.622"
            " 3.537.499zm17.315 9.669L10.436 20.584l-2.947-2.946h.001l-.088-.089"
            " 5.333-5.333 4.75-4.75.088.088.333-.333 2.946 2.947zM4.072 8.054"
            "l-.002.001 1.946 1.946.001-.001.59.59-4.75 4.75-.09-.09h-.001L.587"
            " 14.072l-5.334 5.333-.589-.589 5.334-5.333-1.179-1.179h.001l-.088-.089"
            " 4.75-4.75.59.59z"
        ),
        fill_rule="evenodd",
        clip_rule="evenodd",
    ),
)

_BY_RESOURCE_ID = {resource.resource_id: resource for resource in _RESOURCES}

# The harness-brush covering value falls through the managed switch to tubing.
_BY_COVERING_TYPE = {
    0: _TUBING,
    1: _TUBING,
    2: _RESOURCES[1],
    3: _RESOURCES[2],
    4: _RESOURCES[3],
    5: _RESOURCES[4],
}


def harness_covering_pattern_for_type(
    covering_type: int,
) -> HarnessCoveringPatternResource:
    """Return the managed resource selected for a covering-type value."""

    return _BY_COVERING_TYPE.get(int(covering_type), _TUBING)


def harness_covering_pattern_by_id(
    resource_id: str,
) -> HarnessCoveringPatternResource | None:
    """Resolve a portable covering-pattern resource ID."""

    return _BY_RESOURCE_ID.get(resource_id)
