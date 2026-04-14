from __future__ import annotations

import html
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from altium_monkey import AltiumDesign, PcbLayer, PcbSvgRenderOptions
from altium_monkey.altium_resolved_layer_stack import (
    ResolvedLayerStack,
    resolved_layer_stack_from_pcbdoc,
)


SCALE_FACTOR = 10.0
MIN_ROUTING_LENGTH_MILS = 10.0

ROUTING_GREY = "#B8B8B8"
PAD_VIA_BLACK = "#000000"
HIGHLIGHT_RED = "#D00000"
DRILL_KNOCKOUT = "#FFFFFF"
BOARD_OUTLINE_COLOR = "#000000"

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
PROJECT_DIR = EXAMPLES_DIR / "assets" / "projects" / "rt_super_c1"
PROJECT_FILE = PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SVG_DIR = OUTPUT_DIR / "svgs"
OUTPUT_HTML_DIR = OUTPUT_DIR / "html"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcbdoc_netclass_svg_manifest.json"


@dataclass(frozen=True)
class NetClassView:
    name: str
    nets: tuple[str, ...]
    layers: tuple[PcbLayer, ...]


def _examples_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _safe_filename(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return token.strip("_").lower() or "item"


def _display_name_for_layer(layer: PcbLayer, stack: ResolvedLayerStack) -> str:
    resolved = stack.layer_by_legacy_id(layer.value)
    if resolved is not None:
        return resolved.display_name
    return layer.name.replace("_", " ").title()


def _net_name_by_index(pcbdoc: object) -> dict[int, str]:
    result: dict[int, str] = {}
    for index, net in enumerate(getattr(pcbdoc, "nets", []) or []):
        name = str(getattr(net, "name", "") or "").strip()
        if name:
            result[index] = name
    return result


def _net_class_views(
    pcbdoc: object,
    net_name_by_index: dict[int, str],
) -> list[NetClassView]:
    views: list[NetClassView] = []
    for net_class in getattr(pcbdoc, "net_classes", []) or []:
        try:
            class_kind = int(getattr(net_class, "kind", 0))
        except (TypeError, ValueError):
            class_kind = 0
        if class_kind != 0:
            continue

        class_name = str(getattr(net_class, "name", "") or "").strip()
        members = tuple(
            member
            for member in (
                str(raw_member or "").strip()
                for raw_member in getattr(net_class, "members", []) or []
            )
            if member
        )
        if not class_name or not members:
            continue

        layers = _routed_copper_layers_for_netclass(
            pcbdoc,
            set(members),
            net_name_by_index,
        )
        if layers:
            views.append(NetClassView(class_name, members, tuple(layers)))

    return sorted(views, key=lambda view: view.name.upper())


def _routed_copper_layers_for_netclass(
    pcbdoc: object,
    class_nets: set[str],
    net_name_by_index: dict[int, str],
) -> list[PcbLayer]:
    layers: set[PcbLayer] = set()

    for track in getattr(pcbdoc, "tracks", []) or []:
        layer = _copper_layer_from_primitive(track)
        if layer is None:
            continue
        if not _primitive_net_is_in_class(track, class_nets, net_name_by_index):
            continue
        if _track_length_mils(track) >= MIN_ROUTING_LENGTH_MILS:
            layers.add(layer)

    for arc in getattr(pcbdoc, "arcs", []) or []:
        layer = _copper_layer_from_primitive(arc)
        if layer is None:
            continue
        if not _primitive_net_is_in_class(arc, class_nets, net_name_by_index):
            continue
        if _arc_length_mils(arc) >= MIN_ROUTING_LENGTH_MILS:
            layers.add(layer)

    return sorted(layers, key=lambda layer: layer.value)


def _copper_layer_from_primitive(primitive: object) -> PcbLayer | None:
    try:
        layer = PcbLayer(int(getattr(primitive, "layer")))
    except (TypeError, ValueError):
        return None
    return layer if layer.is_copper() else None


def _primitive_net_is_in_class(
    primitive: object,
    class_nets: set[str],
    net_name_by_index: dict[int, str],
) -> bool:
    net_index_raw = getattr(primitive, "net_index", None)
    try:
        net_index = int(net_index_raw)
    except (TypeError, ValueError):
        return False
    return net_name_by_index.get(net_index) in class_nets


def _track_length_mils(track: object) -> float:
    start_x = float(getattr(track, "start_x_mils", 0.0) or 0.0)
    start_y = float(getattr(track, "start_y_mils", 0.0) or 0.0)
    end_x = float(getattr(track, "end_x_mils", 0.0) or 0.0)
    end_y = float(getattr(track, "end_y_mils", 0.0) or 0.0)
    return math.hypot(end_x - start_x, end_y - start_y)


def _arc_length_mils(arc: object) -> float:
    radius_mils = float(getattr(arc, "radius_mils", 0.0) or 0.0)
    if radius_mils <= 0.0:
        return 0.0
    start_angle = float(getattr(arc, "start_angle", 0.0) or 0.0)
    end_angle = float(getattr(arc, "end_angle", 0.0) or 0.0)
    delta_degrees = (end_angle - start_angle) % 360.0
    if math.isclose(delta_degrees, 0.0, abs_tol=1e-9):
        delta_degrees = 360.0
    return abs(radius_mils * math.radians(delta_degrees))


def _copper_layer_colors() -> dict[PcbLayer, str]:
    return {layer: ROUTING_GREY for layer in PcbLayer if layer.is_copper()}


def _render_layer_svg(
    pcbdoc: object,
    layer: PcbLayer,
    net_class_name: str,
    project_parameters: dict[str, str],
) -> str:
    options = PcbSvgRenderOptions(
        visible_layers={layer},
        svg_display_scale=SCALE_FACTOR,
        layer_colors=_copper_layer_colors(),
        polygon_overlay_color=ROUTING_GREY,
        board_outline_color=BOARD_OUTLINE_COLOR,
        board_cutout_color=BOARD_OUTLINE_COLOR,
        drill_hole_mode="knockout",
    )
    layer_svgs = pcbdoc.to_layer_svgs(
        options=options,
        project_parameters=project_parameters,
    )
    layer_svg = layer_svgs[layer.to_json_name()]
    return _style_netclass_svg(layer_svg, net_class_name)


def _style_netclass_svg(svg: str, net_class_name: str) -> str:
    lines: list[str] = []
    for line in svg.splitlines():
        primitive = _svg_attr(line, "data-primitive")
        if primitive is None:
            lines.append(line)
            continue

        styled = line
        if primitive in {"track", "arc"}:
            color = (
                HIGHLIGHT_RED
                if _svg_element_has_netclass(line, net_class_name)
                else ROUTING_GREY
            )
            styled = _set_existing_svg_color_attrs(styled, stroke=color)
        elif primitive in {"fill", "region", "shapebased-region", "polygon-outline"}:
            styled = _set_existing_svg_color_attrs(
                styled,
                stroke=ROUTING_GREY,
                fill=ROUTING_GREY,
            )
        elif primitive == "pad":
            styled = _set_existing_svg_color_attrs(
                styled,
                stroke=PAD_VIA_BLACK,
                fill=PAD_VIA_BLACK,
            )
        elif primitive == "via":
            styled = _set_existing_svg_color_attrs(
                styled,
                stroke=PAD_VIA_BLACK,
                fill=PAD_VIA_BLACK,
            )
        elif primitive == "via-hole":
            color = (
                HIGHLIGHT_RED
                if _svg_element_has_netclass(line, net_class_name)
                else DRILL_KNOCKOUT
            )
            styled = _set_existing_svg_color_attrs(styled, stroke=color, fill=color)
        elif primitive == "pad-hole":
            styled = _set_existing_svg_color_attrs(
                styled,
                stroke=DRILL_KNOCKOUT,
                fill=DRILL_KNOCKOUT,
            )
        lines.append(styled)
    return _apply_review_draw_order("\n".join(lines))


def _apply_review_draw_order(svg: str) -> str:
    """
    Draw routing first, then copper areas/pads/vias above it for review clarity.
    """
    result: list[str] = []
    layer_group_start: str | None = None
    layer_group_body: list[str] = []

    for line in svg.splitlines():
        if layer_group_start is None:
            if _is_copper_layer_group_start(line):
                layer_group_start = line
                layer_group_body = []
            else:
                result.append(line)
            continue

        if line.strip() == "</g>":
            result.append(layer_group_start)
            result.extend(_sort_review_primitives(layer_group_body))
            result.append(line)
            layer_group_start = None
            layer_group_body = []
            continue

        layer_group_body.append(line)

    if layer_group_start is not None:
        result.append(layer_group_start)
        result.extend(layer_group_body)

    return "\n".join(result)


def _is_copper_layer_group_start(line: str) -> bool:
    layer_id = _svg_attr(line, "data-layer-id")
    if layer_id is None:
        return False
    try:
        layer = PcbLayer(int(layer_id))
    except (TypeError, ValueError):
        return False
    return layer.is_copper()


def _sort_review_primitives(lines: list[str]) -> list[str]:
    indexed = list(enumerate(lines))
    indexed.sort(key=lambda item: (_review_draw_priority(item[1]), item[0]))
    return [line for _index, line in indexed]


def _review_draw_priority(line: str) -> int:
    primitive = _svg_attr(line, "data-primitive")
    if primitive in {"track", "arc"}:
        return 10
    if primitive in {"fill", "region", "shapebased-region", "polygon-outline"}:
        return 20
    if primitive in {"pad", "via"}:
        return 30
    if primitive in {"pad-hole", "via-hole"}:
        return 40
    return 50


def _svg_attr(svg_element: str, name: str) -> str | None:
    match = re.search(rf'\b{re.escape(name)}="([^"]*)"', svg_element)
    if match is None:
        return None
    return html.unescape(match.group(1))


def _svg_element_has_netclass(svg_element: str, net_class_name: str) -> bool:
    class_values: set[str] = set()
    for attr_name in ("data-net-classes", "data-net-class"):
        value = _svg_attr(svg_element, attr_name)
        if not value:
            continue
        class_values.update(item.strip() for item in value.split(";") if item.strip())
    return net_class_name in class_values


def _replace_svg_attr(svg_element: str, name: str, value: str) -> str:
    escaped = html.escape(value, quote=True)
    return re.sub(
        rf'\b{re.escape(name)}="[^"]*"',
        f'{name}="{escaped}"',
        svg_element,
    )


def _set_existing_svg_color_attrs(
    svg_element: str,
    *,
    stroke: str | None = None,
    fill: str | None = None,
) -> str:
    result = svg_element
    if stroke is not None and _svg_attr(result, "stroke") is not None:
        result = _replace_svg_attr(result, "stroke", stroke)
    if fill is not None and _svg_attr(result, "fill") is not None:
        result = _replace_svg_attr(result, "fill", fill)
    if _svg_attr(result, "data-color") is not None:
        data_color = fill or stroke
        if data_color is not None:
            result = _replace_svg_attr(result, "data-color", data_color)
    return result


def _layer_svg_filename(net_class_name: str, layer: PcbLayer) -> str:
    return (
        f"{_safe_filename(net_class_name)}__"
        f"layer_{layer.value:02d}_{_safe_filename(layer.to_json_name())}.svg"
    )


def _write_svg_file(
    net_class_name: str,
    layer: PcbLayer,
    svg: str,
) -> Path:
    output_path = (
        OUTPUT_SVG_DIR
        / _safe_filename(net_class_name)
        / _layer_svg_filename(net_class_name, layer)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")
    return output_path


def _write_netclass_html(
    view: NetClassView,
    layer_outputs: list[dict[str, object]],
) -> Path:
    output_path = OUTPUT_HTML_DIR / f"{_safe_filename(view.name)}.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    layer_sections: list[str] = []
    for entry in layer_outputs:
        svg_path = SAMPLE_DIR / str(entry["svg"])
        svg_text = svg_path.read_text(encoding="utf-8")
        layer_sections.append(
            "\n".join(
                [
                    '<section class="layer-row">',
                    f"  <h2>{html.escape(str(entry['display_name']))}</h2>",
                    '  <div class="svg-frame">',
                    svg_text,
                    "  </div>",
                    "</section>",
                ]
            )
        )

    nets = ", ".join(html.escape(net) for net in view.nets)
    document = "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '  <meta charset="utf-8">',
            (f"  <title>{html.escape(view.name)} net-class PCB SVG review</title>"),
            "  <style>",
            "    body { font-family: Arial, sans-serif; margin: 24px; background: #f7f7f4; color: #202020; }",
            "    h1 { margin: 0 0 8px; font-size: 28px; }",
            "    .summary { margin: 0 0 24px; color: #505050; }",
            "    .legend { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }",
            "    .legend span { display: inline-flex; align-items: center; gap: 6px; }",
            "    .swatch { width: 20px; height: 12px; border: 1px solid #404040; display: inline-block; }",
            "    .layer-row { margin: 0 0 28px; padding: 16px; background: #ffffff; border: 1px solid #d8d8d0; }",
            "    .layer-row h2 { margin: 0 0 12px; font-size: 18px; }",
            "    .svg-frame { overflow: auto; border: 1px solid #e4e4de; padding: 12px; background: #ffffff; }",
            "    svg { display: block; max-width: none; }",
            "  </style>",
            "</head>",
            "<body>",
            f"  <h1>{html.escape(view.name)}</h1>",
            f'  <p class="summary">Members: {nets}</p>',
            '  <div class="legend">',
            f'    <span><i class="swatch" style="background:{HIGHLIGHT_RED}"></i>highlighted routing / via drills</span>',
            f'    <span><i class="swatch" style="background:{ROUTING_GREY}"></i>other routed copper and pours</span>',
            f'    <span><i class="swatch" style="background:{PAD_VIA_BLACK}"></i>pads and via annular rings</span>',
            "  </div>",
            *layer_sections,
            "</body>",
            "</html>",
            "",
        ]
    )
    output_path.write_text(document, encoding="utf-8")
    return output_path


