#!/usr/bin/env python3
"""Helper for the altium-schematic Claude Code skill.

Reads Altium projects and schematics via altium-monkey and emits compact JSON
that Claude can selectively load into context. Each subcommand returns the
smallest payload that answers a typical question.

Run with:
    uv run --quiet python .claude/skills/altium-schematic/read_schematic.py <subcommand> ...

Subcommands:
    summary       Project overview: sheets, counts, variants, top-level nets
    components    List/filter components (by sheet, type, designator, value)
    nets          List nets, or fetch full detail (terminals) for one net
    connections   What is connected to a designator or designator+pin
    bom           Bill of materials, optionally per variant
    sheet         Inspect a single .SchDoc file (no project required)
    raw           Escape hatch: dump the full design or netlist JSON

All outputs are JSON on stdout. Errors go to stderr with exit code 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _die(msg: str) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


def _load_design(prjpcb: Path):
    from altium_monkey import AltiumDesign

    if not prjpcb.exists():
        _die(f"PrjPcb not found: {prjpcb}")
    if prjpcb.suffix.lower() != ".prjpcb":
        _die(f"Expected a .PrjPcb file, got: {prjpcb.name}")
    return AltiumDesign.from_prjpcb(prjpcb)


def _emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def cmd_summary(args: argparse.Namespace) -> None:
    design = _load_design(args.prjpcb)
    project = design.project
    netlist = design.to_netlist()
    dj = design.to_json()

    sheets = [
        {
            "filename": s.get("filename"),
            "title": s.get("title"),
            "is_top_level": next(
                (
                    d.get("is_top_level")
                    for d in dj.get("schematic_hierarchy", {}).get("documents", [])
                    if d.get("filename") == s.get("filename")
                ),
                None,
            ),
        }
        for s in dj.get("sheets", [])
    ]

    component_types: dict[str, int] = {}
    for c in dj.get("components", []):
        t = c.get("classification", {}).get("type") or "unknown"
        component_types[t] = component_types.get(t, 0) + 1

    power_nets = sorted(
        {
            n["name"]
            for n in netlist.to_json().get("nets", [])
            if n.get("name")
            and not n.get("auto_named")
            and (
                any(
                    t.get("pin_type") == "POWER" for t in n.get("terminals", [])
                )
                or any(p in n["name"].upper() for p in ("GND", "VSS"))
            )
        }
    )

    payload = {
        "project_file": str(args.prjpcb.name),
        "current_variant": project.get_current_variant() if project else None,
        "variants": design.get_variants(),
        "sheets": sheets,
        "component_count": len(dj.get("components", [])),
        "net_count": len(netlist.nets),
        "component_type_counts": component_types,
        "power_and_ground_nets": sorted(set(power_nets)),
        "pcb_documents": [p.name for p in design.get_pcbdoc_paths()],
    }
    _emit(payload)


def cmd_components(args: argparse.Namespace) -> None:
    design = _load_design(args.prjpcb)
    dj = design.to_json()
    components = dj.get("components", [])

    if args.designator:
        components = [c for c in components if c.get("designator") == args.designator]
    if args.sheet:
        components = [
            c
            for c in components
            if c.get("hierarchy", {}).get("sheet", "").lower() == args.sheet.lower()
            or Path(c.get("hierarchy", {}).get("sheet", "")).name.lower()
            == args.sheet.lower()
        ]
    if args.type:
        components = [
            c
            for c in components
            if (c.get("classification", {}).get("type") or "").lower()
            == args.type.lower()
        ]
    if args.value_contains:
        needle = args.value_contains.lower()
        components = [c for c in components if needle in (c.get("value") or "").lower()]

    if args.brief:
        components = [
            {
                "designator": c.get("designator"),
                "value": c.get("value"),
                "footprint": c.get("footprint"),
                "type": c.get("classification", {}).get("type"),
                "sheet": c.get("hierarchy", {}).get("sheet"),
                "pin_count": c.get("classification", {}).get("pin_count"),
            }
            for c in components
        ]

    _emit({"count": len(components), "components": components})


def cmd_nets(args: argparse.Namespace) -> None:
    design = _load_design(args.prjpcb)
    netlist_json = design.to_netlist().to_json()
    nets = netlist_json.get("nets", [])

    if args.name:
        target = args.name.upper()
        nets = [n for n in nets if (n.get("name") or "").upper() == target]
        if not nets:
            _die(f"No net named: {args.name}")
        n = nets[0]
        _emit(
            {
                "name": n.get("name"),
                "auto_named": n.get("auto_named"),
                "source_sheets": n.get("source_sheets"),
                "terminal_count": len(n.get("terminals", [])),
                "terminals": n.get("terminals", []),
            }
        )
        return

    if args.contains:
        needle = args.contains.upper()
        nets = [n for n in nets if needle in (n.get("name") or "").upper()]

    summary = [
        {
            "name": n.get("name"),
            "auto_named": n.get("auto_named"),
            "terminal_count": len(n.get("terminals", [])),
            "source_sheets": n.get("source_sheets"),
        }
        for n in nets
    ]
    _emit({"count": len(summary), "nets": summary})


def cmd_connections(args: argparse.Namespace) -> None:
    design = _load_design(args.prjpcb)
    dj = design.to_json()
    netlist_json = design.to_netlist().to_json()

    comp_to_nets: dict[str, list[str]] = dj.get("indexes", {}).get(
        "component_to_nets", {}
    )
    nets_by_name = {n.get("name"): n for n in netlist_json.get("nets", [])}

    if args.designator not in comp_to_nets:
        present = sorted(comp_to_nets.keys())[:30]
        _die(
            f"Designator {args.designator!r} not found. "
            f"Sample present designators: {present}"
        )

    net_names = comp_to_nets[args.designator]
    component = next(
        (c for c in dj.get("components", []) if c.get("designator") == args.designator),
        None,
    )

    result: dict[str, Any] = {
        "designator": args.designator,
        "value": component.get("value") if component else None,
        "footprint": component.get("footprint") if component else None,
        "type": component.get("classification", {}).get("type") if component else None,
        "sheet": component.get("hierarchy", {}).get("sheet") if component else None,
    }

    pin_view: list[dict[str, Any]] = []
    for net_name in net_names:
        net = nets_by_name.get(net_name)
        if not net:
            continue
        own_terms = [
            t for t in net.get("terminals", []) if t.get("designator") == args.designator
        ]
        other_terms = [
            t for t in net.get("terminals", []) if t.get("designator") != args.designator
        ]
        for own in own_terms:
            if args.pin and str(own.get("pin")) != str(args.pin):
                continue
            pin_view.append(
                {
                    "pin": own.get("pin"),
                    "pin_name": own.get("pin_name"),
                    "pin_type": own.get("pin_type"),
                    "net": net_name,
                    "connected_to": [
                        {
                            "designator": t.get("designator"),
                            "pin": t.get("pin"),
                            "pin_name": t.get("pin_name"),
                            "pin_type": t.get("pin_type"),
                        }
                        for t in other_terms
                    ],
                }
            )

    if args.pin and not pin_view:
        _die(f"Pin {args.pin!r} not found on {args.designator}")

    result["pins"] = pin_view
    result["pin_count"] = len(pin_view)
    _emit(result)


def cmd_bom(args: argparse.Namespace) -> None:
    design = _load_design(args.prjpcb)
    if args.variant:
        _emit(design.to_bom(variant=args.variant))
    else:
        _emit(design.to_bom())


def cmd_sheet(args: argparse.Namespace) -> None:
    from altium_monkey import AltiumSchDoc

    if not args.schdoc.exists():
        _die(f"SchDoc not found: {args.schdoc}")
    schdoc = AltiumSchDoc(str(args.schdoc))

    components = []
    for c in schdoc.components:
        params = {}
        for p in getattr(c, "parameters", []) or []:
            name = getattr(p, "name", None)
            text = getattr(p, "text", None)
            if name and text not in (None, ""):
                params[str(name)] = str(text)
        components.append(
            {
                "lib_reference": getattr(c, "lib_reference", None),
                "design_item_id": getattr(c, "design_item_id", None),
                "description": getattr(c, "component_description", None),
                "value": params.get("Value") or params.get("Comment"),
                "manufacturer_part": params.get("MP"),
            }
        )

    net_labels = sorted(
        {getattr(nl, "text", None) for nl in schdoc.net_labels if getattr(nl, "text", None)}
    )
    ports = sorted(
        {getattr(p, "name", None) for p in schdoc.ports if getattr(p, "name", None)}
    )
    sheet_symbols = []
    for s in schdoc.sheet_symbols:
        fn = getattr(s, "file_name", None)
        sn = getattr(s, "sheet_name", None)
        sheet_symbols.append(
            {
                "filename": str(getattr(fn, "text", fn) or "") or None,
                "sheet_name": str(getattr(sn, "text", sn) or "") or None,
            }
        )

    _emit(
        {
            "filename": args.schdoc.name,
            "component_count": len(components),
            "components": components,
            "net_label_count": len(net_labels),
            "net_labels": sorted(set(filter(None, net_labels))),
            "port_count": len(ports),
            "ports": sorted(set(filter(None, ports))),
            "sheet_symbol_count": len(sheet_symbols),
            "sheet_symbols": sheet_symbols,
        }
    )


def cmd_raw(args: argparse.Namespace) -> None:
    design = _load_design(args.prjpcb)
    if args.kind == "design":
        _emit(design.to_json())
    elif args.kind == "netlist":
        _emit(design.to_netlist().to_json())
    else:
        _die(f"Unknown raw kind: {args.kind}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="read_schematic",
        description="altium-monkey-backed schematic reader for the Claude Code skill.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("summary", help="Project overview")
    sp.add_argument("prjpcb", type=Path)
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser("components", help="List/filter components")
    sp.add_argument("prjpcb", type=Path)
    sp.add_argument("--designator")
    sp.add_argument("--sheet", help="Filter by sheet filename")
    sp.add_argument("--type", help="connector, resistor, capacitor, ic, ...")
    sp.add_argument("--value-contains", help="Substring match on Value")
    sp.add_argument(
        "--brief", action="store_true", help="Strip parameters/internals"
    )
    sp.set_defaults(func=cmd_components)

    sp = sub.add_parser("nets", help="List nets or fetch one net's terminals")
    sp.add_argument("prjpcb", type=Path)
    sp.add_argument("--name", help="Exact net name (case-insensitive)")
    sp.add_argument("--contains", help="Substring match on net name")
    sp.set_defaults(func=cmd_nets)

    sp = sub.add_parser(
        "connections", help="What is connected to a designator (or pin)"
    )
    sp.add_argument("prjpcb", type=Path)
    sp.add_argument("--designator", required=True)
    sp.add_argument("--pin")
    sp.set_defaults(func=cmd_connections)

    sp = sub.add_parser("bom", help="Bill of materials")
    sp.add_argument("prjpcb", type=Path)
    sp.add_argument("--variant")
    sp.set_defaults(func=cmd_bom)

    sp = sub.add_parser("sheet", help="Inspect a single .SchDoc")
    sp.add_argument("schdoc", type=Path)
    sp.set_defaults(func=cmd_sheet)

    sp = sub.add_parser("raw", help="Dump full design or netlist JSON")
    sp.add_argument("prjpcb", type=Path)
    sp.add_argument("kind", choices=["design", "netlist"])
    sp.set_defaults(func=cmd_raw)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        _die(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
