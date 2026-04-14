"""
Dedicated helpers for synthesizing `Board6/Data` layer-stack sections.

This module owns the first builder-facing layer-stack template surface for
`PcbDocBuilder`. The public board builder remains the entry point; this file
keeps the stack template/catalog and stack-entry fabrication logic separate so
`altium_pcbdoc_builder.py` does not keep growing into a monolith.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value

LAYER_STACK_CACHE_RE = re.compile(r"^V9_CACHE_LAYER(\d+)_(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class PcbDocDielectricTemplate:
    name: str
    thickness_mils: float
    dielectric_constant: float
    material: str
    dielectric_type: int = 0
    loss_tangent: float | None = None


@dataclass(frozen=True)
class PcbDocCopperLayerTemplate:
    legacy_layer_id: int
    v7_layer_id: int
    name: str
    copper_thickness_mils: float = 1.4
    component_placement: int = 0
    copper_orientation: int | None = None


@dataclass(frozen=True)
class PcbDocLayerStackTemplate:
    name: str
    copper_layers: tuple[PcbDocCopperLayerTemplate, ...]
    dielectrics_between: tuple[PcbDocDielectricTemplate, ...]
    default_layer_pair_low: str = "TOP"
    default_layer_pair_high: str = "BOTTOM"

    def __post_init__(self) -> None:
        if len(self.copper_layers) < 2:
            raise ValueError("Layer stack template requires at least 2 copper layers")
        if len(self.dielectrics_between) != len(self.copper_layers) - 1:
            raise ValueError(
                "Layer stack template dielectric count must equal copper count - 1"
            )

    @classmethod
    def two_layer_fr4(cls) -> "PcbDocLayerStackTemplate":
        # Physical 2-layer default targets a finished stack near 62.5 mil:
        # 0.4 top mask + 1.4 top copper + 58.9 core + 1.4 bottom copper + 0.4 bottom mask.
        return cls(
            name="2-layer-fr4",
            copper_layers=(
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=1,
                    v7_layer_id=16777217,
                    name="Top Layer",
                    copper_thickness_mils=1.4,
                    component_placement=1,
                ),
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=32,
                    v7_layer_id=16842751,
                    name="Bottom Layer",
                    copper_thickness_mils=1.4,
                    component_placement=2,
                ),
            ),
            dielectrics_between=(
                PcbDocDielectricTemplate(
                    name="Dielectric 1",
                    thickness_mils=58.9,
                    dielectric_constant=4.8,
                    material="FR-4",
                    dielectric_type=0,
                ),
            ),
        )

    @classmethod
    def four_layer_fr4(cls) -> "PcbDocLayerStackTemplate":
        return cls(
            name="4-layer-fr4",
            copper_layers=(
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=1,
                    v7_layer_id=16777217,
                    name="Top Layer",
                    copper_thickness_mils=1.4,
                    component_placement=1,
                ),
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=2,
                    v7_layer_id=16777218,
                    name="GND",
                    copper_thickness_mils=1.378,
                ),
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=3,
                    v7_layer_id=16777219,
                    name="PWR",
                    copper_thickness_mils=1.378,
                ),
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=32,
                    v7_layer_id=16842751,
                    name="Bottom Layer",
                    copper_thickness_mils=1.4,
                    component_placement=2,
                    copper_orientation=1,
                ),
            ),
            dielectrics_between=(
                PcbDocDielectricTemplate(
                    name="Dielectric 1",
                    thickness_mils=2.8,
                    dielectric_constant=4.1,
                    material="PP-006",
                    dielectric_type=2,
                    loss_tangent=0.020,
                ),
                PcbDocDielectricTemplate(
                    name="Dielectric 2",
                    thickness_mils=28.4,
                    dielectric_constant=4.8,
                    material="FR-4",
                    dielectric_type=0,
                ),
                PcbDocDielectricTemplate(
                    name="Dielectric 3",
                    thickness_mils=2.8,
                    dielectric_constant=4.1,
                    material="PP-006",
                    dielectric_type=2,
                    loss_tangent=0.020,
                ),
            ),
        )


def is_layer_stack_core_entry_key(key: str | None) -> bool:
    if not key:
        return False
    if key == "LAYER":
        return False
    if key.startswith("V9_STACK_LAYER"):
        return True
    if key.startswith("LAYER") and len(key) > 5 and key[5].isdigit():
        return True
    return False


def is_layer_pair_entry_key(key: str | None) -> bool:
    return bool(key and key.startswith("LAYERPAIR"))


class PcbDocLayerStackBuilder:
    """
    Template resolver plus Board6/Data layer-stack section fabricator.
    """

    @staticmethod
    def resolve_template(
        template: PcbDocLayerStackTemplate | str,
    ) -> PcbDocLayerStackTemplate:
        if isinstance(template, PcbDocLayerStackTemplate):
            return template
        key = template.strip().lower()
        if key in {"2-layer", "2layer", "two-layer", "two_layer"}:
            return PcbDocLayerStackTemplate.two_layer_fr4()
        if key in {"4-layer", "4layer", "four-layer", "four_layer"}:
            return PcbDocLayerStackTemplate.four_layer_fr4()
        raise ValueError(f"Unknown layer stack template: {template}")

    @staticmethod
    def build_layer_pair_entry_strings(
        template: PcbDocLayerStackTemplate,
        *,
        substack_guid: str,
    ) -> tuple[str, ...]:
        return (
            "LAYERPAIR0DRILLDRAWING=FALSE",
            "LAYERPAIR0DRILLGUIDE=FALSE",
            f"LAYERPAIR0LOW={template.default_layer_pair_low}",
            f"LAYERPAIR0HIGH={template.default_layer_pair_high}",
            f"LAYERPAIR0SUBSTACK_0={substack_guid}",
        )

    @staticmethod
    def build_legacy_layer_entry_strings(
        template: PcbDocLayerStackTemplate,
    ) -> tuple[str, ...]:
        copper_layers_by_legacy_id = {
            layer.legacy_layer_id: layer for layer in template.copper_layers
        }
        dielectrics_after_legacy_id: dict[int, PcbDocDielectricTemplate] = {}
        for copper_layer, dielectric in zip(
            template.copper_layers[:-1], template.dielectrics_between
        ):
            dielectrics_after_legacy_id[copper_layer.legacy_layer_id] = dielectric

        ordered_ids = [layer.legacy_layer_id for layer in template.copper_layers]
        prev_by_layer_id = {ordered_ids[0]: 0}
        next_by_layer_id: dict[int, int] = {}
        for left, right in zip(ordered_ids, ordered_ids[1:]):
            next_by_layer_id[left] = right
            prev_by_layer_id[right] = left
        next_by_layer_id[ordered_ids[-1]] = 0

        entries: list[str] = []
        for layer_id in range(1, 33):
            copper = copper_layers_by_legacy_id.get(layer_id)
            dielectric = dielectrics_after_legacy_id.get(layer_id)
            name = (
                copper.name
                if copper is not None
                else (
                    "Top Layer"
                    if layer_id == 1
                    else "Bottom Layer"
                    if layer_id == 32
                    else f"Mid-Layer {layer_id - 1}"
                )
            )
            copper_thickness = (
                copper.copper_thickness_mils if copper is not None else 1.4
            )
            if dielectric is None:
                diel_const = 4.8
                diel_height = 12.6
                diel_material = "FR-4"
                diel_type = 0
            else:
                diel_const = dielectric.dielectric_constant
                diel_height = dielectric.thickness_mils
                diel_material = dielectric.material
                diel_type = dielectric.dielectric_type

            entries.extend(
                (
                    f"LAYER{layer_id}COPTHICK={_format_mil_value(copper_thickness)}",
                    f"LAYER{layer_id}DIELCONST={format(float(diel_const), '.3f')}",
                    f"LAYER{layer_id}DIELHEIGHT={_format_mil_value(diel_height)}",
                    f"LAYER{layer_id}DIELMATERIAL={diel_material}",
                    f"LAYER{layer_id}DIELTYPE={int(diel_type)}",
                    f"LAYER{layer_id}MECHENABLED=FALSE",
                    f"LAYER{layer_id}NAME={name}",
                    f"LAYER{layer_id}NEXT={int(next_by_layer_id.get(layer_id, 0))}",
                    f"LAYER{layer_id}PREV={int(prev_by_layer_id.get(layer_id, 0))}",
                )
            )
        return tuple(entries)

    @staticmethod
    def build_v9_stack_entry_strings(
        template: PcbDocLayerStackTemplate,
        *,
        substack_guid: str,
    ) -> tuple[str, ...]:
        rows: list[dict[str, str]] = []

        def add_row(index: int, fields: dict[str, str]) -> None:
            guid = uuid.uuid5(
                uuid.NAMESPACE_OID, f"pcbdoc-builder:{template.name}:v9:{index}"
            )
            row = {
                "ID": f"{{{str(guid).upper()}}}",
                "USEDBYPRIMS": "FALSE",
                f"{substack_guid}CONTEXT": "0",
                f"{substack_guid}USEDBYPRIMS": "FALSE",
            }
            row.update(fields)
            rows.append(row)

        add_row(0, {"LAYERID": "16973832", "NAME": "Top Paste"})
        add_row(1, {"LAYERID": "16973830", "NAME": "Top Overlay"})
        add_row(
            2,
            {
                "LAYERID": "16973834",
                "NAME": "Top Solder",
                "COVERLAY_EXPANSION": "0mil",
                "DIELCONST": "3.500",
                "DIELHEIGHT": "0.4mil",
                "DIELMATERIAL": "Solder Resist",
                "DIELTYPE": "3",
            },
        )

        row_index = 3
        for copper_index, copper in enumerate(template.copper_layers):
            row_fields = {
                "LAYERID": str(int(copper.v7_layer_id)),
                "NAME": copper.name,
                "COMPONENTPLACEMENT": str(int(copper.component_placement)),
                "COPTHICK": _format_mil_value(copper.copper_thickness_mils),
            }
            if copper.copper_orientation is not None:
                row_fields["COPPERORIENTATION"] = str(int(copper.copper_orientation))
            add_row(row_index, row_fields)
            row_index += 1

            if copper_index >= len(template.dielectrics_between):
                continue
            dielectric_id = 17039361 + copper_index
            dielectric = template.dielectrics_between[copper_index]
            row_fields = {
                "LAYERID": str(int(dielectric_id)),
                "NAME": dielectric.name,
                "DIELCONST": format(float(dielectric.dielectric_constant), ".3f"),
                "DIELHEIGHT": _format_mil_value(dielectric.thickness_mils),
                "DIELMATERIAL": dielectric.material,
                "DIELTYPE": str(int(dielectric.dielectric_type)),
            }
            if dielectric.loss_tangent is not None:
                row_fields["DIELLOSSTANGENT"] = format(
                    float(dielectric.loss_tangent), ".3f"
                )
            add_row(row_index, row_fields)
            row_index += 1

        add_row(
            row_index,
            {
                "LAYERID": "16973835",
                "NAME": "Bottom Solder",
                "COVERLAY_EXPANSION": "0mil",
                "DIELCONST": "3.500",
                "DIELHEIGHT": "0.4mil",
                "DIELMATERIAL": "Solder Resist",
                "DIELTYPE": "3",
            },
        )
        row_index += 1
        add_row(row_index, {"LAYERID": "16973831", "NAME": "Bottom Overlay"})
        row_index += 1
        add_row(row_index, {"LAYERID": "16973833", "NAME": "Bottom Paste"})

        entries: list[str] = []
        for index, row in enumerate(rows):
            for suffix, value in row.items():
                entries.append(f"V9_STACK_LAYER{index}_{suffix}={value}")
        return tuple(entries)