def _write_manifest(
    *,
    pcbdoc_path: Path,
    views: list[dict[str, object]],
    project_parameters: dict[str, str],
) -> None:
    manifest = {
        "project": _examples_relative(PROJECT_FILE),
        "pcbdoc": _examples_relative(pcbdoc_path),
        "scale_factor": SCALE_FACTOR,
        "minimum_routing_length_mils": MIN_ROUTING_LENGTH_MILS,
        "project_parameters": project_parameters,
        "render_style": {
            "routing_grey": ROUTING_GREY,
            "pad_via_black": PAD_VIA_BLACK,
            "highlight_red": HIGHLIGHT_RED,
            "drill_knockout": DRILL_KNOCKOUT,
            "board_outline_color": BOARD_OUTLINE_COLOR,
        },
        "net_classes": views,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    pcbdoc = design.load_pcbdoc()
    stack = resolved_layer_stack_from_pcbdoc(pcbdoc)
    project_parameters = design.get_pcb_project_parameters()

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_SVG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML_DIR.mkdir(parents=True, exist_ok=True)

    net_name_by_index = _net_name_by_index(pcbdoc)
    netclass_views = _net_class_views(pcbdoc, net_name_by_index)
    manifest_views: list[dict[str, object]] = []

    for view in netclass_views:
        layer_outputs: list[dict[str, object]] = []
        for layer in view.layers:
            layer_svg = _render_layer_svg(pcbdoc, layer, view.name, project_parameters)
            output_path = _write_svg_file(view.name, layer, layer_svg)
            layer_outputs.append(
                {
                    "key": layer.to_json_name(),
                    "display_name": _display_name_for_layer(layer, stack),
                    "legacy_id": layer.value,
                    "svg": _sample_relative(output_path),
                    "byte_count": output_path.stat().st_size,
                }
            )

        html_path = _write_netclass_html(view, layer_outputs)
        manifest_views.append(
            {
                "name": view.name,
                "members": list(view.nets),
                "layers": layer_outputs,
                "html": _sample_relative(html_path),
            }
        )

    _write_manifest(
        pcbdoc_path=Path(pcbdoc.filepath or ""),
        views=manifest_views,
        project_parameters=project_parameters,
    )

    print(f"Loaded project: {_examples_relative(PROJECT_FILE)}")
    print(f"Loaded PCB: {Path(pcbdoc.filepath or '').name}")
    print(f"SVG display scale factor: {SCALE_FACTOR:g}")
    print(f"Minimum routed-copper layer threshold: {MIN_ROUTING_LENGTH_MILS:g} mil")
    print(f"Net classes rendered: {len(manifest_views)}")
    for view in manifest_views:
        layer_names = ", ".join(str(layer["display_name"]) for layer in view["layers"])
        print(f"  {view['name']}: {layer_names}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
