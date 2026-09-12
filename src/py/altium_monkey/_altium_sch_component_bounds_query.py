"""Bounded request-local component bounds over prepared ownership and state."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessBundle,
    AltiumSchHarnessLayoutConnectionPoint,
    AltiumSchHarnessLayoutCovering,
    HarnessCoveredItem,
    HarnessLayoutConnectionPointStyle,
    _HarnessConnectionBundleIndex,
    _abs_safe_i32,
    _harness_connection_point_bounds,
    _harness_internal_location,
    _unchecked_i32,
)
from ._altium_record_sch__physical_model import (
    _AltiumSchHarnessCavityComponent,
    _AltiumSchLineView,
)
from ._altium_sch_bounds_accessibility import _BoundsAccessibilityIndex
from ._altium_sch_bounds_covering import (
    _BoundsColdCoveringIndex,
    _BoundsCoveringIndex,
    _ColdCoveringCapture,
)
from ._altium_sch_bounds_physical import (
    _BoundsPhysicalModelIndex,
    _build_bounds_physical_model_index,
    _ENGINE_EMPTY_BOUNDS,
    _enclose_engine_bounds,
    _finish_cavity_bounds,
)
from ._altium_sch_bounds_traversal import _BoundsTraversalIndex
from ._altium_sch_bounds_source_tree import _BoundsDocumentOwnerIndex
from ._altium_sch_component_bounds import (
    _BoundsAngleStepLimitExceeded,
    _arc_own_bounds_with_work,
    _label_own_bounds_from_size,
    _primitive_own_bounds,
    _parameter_set_bounds_from_state,
)
from .altium_record_sch__arc import AltiumSchArc
from .altium_record_sch__bezier import AltiumSchBezier
from .altium_record_sch__blanket import AltiumSchBlanket
from .altium_record_sch__component import AltiumSchComponent, AltiumSchHarnessComponent
from .altium_record_sch__image import AltiumSchImage
from .altium_record_sch__label import AltiumSchLabel
from .altium_record_sch__parameter import AltiumSchImageParameter, AltiumSchParameter
from .altium_record_sch__parameter_set import AltiumSchParameterSet
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_record_sch__ieee_symbol import AltiumSchIeeeSymbol, _IEEE_SYMBOL_SHAPES
from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_sch__polyline import AltiumSchPolyline
from .altium_record_sch__wire import AltiumSchWire
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
from .altium_sch_geometry_oracle import SchGeometryBounds
from .altium_sch_enums import ParameterSetStyle
from .altium_sch_paint_order import (
    _ManagedPaintRectangle,
    _NonAccessibleBoundsSource,
    _reduce_non_accessible_children_bounds,
)


@dataclass(frozen=True)
class _BoundsLabelText:
    """Prepared display state, including the already-resolved superscript gate."""

    source: AltiumSchLabel
    display_string: str
    font_id: int
    superscript: tuple[str, int] | None


_BoundsTextMeasure = Callable[[tuple[int, int], str, int], SchGeometryBounds]


@dataclass(frozen=True)
class _BoundsParameterSetText:
    """Display string captured after evaluator and preference resolution."""

    source: AltiumSchParameterSet
    display_string: str


@dataclass(frozen=True)
class _BoundsQueryLimits:
    max_queries: int = 1_000_000
    max_visits: int = 1_000_000
    max_vertices: int = 1_000_000
    max_angle_steps: int = 4096
    max_text_characters: int = 1_000_000
    max_dependency_sources: int = 1_000_000
    max_dependency_references: int = 1_000_000
    max_model_evaluations: int = 1_000_000
    max_blanket_edge_tests: int = 1_000_000

    def __post_init__(self) -> None:
        values = (
            self.max_queries,
            self.max_visits,
            self.max_vertices,
            self.max_angle_steps,
            self.max_text_characters,
            self.max_dependency_sources,
            self.max_dependency_references,
            self.max_model_evaluations,
            self.max_blanket_edge_tests,
        )
        # Equality-based counters cannot enforce fractional or non-finite limits.
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError(
                "component bounds request limits must be nonnegative integers"
            )


def _bounds_source_vertex_count(record: object) -> int:
    if isinstance(record, (AltiumSchPolygon, AltiumSchBezier, AltiumSchPolyline)):
        return len(record.vertices)
    if isinstance(record, AltiumSchWire):
        return len(record.points)
    if isinstance(record, _AltiumSchLineView):
        return len(record.lines) * 2
    if isinstance(record, AltiumSchIeeeSymbol):
        polylines, polygons, _ = _IEEE_SYMBOL_SHAPES.get(record.symbol, ([], [], []))
        return sum(map(len, polylines)) + sum(map(len, polygons))
    return 0


_BoundsSteps = Generator[
    tuple[Literal["own", "model"], int],
    SchGeometryBounds | None,
    SchGeometryBounds | None,
]


class _ComponentBoundsQueries:
    """Calculate ordinary/cavity components using prepared, same-tree state.

    Only audited own-bounds adapters are accepted. Callers must capture import
    phases and editor state before constructing this request; no final-tree
    accessibility replay or alternate-symbol substitution is inferred here.
    Missing-model parameter text and unaudited primitive families remain unsupported.
    """

    def __init__(
        self,
        traversal: _BoundsTraversalIndex,
        accessibility: _BoundsAccessibilityIndex,
        *,
        limits: _BoundsQueryLimits = _BoundsQueryLimits(),
        covering_bounds: _BoundsCoveringIndex | None = None,
        cold_coverings: _BoundsColdCoveringIndex | None = None,
        document_owners: _BoundsDocumentOwnerIndex | None = None,
        label_text: Mapping[int, _BoundsLabelText] | None = None,
        parameter_set_text: Mapping[int, _BoundsParameterSetText] | None = None,
        measure_text: _BoundsTextMeasure | None = None,
    ) -> None:
        if accessibility.tree is not traversal.tree:
            raise ValueError("component bounds indexes must share the same source tree")
        if covering_bounds is not None and covering_bounds.tree is not traversal.tree:
            raise ValueError("covering bounds index must share the same source tree")
        self._validate_cold_coverings(traversal, covering_bounds, cold_coverings)
        if document_owners is not None and document_owners.tree is not traversal.tree:
            raise ValueError("document-owner index must share the same source tree")
        self._traversal = traversal
        self._accessibility = accessibility
        self._limits = limits
        self._covering_bounds = covering_bounds
        self._cold_coverings = cold_coverings
        self._document_owners = document_owners
        references = len(label_text or {}) + len(parameter_set_text or {})
        if references > limits.max_dependency_references:
            raise ValueError("component bounds dependency reference limit exceeded")
        self._label_text = self._capture_label_text(traversal, label_text, limits)
        self._parameter_set_text = self._capture_parameter_set_text(
            traversal, parameter_set_text
        )
        self._measure_text = measure_text
        self._parameter_set_classifications: dict[int, bool] = {}
        self._results: dict[int, _ManagedPaintRectangle] = {}
        self._arc_angles: dict[int, tuple[float, float]] = {}
        self._visits = 0
        self._queries = 0
        self._vertices = 0
        self._angle_steps = 0
        self._text_characters = 0
        self._dependency_sources = 0
        self._dependency_references = references
        self._model_evaluations = 0
        self._blanket_edge_tests = 0
        self._bundle_indexes: dict[int | None, _HarnessConnectionBundleIndex] = {}
        self._connection_bounds: dict[int, SchGeometryBounds] = {}
        self._physical_models: _BoundsPhysicalModelIndex | None = None
        self._physical_model_bounds_cache: dict[int, SchGeometryBounds] = {}
        self._failed = False

    @staticmethod
    def _capture_label_text(
        traversal: _BoundsTraversalIndex,
        prepared: Mapping[int, _BoundsLabelText] | None,
        limits: _BoundsQueryLimits,
    ) -> dict[int, _BoundsLabelText]:
        if prepared is None:
            return {}
        if len(prepared) > limits.max_dependency_references:
            raise ValueError("component bounds dependency reference limit exceeded")
        records = traversal.tree.records
        for index, entry in prepared.items():
            if type(index) is not int or not 0 <= index < len(records):
                raise ValueError("label text source is outside the source tree")
            if (
                type(entry.source) is not AltiumSchLabel
                or entry.source is not records[index]
            ):
                raise ValueError(
                    "label text capture has a different or unsupported source"
                )
        return dict(prepared)

    @staticmethod
    def _capture_parameter_set_text(
        traversal: _BoundsTraversalIndex,
        prepared: Mapping[int, _BoundsParameterSetText] | None,
    ) -> dict[int, _BoundsParameterSetText]:
        if prepared is None:
            return {}
        records = traversal.tree.records
        for index, entry in prepared.items():
            if type(index) is not int or not 0 <= index < len(records):
                raise ValueError("parameter-set text source is outside the source tree")
            if (
                type(entry.source) is not AltiumSchParameterSet
                or entry.source is not records[index]
            ):
                raise ValueError(
                    "parameter-set text capture has a different or unsupported source"
                )
        return dict(prepared)

    @staticmethod
    def _validate_cold_coverings(
        traversal: _BoundsTraversalIndex,
        covering_bounds: _BoundsCoveringIndex | None,
        cold_coverings: _BoundsColdCoveringIndex | None,
    ) -> None:
        if cold_coverings is None:
            return
        if cold_coverings.tree is not traversal.tree:
            raise ValueError("cold covering index must share the same source tree")
        if covering_bounds is not None and any(
            index in covering_bounds.own_bounds for index in cold_coverings.captures
        ):
            raise ValueError("covering source has conflicting cold and prepared state")

    def calculate(self, root: int) -> _ManagedPaintRectangle:
        if self._failed:
            raise RuntimeError(
                "component bounds request is unusable after a failed query"
            )
        try:
            if self._queries == self._limits.max_queries:
                raise ValueError("component bounds request query limit exceeded")
            self._queries += 1
            return self._calculate(root)
        except Exception:
            self._failed = True
            raise

    def _calculate(self, root: int) -> _ManagedPaintRectangle:
        records = self._traversal.tree.records
        if not 0 <= root < len(records):
            raise ValueError("component bounds root is outside the source tree")
        component = records[root]
        if not isinstance(component, AltiumSchComponent):
            raise ValueError("component bounds query requires a component root")
        if type(component) not in (
            AltiumSchComponent,
            AltiumSchHarnessComponent,
            _AltiumSchHarnessCavityComponent,
        ):
            raise NotImplementedError(
                f"component bounds query for {type(component).__name__}"
            )
        cached = self._results.get(root)
        if cached is not None:
            return cached
        bounds = _reduce_non_accessible_children_bounds(
            _harness_internal_location(component.location),
            self._sources(root),
            max_sources=self._limits.max_visits,
        )
        self._results[root] = bounds
        return bounds

    def _visit(self) -> None:
        if self._visits == self._limits.max_visits:
            raise ValueError("component bounds request visit limit exceeded")
        self._visits += 1

    def _sources(self, root: int) -> Iterator[_NonAccessibleBoundsSource]:
        self._visit()
        # The root bypasses spatial/current-part/accessibility filtering.
        yield _NonAccessibleBoundsSource(
            self._own_bounds(root), include_accessible=True
        )
        ignore_cavity = (
            type(self._traversal.tree.records[root]) is AltiumSchHarnessComponent
            and self._physical_model_index().cavity_components[root] is not None
        )
        for index in self._traversal.tree.iter_descendant_indices(root):
            # Rejected nodes still consume work and never prune their children.
            self._visit()
            if ignore_cavity and self._traversal.has_cavity_ancestor[index]:
                continue
            if not self._traversal.spatial_current_part[index]:
                continue
            if self._accessibility.is_accessible[index]:
                continue
            yield _NonAccessibleBoundsSource(self._own_bounds(index))

    def _own_bounds(self, index: int) -> SchGeometryBounds | None:
        # Physical models may nest arbitrarily. Explicit continuations preserve
        # managed call order without consuming the Python call stack.
        stack = [self._own_bounds_steps(index)]
        result: SchGeometryBounds | None = None
        while stack:
            try:
                kind, child = stack[-1].send(result)
            except StopIteration as done:
                result = cast(SchGeometryBounds | None, done.value)
                stack.pop()
                continue
            stack.append(
                self._physical_model_steps(child)
                if kind == "model"
                else self._own_bounds_steps(child)
            )
            result = None
        return result

    def _own_bounds_steps(self, index: int) -> _BoundsSteps:
        record = self._traversal.tree.records[index]
        if type(record) is AltiumSchHarnessComponent:
            return (yield from self._harness_component_steps(index, record))
        if type(record) is AltiumSchImageParameter:
            model = self._physical_model_index().parameter_models[index]
            if model is None:
                raise NotImplementedError(
                    "image-parameter text own bounds without a model"
                )
            return (yield "model", model)
        return self._own_bounds_direct(index)

    def _own_bounds_direct(self, index: int) -> SchGeometryBounds | None:
        record = self._traversal.tree.records[index]
        if type(record) is AltiumSchLabel:
            return self._label_bounds(index, record)
        if type(record) is AltiumSchParameterSet:
            return self._parameter_set_bounds(index, record)
        if type(record) is AltiumSchHarnessLayoutCovering:
            return self._prepared_covering_bounds(index)
        if type(record) is AltiumSchHarnessLayoutConnectionPoint:
            return self._connection_point_bounds(index, record)
        if isinstance(record, AltiumSchSheetSymbol) and record.sheet_name is not None:
            characters = len(record.sheet_name.text)
            if characters > self._limits.max_text_characters - self._text_characters:
                raise ValueError(
                    "component bounds request text character limit exceeded"
                )
            self._text_characters += characters
        if isinstance(record, AltiumSchArc):
            angles = self._arc_angles.get(index, (record.start_angle, record.end_angle))
            try:
                bounds, next_angles, steps = _arc_own_bounds_with_work(
                    record,
                    angles,
                    max_angle_steps=self._limits.max_angle_steps - self._angle_steps,
                )
            except _BoundsAngleStepLimitExceeded as error:
                self._angle_steps += error.steps
                raise
            self._angle_steps += steps
            self._arc_angles[index] = next_angles
            return bounds
        return self._bounded_primitive_bounds(record)

    def _parameter_set_bounds(
        self, index: int, record: AltiumSchParameterSet
    ) -> SchGeometryBounds:
        if self._document_owners is None:
            raise NotImplementedError(
                "AltiumSchParameterSet own bounds require captured document ownership"
            )
        has_document = self._document_owners.document_sources[index] is not None
        differential_pair = False
        if has_document:
            if index not in self._parameter_set_classifications:
                self._parameter_set_classifications[index] = (
                    self._classify_parameter_set(index)
                )
            differential_pair = self._parameter_set_classifications[index]
        display_string = ""
        if (
            has_document
            and not differential_pair
            and record.style == ParameterSetStyle.LARGE
        ):
            display_string = self._parameter_set_display_string(index)
        return _parameter_set_bounds_from_state(
            _harness_internal_location(record.location),
            record.orientation,
            record.style,
            has_effective_document=has_document,
            differential_pair=differential_pair,
            display_string=display_string,
            measure=self._measure_text,
            max_text_characters=self._limits.max_text_characters,
        )

    def _parameter_set_display_string(self, index: int) -> str:
        entry = self._parameter_set_text.get(index)
        if entry is None or self._measure_text is None:
            raise NotImplementedError(
                "parameter-set large bounds require prepared display state and a text provider"
            )
        # The engine measures twice at distinct origins. Reserve both calls in
        # the shared request budget before either reaches the provider.
        self._charge_dependency_text(2 * len(entry.display_string))
        return entry.display_string

    def _classify_parameter_set(self, index: int) -> bool:
        tree = self._traversal.tree
        for child_index in tree.children[index]:
            self._visit()
            child = tree.records[child_index]
            if type(child) is not AltiumSchParameter:
                continue
            self._charge_dependency_text(len(child.name))
            if dotnet_ordinal_ignore_case_key(child.name) == "DIFFERENTIALPAIR":
                self._charge_dependency_text(len(child.text))
                return dotnet_ordinal_ignore_case_key(child.text) == "TRUE"
        return False

    def _label_bounds(self, index: int, record: AltiumSchLabel) -> SchGeometryBounds:
        entry = self._label_text.get(index)
        if entry is None or self._measure_text is None:
            raise NotImplementedError(
                "AltiumSchLabel own bounds require prepared display state and a text provider"
            )
        characters = len(entry.display_string)
        if entry.superscript is not None:
            characters += len(entry.superscript[0])
        # Reserve both calls before invoking the provider; a failed request
        # cannot expose a partial aggregate or continue after exhaustion.
        self._charge_dependency_text(characters)
        location = _harness_internal_location(record.location)
        measured = self._measure_text(location, entry.display_string, entry.font_id)
        width = _abs_safe_i32(_unchecked_i32(measured.right - measured.left))
        height = _abs_safe_i32(_unchecked_i32(measured.top - measured.bottom))
        if entry.superscript is not None:
            text, font_id = entry.superscript
            extra = self._measure_text(location, text, font_id)
            extra_width = _unchecked_i32(extra.right - extra.left)
            if extra_width == -(1 << 31):
                raise OverflowError("superscript text width cannot be Int32.MinValue")
            width = _unchecked_i32(width + 200_000 + abs(extra_width))
        return _label_own_bounds_from_size(
            location, width, height, int(record.justification), int(record.orientation)
        )

    def _bounded_primitive_bounds(self, record: object) -> SchGeometryBounds | None:
        vertices = _bounds_source_vertex_count(record)
        remaining = self._limits.max_vertices - self._vertices
        if vertices > remaining:
            raise ValueError("component bounds request vertex limit exceeded")
        self._vertices += vertices
        edge_tests_remaining = (
            self._limits.max_blanket_edge_tests - self._blanket_edge_tests
        )
        if type(record) is AltiumSchBlanket and record.is_collapsed:
            edge_tests = 4 * vertices * vertices
            if edge_tests > edge_tests_remaining:
                raise ValueError("blanket edge-test limit exceeded")
            self._blanket_edge_tests += edge_tests
        return _primitive_own_bounds(
            record, max_vertices=remaining, max_blanket_edge_tests=edge_tests_remaining
        )

    def _prepared_covering_bounds(self, index: int) -> SchGeometryBounds:
        if self._cold_coverings is not None:
            captured = self._cold_coverings.captures.get(index)
            if captured is not None:
                return self._cold_covering_bounds(index, captured)
        if (
            self._covering_bounds is None
            or index not in self._covering_bounds.own_bounds
        ):
            raise NotImplementedError(
                "covering own bounds require prepared border state"
            )
        return self._covering_bounds.own_bounds[index]

    def _cold_covering_bounds(
        self, index: int, captured: _ColdCoveringCapture
    ) -> SchGeometryBounds:
        record = self._traversal.tree.records[index]
        if (
            captured.source is not record
            or captured.source.covered_items is not captured.covered_items
        ):
            raise ValueError("cold covering source changed after capture")
        # Current non-layout documents produce no trees. Both tree lookup calls
        # still scan covered items; reference lookup/dedup cannot affect bounds
        # in this fixed phase, so do not build an unused document topology.
        for _ in range(2):
            self._charge_cold_covering_items(captured.covered_items)
        return _ENGINE_EMPTY_BOUNDS

    def _charge_cold_covering_items(
        self, items: tuple[HarnessCoveredItem, ...]
    ) -> None:
        remaining = self._limits.max_dependency_references - self._dependency_references
        if len(items) > remaining:
            raise ValueError("component bounds dependency reference limit exceeded")
        self._dependency_references += len(items)
        for item in items:
            self._charge_dependency_text(len(item.unique_id))

    def _connection_bundle_sources(
        self, parent: int | None
    ) -> Iterator[AltiumSchHarnessBundle]:
        tree = self._traversal.tree
        indices = range(len(tree.records)) if parent is None else tree.children[parent]
        for index in indices:
            if self._dependency_sources == self._limits.max_dependency_sources:
                raise ValueError("component bounds dependency source limit exceeded")
            self._dependency_sources += 1
            if parent is None and tree.parents[index] is not None:
                continue
            record = tree.records[index]
            if type(record) is AltiumSchHarnessBundle:
                self._charge_dependency_text(len(str(record.unique_id or "")))
                yield record

    def _charge_dependency_text(self, characters: int) -> None:
        if characters > self._limits.max_text_characters - self._text_characters:
            raise ValueError("component bounds request text character limit exceeded")
        self._text_characters += characters

    def _connection_point_bundles(
        self, index: int, record: AltiumSchHarnessLayoutConnectionPoint
    ) -> tuple[AltiumSchHarnessBundle, ...]:
        identifiers = record.connected_bundles_unique_ids
        remaining = self._limits.max_dependency_references - self._dependency_references
        if len(identifiers) > remaining:
            raise ValueError("component bounds dependency reference limit exceeded")
        self._dependency_references += len(identifiers)
        for identifier in identifiers:
            self._charge_dependency_text(len(identifier))
        parent = self._traversal.tree.parents[index]
        bundle_index = self._bundle_indexes.get(parent)
        if bundle_index is None:
            bundle_index = _HarnessConnectionBundleIndex(
                self._connection_bundle_sources(parent)
            )
            self._bundle_indexes[parent] = bundle_index
        bundles = bundle_index.resolve(
            identifiers,
            max_resolved_bundles=(
                self._limits.max_dependency_references - self._dependency_references
            ),
        )
        self._dependency_references += len(bundles)
        vertices = sum(len(bundle.points) for bundle in bundles)
        if vertices > self._limits.max_vertices - self._vertices:
            raise ValueError("component bounds request vertex limit exceeded")
        self._vertices += vertices
        return bundles

    def _connection_point_bounds(
        self, index: int, record: AltiumSchHarnessLayoutConnectionPoint
    ) -> SchGeometryBounds:
        cached = self._connection_bounds.get(index)
        if cached is not None:
            return cached
        bundles: tuple[AltiumSchHarnessBundle, ...] = ()
        if (
            record.style is HarnessLayoutConnectionPointStyle.INSULATOR
            and record.connected_bundles_unique_ids
        ):
            bundles = self._connection_point_bundles(index, record)
        x, y = _harness_internal_location(record.location)
        bounds = _harness_connection_point_bounds(
            record, x=x, y=y, resolved_bundles=bundles
        )
        self._connection_bounds[index] = bounds
        return bounds

    def _physical_model_index(self) -> _BoundsPhysicalModelIndex:
        if self._physical_models is None:
            tree = self._traversal.tree
            remaining = self._limits.max_dependency_sources - self._dependency_sources
            self._physical_models = _build_bounds_physical_model_index(
                tree,
                max_sources=remaining,
                max_references=(
                    self._limits.max_dependency_references - self._dependency_references
                ),
            )
            self._dependency_sources += len(tree.records)
            self._dependency_references += self._physical_models.reference_count
        return self._physical_models

    def _physical_model_steps(self, model: int) -> _BoundsSteps:
        if self._model_evaluations == self._limits.max_model_evaluations:
            raise ValueError("component bounds model evaluation limit exceeded")
        self._model_evaluations += 1
        record = self._traversal.tree.records[model]
        if type(record) is _AltiumSchHarnessCavityComponent:
            # Cavity reads can advance arc angles. Never cache the aggregate and
            # thereby suppress later read-side effects in another managed call.
            return (yield from self._cavity_model_steps(model, record))
        cached = self._physical_model_bounds_cache.get(model)
        if cached is not None:
            return cached
        if type(record) not in (AltiumSchImage, _AltiumSchLineView):
            raise NotImplementedError(
                f"physical-model own bounds for {type(record).__name__}"
            )
        bounds = self._own_bounds_direct(model)
        assert isinstance(bounds, SchGeometryBounds)
        self._physical_model_bounds_cache[model] = bounds
        return bounds

    def _cavity_model_steps(
        self, index: int, record: _AltiumSchHarnessCavityComponent
    ) -> _BoundsSteps:
        self._visit()
        # Engine selection completes before any own-bounds evaluation. Its
        # unfiltered cavity root has HasOwn=false, so only children need calls.
        relevant: list[int] = []
        for child, passes in self._traversal.iter_visible_child_candidates(index):
            self._visit()
            if passes:
                relevant.append(child)
        bounds = _ENGINE_EMPTY_BOUNDS
        for child in relevant:
            candidate = yield "own", child
            bounds = _enclose_engine_bounds(bounds, candidate)
        return _finish_cavity_bounds(
            bounds, _harness_internal_location(record.location)
        )

    def _harness_component_steps(
        self, index: int, record: AltiumSchHarnessComponent
    ) -> _BoundsSteps:
        physical = self._physical_model_index()
        main = physical.main_parameters[index]
        if main is None:
            return None
        parameter = self._traversal.tree.records[main]
        assert isinstance(parameter, AltiumSchImageParameter)
        if type(parameter) is not AltiumSchImageParameter:
            raise NotImplementedError(
                f"physical-model parameter for {type(parameter).__name__}"
            )
        if parameter.is_hidden:
            return None
        model = physical.parameter_models[main]
        if model is not None:
            return (yield "model", model)
        x, y = _harness_internal_location(record.location)
        return SchGeometryBounds(
            left=_unchecked_i32(x - 5),
            bottom=_unchecked_i32(y - 5),
            right=_unchecked_i32(x + 5),
            top=_unchecked_i32(y + 5),
        )
