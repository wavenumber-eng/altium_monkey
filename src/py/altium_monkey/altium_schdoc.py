"""
Parse `.SchDoc` schematic files into an object model.
"""

from __future__ import annotations

import logging
import math
import re
import struct
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, Callable

from .altium_api_markers import public_api
from .altium_json_apply_helpers import JsonApplyMixin

from . import (
    AltiumSchArc,
    AltiumSchBezier,
    AltiumSchBlanket,
    AltiumSchBus,
    AltiumSchBusEntry,
    AltiumSchCompileMask,
    AltiumSchComponent,
    AltiumSchCrossSheetConnector,
    AltiumSchDesignator,
    AltiumSchEllipse,
    AltiumSchEllipticalArc,
    AltiumSchFileName,
    AltiumSchHarnessConnector,
    AltiumSchHarnessEntry,
    AltiumSchHarnessType,
    AltiumSchImage,
    AltiumSchImplementation,
    AltiumSchImplementationList,
    AltiumSchImplParams,
    AltiumSchMapDefinerList,
    AltiumSchJunction,
    AltiumSchLabel,
    AltiumSchLine,
    AltiumSchNetLabel,
    AltiumSchNote,
    AltiumSchNoErc,
    AltiumSchParameter,
    AltiumSchParameterSet,
    AltiumSchPin,
    AltiumSchPolygon,
    AltiumSchPolyline,
    AltiumSchPort,
    AltiumSchPowerPort,
    AltiumSchRectangle,
    AltiumSchRoundedRectangle,
    AltiumSchSheet,
    AltiumSchSheetEntry,
    AltiumSchSheetName,
    AltiumSchSheetSymbol,
    AltiumSchSignalHarness,
    AltiumSchTemplate,
    AltiumSchTextFrame,
    AltiumSchWire,
)
from .altium_sch_record_factory import create_record_from_record
from .altium_sch_binding import SchematicBindingContext
from .altium_record_types import SchRecordType
from .altium_record_types import CoordPoint
from .altium_object_collection import ObjectCollection, ObjectCollectionView
from .altium_ole import AltiumOleFile
from .altium_sch_json_object_types import (
    SchJsonObjectType,
    sch_json_object_type_from_record,
    sch_record_type_from_json_object_type,
)
from .altium_sch_component_insert_helpers import (
    clone_symbol_children,
    load_or_cache_schlib,
    merge_schlib_fonts,
    remap_font_ids,
)
from .altium_schdoc_info import (
    SchComponentInfo,
    SchCrossSheetConnectorInfo,
    SchHarnessInfo,
    SchNetLabelInfo,
    SchPinInfo,
    SchPortInfo,
    SchPowerPortInfo,
    SchSheetSymbolInfo,
)
from .altium_utilities import as_dynamic as _as_dynamic
from .altium_utilities import get_records_in_section, parse_storage_stream_raw

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_netlist_options import NetlistOptions
    from .altium_sch_geometry_oracle import SchGeometryDocument, SchIrRenderProfile
    from .altium_sch_svg_renderer import SchSvgRenderOptions

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _SchGeometryOwnershipState:
    template_obj: Any | None
    template_idx: int | None
    show_template_graphics: bool
    multipart_design_items: set[str]
    component_content_parts: dict[int, set[int]]
    component_owner_indexes: set[int]
    pin_owner_indexes: set[int]

    def owner_index_of(self, obj: object) -> int | None:
        owner_index = getattr(obj, "owner_index", None)
        if owner_index is None:
            return None
        try:
            return int(owner_index)
        except (TypeError, ValueError):
            return None

    def is_component_owned(self, obj: object) -> bool:
        owner_index = self.owner_index_of(obj)
        return owner_index is not None and owner_index in self.component_owner_indexes

    def is_template_owned(self, obj: object) -> bool:
        owner_index = self.owner_index_of(obj)
        return (
            self.template_idx is not None
            and self.template_idx > 0
            and owner_index == self.template_idx
        )

    def is_pin_owned(self, obj: object) -> bool:
        owner_index = self.owner_index_of(obj)
        return owner_index is not None and owner_index in self.pin_owner_indexes


@dataclass(frozen=True)
class _SchSheetGeometrySetup:
    sheet_width_mils: int
    sheet_height_mils: int
    sheet_unique_id: str
    area_color: int
    use_custom_sheet: bool
    border_on: bool
    margin: int
    reference_zones_on: bool
    reference_zone_style: int
    title_block_on: bool
    x_zones: int
    y_zones: int
    units_per_px: int
    workspace_bottom_units: int
    outer_top_units: int
    outer_right_units: int
    has_explicit_border_on: bool

    @property
    def render_border_rects(self) -> bool:
        return self.border_on and (
            self.has_explicit_border_on
            or not (self.reference_zones_on and self.reference_zone_style == 1)
        )

    @property
    def working_margin(self) -> int:
        return self.margin if (self.reference_zones_on or self.title_block_on) else 0


GEOMETRY_KIND_ORDER = {
    "line": 10,
    "bezier": 20,
    "arc": 30,
    "ellipse": 40,
    "ellipticalarc": 50,
    "bus": 60,
    "busentry": 70,
    "label": 80,
    "netlabel": 90,
    "parameter": 100,
    "parameterset": 105,
    "noerc": 107,
    "blanket": 108,
    "compilemask": 109,
    "polygon": 110,
    "polyline": 120,
    "rectangle": 130,
    "roundrectangle": 140,
    "textframe": 142,
    "note": 143,
    "image": 145,
    "port": 150,
    "power": 160,
    "harnessconnector": 165,
    "harnessentry": 166,
    "harnessconnectortype": 167,
    "pie": 170,
    "sheet": 180,
    "template": 181,
    "sheetentry": 182,
    "sheetfilename": 183,
    "sheetname": 184,
    "sheetsymbol": 185,
    "signalharness": 186,
    "wire": 190,
}


COMPONENT_GRAPHIC_CHILD_TYPES = (
    AltiumSchLine,
    AltiumSchRectangle,
    AltiumSchRoundedRectangle,
    AltiumSchEllipse,
    AltiumSchArc,
    AltiumSchPolyline,
    AltiumSchPolygon,
    AltiumSchBezier,
    AltiumSchTextFrame,
    AltiumSchNoErc,
    AltiumSchParameterSet,
    AltiumSchCompileMask,
    AltiumSchBlanket,
    AltiumSchImage,
    AltiumSchLabel,
    AltiumSchNetLabel,
    AltiumSchPort,
    AltiumSchPowerPort,
    AltiumSchSheetEntry,
    AltiumSchHarnessEntry,
    AltiumSchHarnessConnector,
    AltiumSchSignalHarness,
    AltiumSchSheetName,
    AltiumSchFileName,
    AltiumSchDesignator,
)


# Clean SchDoc API - wrapper classes and helper functions


@public_api
class AltiumSchDoc(JsonApplyMixin):
    """
    Represents a complete .SchDoc schematic file.

    Attributes:
        filepath: Path to SchDoc file
        sheet: SHEET record (root container with font table)
        components: List of placed component instances (AltiumSchComponent with children)
        wires: List of wire connections
        buses: List of bus connections
        net_labels: List of net labels
        power_ports: List of power/ground symbols
        junctions: List of wire junctions
        ports: List of hierarchical ports
        sheet_symbols: List of hierarchical sheet boxes
        graphics: List of sheet-level graphical objects
        labels: List of sheet-level text labels
        embedded_images: Dict of embedded images
        all_objects: List of all parsed objects (preserves file order)
    """

    def __init__(
        self,
        filepath: Path | str | None = None,
        *,
        create_sheet: bool = True,
        debug: bool = False,
    ) -> None:
        """
        Create an AltiumSchDoc.

                Args:
                    filepath: Path to .SchDoc binary file to parse.
                              If None, creates an empty schematic for authoring.
                    create_sheet: If True and no filepath, auto-create default Sheet.
                    debug: Enable debug output.
        """
        self.filepath: Path | None = None
        self.sheet: AltiumSchSheet | None = None

        # Single authoritative object collection. Typed access is exposed via properties below.
        self.objects: ObjectCollection = ObjectCollection()

        # Embedded data
        self.embedded_images: dict[str, bytes] = {}
        self._raw_storage_entries: dict[str, tuple[bytes, bytes]] = {}
        self._schlib_cache: dict[Path, Any] = {}

        # Object definitions for custom power port graphics
        self.object_definitions: dict[str, list[dict]] = {}

        # File-level metadata (preserved for round-trip)
        self._file_unique_id: str | None = None  # UniqueID from FileHeader
        self._file_weight: int | None = (
            None  # Original Weight from FileHeader (for round-trip)
        )
        self._preserve_loaded_index_in_sheet: bool = False

        if filepath is not None:
            # Parse binary file - delegate to from_file logic
            self._load_from_file(Path(filepath), debug=debug)
            self._preserve_loaded_index_in_sheet = True
        elif create_sheet:
            # Empty authoring mode with default sheet
            self._create_default_sheet()

    # -- all_objects aliases objects --
    @property
    def all_objects(self) -> ObjectCollection:
        return self.objects

    @all_objects.setter
    def all_objects(self, value: ObjectCollection) -> None:
        if isinstance(value, ObjectCollection):
            self.objects = value
        else:
            self.objects = ObjectCollection(value)

    # -- Typed convenience properties --

    def _object_view(self, predicate: Callable[[object], bool]) -> ObjectCollectionView:
        """
        Return a live read-only query view over the authoritative object store.
        """
        return ObjectCollectionView(self.objects, predicate)

    @property
    def components(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchComponent))

    @property
    def wires(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchWire)

    @property
    def buses(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBus)

    @property
    def bus_entries(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBusEntry)

    @property
    def arcs(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchArc)

    @property
    def lines(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchLine)

    @property
    def rectangles(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchRectangle)

    @property
    def compile_masks(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchCompileMask)

    @property
    def rounded_rectangles(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchRoundedRectangle)

    @property
    def polygons(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchPolygon)

    @property
    def blankets(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBlanket)

    @property
    def beziers(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBezier)

    @property
    def polylines(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchPolyline)

    @property
    def ellipses(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchEllipse)

    @property
    def elliptical_arcs(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchEllipticalArc)

    @property
    def net_labels(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchNetLabel))

    @property
    def power_ports(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchPowerPort)

    @property
    def cross_sheet_connectors(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchCrossSheetConnector))

    @property
    def junctions(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchJunction))

    @property
    def ports(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchPort))

    @property
    def sheet_symbols(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSheetSymbol))

    @property
    def sheet_entries(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSheetEntry))

    @property
    def sheet_names(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSheetName))

    @property
    def file_names(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchFileName))

    @property
    def labels(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchLabel)

    @property
    def text_strings(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchLabel)

    @property
    def text_frames(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchTextFrame)

    @property
    def notes(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchNote))

    @property
    def no_ercs(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchNoErc)

    @property
    def parameter_sets(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchParameterSet)

    @property
    def differential_pair_directives(self) -> ObjectCollection:
        return self._object_view(
            lambda o: isinstance(o, AltiumSchParameterSet) and o.is_differential_pair()
        )

    @property
    def parameters(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchParameter))

    @property
    def designators(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchDesignator))

    @property
    def images(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchImage))

    @staticmethod
    def _sanitize_embedded_asset_name(name: str, fallback: str) -> str:
        """
        Sanitize embedded asset names for stable filesystem extraction.
        """
        text = str(name or "").strip()
        if not text:
            text = fallback
        text = text.replace("\x00", "").strip()
        text = re.sub(r'[<>:"/\\|?*]+', "_", text)
        text = re.sub(r"\s+", "_", text).strip("._ ")
        return text or fallback

    @staticmethod
    def _embedded_image_extension_from_data(data: bytes) -> str | None:
        """
        Infer the native embedded image file extension from payload bytes.
        """
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return ".png"
        if data[:2] == b"BM":
            return ".bmp"
        if data[:2] == b"\xff\xd8":
            return ".jpg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ".gif"
        if data[:12] == b"RIFF" and data[8:12] == b"WEBP":
            return ".webp"
        head = data[:256].lstrip()
        if head.startswith(b"<svg") or head.startswith(b"<?xml"):
            return ".svg"
        return None

    @staticmethod
    def _embedded_image_output_name(image: AltiumSchImage, index: int) -> str:
        fallback = f"image_{index:03d}"
        original_name = PureWindowsPath(str(image.filename or "")).name
        stem = Path(original_name).stem if original_name else fallback
        stem = AltiumSchDoc._sanitize_embedded_asset_name(stem, fallback)

        data = image.image_data or b""
        extension = AltiumSchDoc._embedded_image_extension_from_data(data)
        if extension is None and original_name:
            original_extension = Path(original_name).suffix.lower()
            if original_extension in {
                ".bmp",
                ".gif",
                ".jpg",
                ".jpeg",
                ".png",
                ".svg",
                ".webp",
            }:
                extension = (
                    ".jpg" if original_extension == ".jpeg" else original_extension
                )
        extension = extension or ".bin"
        return f"{index:03d}__{stem}{extension}"

    def extract_embedded_images(
        self,
        output_dir: Path | str,
        *,
        verbose: bool = False,
    ) -> list[Path]:
        """
        Extract embedded schematic IMAGE payloads to `output_dir`.

        Files are written as `<index:03d>__<source stem>.<detected ext>`.
        The extension is detected from the embedded bytes, not from the IMAGE
        record's source filename. Native Altium commonly stores embedded
        schematic images as BMP payloads even when the original source path was
        a PNG or JPG.

        Linked image records without embedded payload bytes are skipped.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for index, image in enumerate(self.images, start=1):
            if not image.image_data:
                if verbose:
                    log.info(
                        "Skipping image without embedded payload: %s",
                        image.filename or f"image_{index:03d}",
                    )
                continue
            filename = self._embedded_image_output_name(image, index)
            image_path = output_path / filename
            image_path.write_bytes(image.image_data)
            written.append(image_path)
            if verbose:
                log.info("Extracted embedded image: %s", image_path.name)

        return written

    @property
    def harness_connectors(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchHarnessConnector))

    @property
    def harness_entries(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchHarnessEntry))

    @property
    def harness_types(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchHarnessType))

    @property
    def signal_harnesses(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSignalHarness))

    @property
    def graphics(self) -> ObjectCollection:
        """
        All graphical primitives (lines, rectangles, arcs, etc.).
        """
        return self._object_view(
            lambda o: isinstance(
                o,
                (
                    AltiumSchLine,
                    AltiumSchRectangle,
                    AltiumSchRoundedRectangle,
                    AltiumSchEllipse,
                    AltiumSchArc,
                    AltiumSchPolyline,
                    AltiumSchPolygon,
                    AltiumSchBezier,
                ),
            ),
        )

    def _create_default_sheet(self) -> None:
        """
        Create a default Sheet with reasonable defaults.

        Matches Altium File->New pattern:
        - A size sheet (9.5" x 7.5")
        - No title block
        - Reference zones on
        - Standard grids (10 mil snap/visible)
        - Font 1: Times New Roman 10pt
        """
        import random
        import string

        # Create sheet with defaults (AltiumSchSheet has good defaults)
        sheet = AltiumSchSheet()
        sheet.sheet_style = 5  # A size (9.5" x 7.5")
        sheet.title_block_on = False
        sheet.reference_zones_on = True
        sheet.owner_index = 0  # Sheet is root (owned by file header)
        sheet.index_in_sheet = -1

        self.sheet = sheet
        self.all_objects = [sheet]

        # Add default system PARAMETER records (required by Altium)
        self._add_default_parameters()

        # Generate unique ID for file header
        self._file_unique_id = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )

    def _binding_context(self) -> SchematicBindingContext:
        """
        Get the narrow schematic binding context for this document.
        """
        context = getattr(self, "_schematic_binding_context", None)
        if context is None:
            context = SchematicBindingContext(self, kind="schdoc")
            self._schematic_binding_context = context
        return context

    def _bind_schematic_object(self, obj: Any) -> None:
        """
        Bind an object to this document's schematic context.
        """
        bind_hook = getattr(obj, "_bind_to_schematic_context", None)
        if callable(bind_hook):
            bind_hook(self._binding_context())

    def _bind_all_objects_to_context(self) -> None:
        """
        Bind every document object to this document context.
        """
        for obj in self.all_objects:
            self._bind_schematic_object(obj)

    def _sync_embedded_images_from_objects(self) -> None:
        """
        Rebuild the embedded-image storage map from current IMAGE objects.

        Public mutation creates and edits real ``AltiumSchImage`` objects.
        Before save, synchronize the storage-stream payloads from those objects
        so ``add_object(make_sch_embedded_image(...))`` is the canonical public
        write path.
        """
        synced_images: dict[str, bytes] = {}
        for image in self.images:
            if (
                isinstance(image, AltiumSchImage)
                and image.embed_image
                and image.filename
                and image.image_data
            ):
                synced_images.setdefault(image.filename, image.image_data)
        self.embedded_images = synced_images
        self._raw_storage_entries = {
            filename: entry
            for filename, entry in self._raw_storage_entries.items()
            if filename in synced_images
        }

    def _add_default_parameters(self) -> None:
        """
        Add default system PARAMETER records.

        Altium SchDoc files require system parameters like CurrentTime, Author, etc.
        Without these, Altium may freeze or fail to load the file.
        """
        from .altium_record_sch__parameter import AltiumSchParameter

        # System parameters required by Altium SchDoc format
        # These are the parameters found in a standard Altium-created SchDoc file
        default_params = [
            "CurrentTime",
            "CurrentDate",
            "Time",
            "Date",
            "DocumentFullPathAndName",
            "DocumentName",
            "ModifiedDate",
            "ApprovedBy",
            "CheckedBy",
            "Author",
            "CompanyName",
            "DrawnBy",
            "Engineer",
            "Organization",
            "Address1",
            "Address2",
            "Address3",
            "Address4",
            "Title",
            "DocumentNumber",
            "Revision",
            "SheetNumber",
            "SheetTotal",
            "Rule",
            "ImagePath",
            "ProjectName",
            "Application_BuildNumber",
        ]

        for i, name in enumerate(default_params):
            param = AltiumSchParameter()
            param.name = name
            param.text = ""  # Empty value by default
            param.owner_index = 0  # All owned by sheet (at position 0)
            _as_dynamic(param)._use_pascal_case = True  # SchDoc uses PascalCase

            # IndexInSheet: first param omits the persisted field, rest are sequential.
            if i == 0:
                param.index_in_sheet = -2
            else:
                param.index_in_sheet = i

            self.objects.append(param)

    @classmethod
    def _from_schdoc(cls, filepath: Path, debug: bool = False) -> AltiumSchDoc:
        """
        Parse a SchDoc file (convenience method for round-trip testing).

        This is the primary API method for loading SchDoc files.
        Use this method for all file loading operations.

        Args:
            filepath: Path to .SchDoc file
            debug: Enable debug output

        Returns:
            AltiumSchDoc instance with parsed data
        """
        return cls(filepath, debug=debug)

    def _load_from_file(self, filepath: Path, debug: bool = False) -> None:
        """
        Parse a SchDoc file into this instance.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"SchDoc file not found: {filepath}")

        log.info(f"Parsing SchDoc file: {filepath.name}")

        self.filepath = filepath

        # Open OLE file
        ole = AltiumOleFile(filepath)

        # Parse FileHeader stream
        log.info("  Parsing FileHeader stream...")
        records = get_records_in_section(ole, "FileHeader")

        if debug:
            log.info(f"    Found {len(records)} records in FileHeader")

        # First record: File header (preserve UniqueID and Weight for round-trip)
        if records and "HEADER" in records[0]:
            header_text = records[0].get("HEADER", "")
            self._file_unique_id = (
                records[0].get("UniqueID") or records[0].get("UNIQUEID") or "AAAAAAAA"
            )
            # Preserve original Weight for round-trip (Altium may exclude certain records)
            weight_str = records[0].get("Weight") or records[0].get("WEIGHT") or ""
            if weight_str:
                self._file_weight = int(weight_str)
            if debug:
                log.info(f"    Header: {header_text[:60]}...")
                log.info(f"    UniqueID: {self._file_unique_id}")
                log.info(f"    Weight: {self._file_weight}")

        # Parse all records and build object hierarchy
        self._parse_records(records[1:], debug, source_stream="FileHeader")

        # Parse Additional stream if exists (must be a stream, not a storage)
        if ole.exists("Additional") and ole.get_type("Additional") == 2:
            log.info("  Parsing Additional stream...")
            additional_records = get_records_in_section(ole, "Additional")
            if additional_records:
                # Skip header record
                self._parse_records(
                    additional_records[1:], debug, source_stream="Additional"
                )

        # Parse Storage stream for embedded images (must be a stream, not a storage)
        if ole.exists("Storage") and ole.get_type("Storage") == 2:
            log.info("  Parsing Storage stream...")
            self.embedded_images, self._raw_storage_entries = parse_storage_stream_raw(
                ole, debug
            )
            if self.embedded_images:
                log.info(f"    Found {len(self.embedded_images)} embedded images")

        # Parse ObjectDefinitions stream for custom power port/connector graphics
        if ole.exists("ObjectDefinitions") and ole.get_type("ObjectDefinitions") == 2:
            log.info("  Parsing ObjectDefinitions stream...")
            self._parse_object_definitions(ole, debug)

        ole.close()

        # Build component hierarchy (assign children to components based on OwnerIndex)
        self._build_component_hierarchy(debug)

        # Build ParameterSet hierarchy (assign child parameters based on OwnerIndex)
        self._build_parameterset_hierarchy(debug)

        # Build Implementation hierarchy (ImplementationList -> Implementation)
        self._build_implementation_hierarchy(debug)

        # Build harness and sheet symbol hierarchy
        self._build_harness_and_sheet_hierarchy(debug)

        # Link images with embedded data
        self._link_embedded_images(debug)

        # Set parent references for any remaining objects not covered above.
        self._set_all_parent_references(debug)
        self._bind_all_objects_to_context()

        log.info(
            f"  Parsed successfully: {len(self.all_objects)} total objects, "
            f"{len(self.components)} components"
        )

    def _parse_object_definitions(self, ole: Any, debug: bool = False) -> None:
        """
        Parse ObjectDefinitions stream for custom power port/connector graphics.

                The ObjectDefinitions stream contains object-definition headers
                followed by child primitive records (Lines, Polygons, etc.) in
                local coordinates. These define custom graphics for power ports
                that have an ObjectDefinitionId GUID.

                Structure:
                    [0] Header record
                    [1] ObjectDefinition {ObjectDefinitionId=GUID}
                    [2..N] Child primitives (Line, Polygon, etc.)
                    [N+1] Next definition header (if multiple)
        """
        records = get_records_in_section(ole, "ObjectDefinitions")
        if not records:
            return

        # Skip header record, group child primitives by their parent definition
        current_def_id = None
        current_children: list[dict] = []

        for rec in records[1:]:  # Skip header
            record_type = rec.get("RECORD", "")
            if str(record_type) == "129":
                # Save previous definition if any
                if current_def_id and current_children:
                    self.object_definitions[current_def_id] = current_children
                # Start new definition
                current_def_id = rec.get("ObjectDefinitionId", "")
                current_children = []
                if debug:
                    lib_ref = rec.get("LibReference", "")
                    log.info(f"    ObjectDefinition: {lib_ref} ({current_def_id})")
            else:
                # Child primitive record
                current_children.append(rec)

        # Save last definition
        if current_def_id and current_children:
            self.object_definitions[current_def_id] = current_children

        if self.object_definitions:
            log.info(f"    Found {len(self.object_definitions)} object definitions")

    def _parse_records(
        self,
        records: list[dict[str, Any]],
        debug: bool = False,
        source_stream: str = "FileHeader",
    ) -> None:
        """
        Parse records and categorize by type.

        This is a first pass that parses all records. The component
        hierarchy is built in a second pass (_build_component_hierarchy).

        Args:
            records: List of record dictionaries
            debug: Enable debug output
            source_stream: Which stream these records came from ('FileHeader' or 'Additional')
        """
        for _idx, record in enumerate(records):
            try:
                record_type_id = int(record.get("RECORD", -1))
                record_type = SchRecordType(record_type_id)

                # Store record index for hierarchy building
                record["_parse_index"] = len(self.all_objects)

                # Parse based on record type
                if record_type == SchRecordType.SHEET:
                    # SHEET record (root container)
                    sheet = AltiumSchSheet()
                    sheet.parse_from_record(record)
                    _as_dynamic(sheet)._record_index = len(self.all_objects)
                    _as_dynamic(sheet)._raw_record_index = (
                        _idx + 1
                    )  # +1 because we skip header record
                    _as_dynamic(
                        sheet
                    )._source_stream = (
                        source_stream  # Track which stream this came from
                    )
                    self.sheet = sheet
                    self.objects.append(sheet)

                    # Set up font ID translation for deduplication
                    # This maps file font IDs to internal IDs
                    if self.sheet.fonts:
                        self.font_manager.setup_in_translator(self.sheet.fonts)

                elif record_type == SchRecordType.COMPONENT:
                    # Component instance - parse as OOP object
                    component = AltiumSchComponent()
                    component.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(component)._record_index = len(self.all_objects)
                    _as_dynamic(component)._source_stream = source_stream
                    self.objects.append(component)

                elif record_type == SchRecordType.PIN:
                    # PIN instance - parse into AltiumSchPin object
                    pin = AltiumSchPin()
                    pin.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(pin)._record_index = len(self.all_objects)
                    _as_dynamic(pin)._source_stream = source_stream
                    self.objects.append(pin)

                elif record_type == SchRecordType.WIRE:
                    wire = AltiumSchWire()
                    wire.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(wire)._record_index = len(self.all_objects)
                    _as_dynamic(wire)._source_stream = source_stream
                    self.objects.append(wire)

                elif record_type == SchRecordType.BUS:
                    bus = AltiumSchBus()
                    bus.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(bus)._record_index = len(self.all_objects)
                    _as_dynamic(bus)._source_stream = source_stream
                    self.objects.append(bus)

                elif record_type == SchRecordType.IMAGE:
                    image = AltiumSchImage()
                    image.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(image)._record_index = len(self.all_objects)
                    _as_dynamic(image)._source_stream = source_stream
                    # Store raw record for image type classification
                    image._raw_record = record
                    self.objects.append(image)

                else:
                    # Try to create OOP record object
                    obj = create_record_from_record(record)

                    if obj:
                        # Parse using OOP class with font translation support
                        obj.parse_from_record(record, font_manager=self.font_manager)
                        _as_dynamic(obj)._record_index = len(self.all_objects)
                        _as_dynamic(obj)._source_stream = source_stream
                        self.objects.append(obj)

                        # Typed access is provided via properties; no separate lists are maintained.

                    else:
                        # Unknown record type - store as dict
                        if debug:
                            log.info(f"    Unknown record type: {record_type_id}")
                        self.objects.append(record)

            except Exception as e:
                if debug:
                    log.error(f"    Error parsing record: {e}")
                    log.error(f"      Record: {record}")
                # Store as dict on error
                self.objects.append(record)

    def _build_component_hierarchy(self, debug: bool = False) -> None:
        """
        Build component-child hierarchy using OwnerIndex.

        In SchDoc files, objects have an OwnerIndex field that points to
        their parent object's index. Components own their pins, graphics,
        labels, etc.

        This allows extracting complete symbols from schematics.
        """
        if debug:
            log.info("  Building component hierarchy...")

        # Build index lookup: record_index -> component
        component_by_index = {}
        for comp in self.components:
            comp.pins.clear()
            comp.parameters.clear()
            comp.graphics.clear()
            comp.children.clear()
            record_index = getattr(comp, "_record_index", None)
            if record_index is not None:
                component_by_index[record_index] = comp

        # Iterate through all objects and assign children to components
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchComponent):
                continue  # Skip components themselves

            # Get owner index from the object
            owner_index = None
            if isinstance(obj, dict):
                owner_index = int(obj.get("OWNERINDEX", obj.get("OwnerIndex", -1)))
            elif hasattr(obj, "owner_index"):
                owner_index = obj.owner_index

            if owner_index is not None and owner_index >= 0:
                parent_comp = component_by_index.get(owner_index)
                if parent_comp:
                    # Set parent reference for direct owner-linked component children.
                    if hasattr(obj, "parent"):
                        _as_dynamic(obj).parent = parent_comp
                    if obj not in parent_comp.children:
                        parent_comp.children.append(obj)
                    # Add to parent's children
                    if isinstance(obj, AltiumSchPin):
                        # PIN object
                        parent_comp.pins.append(obj)
                    elif isinstance(obj, AltiumSchParameter):
                        parent_comp.parameters.append(obj)
                    elif isinstance(obj, AltiumSchDesignator):
                        # Designator belongs to component - add to parameters for rendering
                        parent_comp.parameters.append(obj)
                    elif isinstance(obj, AltiumSchImplementationList):
                        # Implementation list - add to parameters for footprint lookup
                        # Keep implementation lists attached for footprint lookup.
                        parent_comp.parameters.append(obj)
                    elif isinstance(obj, COMPONENT_GRAPHIC_CHILD_TYPES):
                        # Graphical child records are attached by concrete type, not by
                        # legacy SVG helper presence. This keeps hierarchy building
                        # independent from the renderer implementation.
                        parent_comp.graphics.append(obj)

        if debug:
            for comp in self.components:
                log.info(
                    f"    {comp.lib_reference}: {len(comp.pins)} pins, "
                    f"{len(comp.parameters)} params"
                )

    def _build_parameterset_hierarchy(self, debug: bool = False) -> None:
        """
        Build ParameterSet-child hierarchy using OwnerIndex.

        ParameterSet records can have child Parameter records.
        These children are used for:
        - DifferentialPair detection: parameter with name="DifferentialPair", text="True"
        - Other directive-specific parameters

        Differential-pair detection depends on these child parameters being
        attached to the owning ParameterSet.
        """
        if debug:
            log.info("  Building ParameterSet hierarchy...")

        # Build index lookup: record_index -> ParameterSet
        parameterset_by_index = {}
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchParameterSet):
                rec_idx = getattr(obj, "_record_index", None)
                if rec_idx is not None:
                    parameterset_by_index[rec_idx] = obj

        if not parameterset_by_index:
            if debug:
                log.info("    No ParameterSets found")
            return

        # Iterate through all parameters and assign to ParameterSets
        assigned_count = 0
        for obj in self.all_objects:
            if not isinstance(obj, AltiumSchParameter):
                continue

            owner_index = getattr(obj, "owner_index", None)
            if owner_index is None or owner_index < 0:
                continue

            parent_ps = parameterset_by_index.get(owner_index)
            if parent_ps:
                # Set parent reference
                if hasattr(obj, "parent"):
                    _as_dynamic(obj).parent = parent_ps
                # Add to ParameterSet's parameters list
                parent_ps.parameters.append(obj)
                assigned_count += 1

        if debug:
            log.info(
                f"    Assigned {assigned_count} parameters to {len(parameterset_by_index)} ParameterSets"
            )
            for ps in parameterset_by_index.values():
                if ps.parameters:
                    param_names = [p.name for p in ps.parameters]
                    log.info(f"    ParameterSet '{ps.name}': {param_names}")

    def _build_implementation_hierarchy(self, debug: bool = False) -> None:
        """
        Build ImplementationList-child hierarchy using OwnerIndex.

        ImplementationList records own Implementation records.
        This is needed for SchComponentInfo.footprint to find the current footprint.

        This allows wrapper APIs to resolve the active footprint implementation.
        """
        if debug:
            log.info("  Building Implementation hierarchy...")

        # Build index lookup: record_index -> ImplementationList
        impl_list_by_index = {}
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchImplementationList):
                obj.children.clear()
                rec_idx = getattr(obj, "_record_index", None)
                if rec_idx is not None:
                    impl_list_by_index[rec_idx] = obj

        if not impl_list_by_index:
            if debug:
                log.info("    No ImplementationLists found")
            return

        # Iterate through all implementations and assign to ImplementationLists
        assigned_count = 0
        for obj in self.all_objects:
            if not isinstance(obj, AltiumSchImplementation):
                continue

            owner_index = getattr(obj, "owner_index", None)
            if owner_index is None or owner_index < 0:
                continue

            parent_list = impl_list_by_index.get(owner_index)
            if parent_list:
                # Set parent reference
                if hasattr(obj, "parent"):
                    _as_dynamic(obj).parent = parent_list
                # Add to ImplementationList's children
                parent_list.children.append(obj)
                assigned_count += 1

        if debug:
            log.info(
                f"    Assigned {assigned_count} implementations to {len(impl_list_by_index)} ImplementationLists"
            )

    @staticmethod
    def _set_runtime_parent(child: Any, parent: Any) -> None:
        """
        Attach a resolved runtime parent when the child exposes a `parent` attribute.
        """
        if hasattr(child, "parent"):
            _as_dynamic(child).parent = parent

    def _collect_harness_objects(self) -> list[Any]:
        """
        Return Additional-stream harness objects in file order.
        """
        return [
            obj
            for obj in self.all_objects
            if isinstance(
                obj,
                (
                    AltiumSchHarnessConnector,
                    AltiumSchHarnessEntry,
                    AltiumSchHarnessType,
                    AltiumSchSignalHarness,
                ),
            )
        ]

    @staticmethod
    def _build_harness_connector_index(
        harness_objects: list[Any],
    ) -> dict[int, AltiumSchHarnessConnector]:
        """
        Map Additional-stream indices to harness connectors.
        """
        return {
            index: obj
            for index, obj in enumerate(harness_objects)
            if isinstance(obj, AltiumSchHarnessConnector)
        }

    @staticmethod
    def _resolve_harness_parent(
        child: Any,
        current_connector: AltiumSchHarnessConnector | None,
        connector_by_index: dict[int, AltiumSchHarnessConnector],
    ) -> AltiumSchHarnessConnector | None:
        """
        Resolve the parent connector for a harness child.
        """
        owner_index = getattr(child, "owner_index", 0)
        uses_file_order = getattr(child, "owner_index_additional_list", False)
        if uses_file_order and owner_index == 0:
            return current_connector
        if owner_index > 0:
            return connector_by_index.get(owner_index)
        return None

    def _attach_harness_entry(
        self,
        entry: AltiumSchHarnessEntry,
        current_connector: AltiumSchHarnessConnector | None,
        connector_by_index: dict[int, AltiumSchHarnessConnector],
    ) -> None:
        """
        Attach a harness entry to its resolved harness connector.
        """
        parent = self._resolve_harness_parent(
            entry, current_connector, connector_by_index
        )
        if parent is None:
            return
        self._set_runtime_parent(entry, parent)
        parent.entries.append(entry)

    def _attach_harness_type(
        self,
        harness_type: AltiumSchHarnessType,
        current_connector: AltiumSchHarnessConnector | None,
        connector_by_index: dict[int, AltiumSchHarnessConnector],
    ) -> None:
        """
        Attach a harness type label to its resolved harness connector.
        """
        parent = self._resolve_harness_parent(
            harness_type, current_connector, connector_by_index
        )
        if parent is None:
            return
        self._set_runtime_parent(harness_type, parent)
        _as_dynamic(parent).type_label = harness_type
        parent.children.append(harness_type)

    def _build_harness_hierarchy(self) -> None:
        """
        Resolve harness connector ownership from the Additional stream.
        """
        harness_objects = self._collect_harness_objects()
        connector_by_index = self._build_harness_connector_index(harness_objects)
        current_connector: AltiumSchHarnessConnector | None = None
        for obj in harness_objects:
            if isinstance(obj, AltiumSchHarnessConnector):
                current_connector = obj
                continue
            if isinstance(obj, AltiumSchHarnessEntry):
                self._attach_harness_entry(obj, current_connector, connector_by_index)
                continue
            if isinstance(obj, AltiumSchHarnessType):
                self._attach_harness_type(obj, current_connector, connector_by_index)

    def _build_sheet_symbol_index(self) -> dict[int, AltiumSchSheetSymbol]:
        """
        Map persisted record indices to sheet symbols.
        """
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol] = {}
        for symbol in self.sheet_symbols:
            record_index = getattr(symbol, "_record_index", None)
            if record_index is not None:
                sheet_symbol_by_index[record_index] = symbol
        return sheet_symbol_by_index

    @staticmethod
    def _resolve_indexed_parent(
        owner_index: int,
        parent_by_index: dict[int, Any],
    ) -> Any | None:
        """
        Resolve an explicit owner index to its parent object.
        """
        if owner_index < 0:
            return None
        return parent_by_index.get(owner_index)

    def _attach_sheet_entries(
        self,
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol],
    ) -> None:
        """
        Attach sheet entries to their parent sheet symbols.
        """
        for entry in self.sheet_entries:
            parent = self._resolve_indexed_parent(
                getattr(entry, "owner_index", -1),
                sheet_symbol_by_index,
            )
            if parent is None:
                continue
            self._set_runtime_parent(entry, parent)
            parent.entries.append(entry)

    def _attach_sheet_names(
        self,
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol],
    ) -> None:
        """
        Attach sheet-name records to their parent sheet symbols.
        """
        for name in self.sheet_names:
            parent = self._resolve_indexed_parent(
                getattr(name, "owner_index", -1),
                sheet_symbol_by_index,
            )
            if parent is None:
                continue
            self._set_runtime_parent(name, parent)
            parent.children.append(name)
            parent.sheet_name = name

    def _attach_sheet_file_names(
        self,
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol],
    ) -> None:
        """
        Attach file-name records to their parent sheet symbols.
        """
        for file_name in self.file_names:
            parent = self._resolve_indexed_parent(
                getattr(file_name, "owner_index", -1),
                sheet_symbol_by_index,
            )
            if parent is None:
                continue
            self._set_runtime_parent(file_name, parent)
            parent.children.append(file_name)
            parent.file_name = file_name

    def _build_sheet_symbol_hierarchy(self) -> None:
        """
        Resolve sheet-symbol owned records from persisted OwnerIndex links.
        """
        sheet_symbol_by_index = self._build_sheet_symbol_index()
        self._attach_sheet_entries(sheet_symbol_by_index)
        self._attach_sheet_names(sheet_symbol_by_index)
        self._attach_sheet_file_names(sheet_symbol_by_index)
        for symbol in self.sheet_symbols:
            children: list[Any] = list(symbol.entries)
            if symbol.sheet_name is not None:
                children.append(symbol.sheet_name)
            if symbol.file_name is not None:
                children.append(symbol.file_name)
            symbol.children = children

    def _build_harness_and_sheet_hierarchy(self, debug: bool = False) -> None:
        """
        Build parent-child relationships for harness connectors and sheet symbols.

        Harness connectors own harness entries and harness types.
        Sheet symbols own sheet entries.

        OWNERSHIP MODELS:
        1. Harness objects (in Additional stream):
           - Use file-order hierarchy with OwnerIndexAdditionalList=T flag
           - Entries/types with no OwnerIndex belong to immediately preceding connector
           - Entries/types with OwnerIndex > 0 reference a connector by its Additional stream index

        2. Sheet symbols (in FileHeader stream):
           - Use explicit OwnerIndex referencing parent's record index
        """
        if debug:
            log.info("  Building harness and sheet symbol hierarchy...")

        self._build_harness_hierarchy()
        self._build_sheet_symbol_hierarchy()

        if debug:
            for connector in self.harness_connectors:
                log.info(f"    HarnessConnector: {len(connector.entries)} entries")
            for symbol in self.sheet_symbols:
                log.info(f"    SheetSymbol: {len(symbol.entries)} entries")

    def _link_embedded_images(self, debug: bool = False) -> None:
        """
        Link IMAGE records with their embedded image data.
        """
        if not self.embedded_images or not self.images:
            return

        if debug:
            log.info("  Linking embedded images...")

        for img in self.images:
            if img.filename in self.embedded_images:
                img.image_data = self.embedded_images[img.filename]
                img.detect_format()
            elif img.embedded:
                # Try to match by filename only (strip path)
                img_basename = Path(img.filename).name
                for full_path, data in self.embedded_images.items():
                    if Path(full_path).name == img_basename:
                        img.image_data = data
                        img.detect_format()
                        break

        if debug:
            linked = sum(1 for img in self.images if img.image_data)
            log.info(f"    Linked {linked}/{len(self.images)} images")

    def _set_all_parent_references(self, debug: bool = False) -> None:
        """
        Set parent references for any remaining objects based on owner_index.

        This is a comprehensive pass that handles parent relationships not covered
        by the specific hierarchy assembly passes (Components, SheetSymbols, Harness).

        Handles:
        - Template -> children (polylines, labels, etc.)
        - PIN -> children (parameters)
        - ParameterSet -> children (parameters)
        - ImplementationList -> children (implementations)
        - Any other owner_index relationships

        This method is called after the specific hierarchy assembly passes, so it only
        sets parent for objects that don't already have one.
        """
        if debug:
            log.info("  Setting all parent references...")

        # Build full index lookup: _record_index -> object
        obj_by_index: dict[int, object] = {}
        for obj in self.all_objects:
            rec_idx = getattr(obj, "_record_index", None)
            if rec_idx is not None:
                obj_by_index[rec_idx] = obj

        # Set parent for any object with owner_index > 0 that doesn't have parent yet
        set_count = 0
        for obj in self.all_objects:
            # Skip if parent already set by a specific hierarchy assembly pass.
            if getattr(obj, "parent", None) is not None:
                continue

            owner_idx = getattr(obj, "owner_index", 0)
            if owner_idx > 0:
                parent = obj_by_index.get(owner_idx)
                if parent is not None and hasattr(obj, "parent"):
                    obj.parent = parent
                    parent_children = getattr(parent, "children", None)
                    if isinstance(parent_children, list) and obj not in parent_children:
                        parent_children.append(obj)
                    set_count += 1

        if debug:
            log.info(f"    Set {set_count} additional parent references")

    def _get_index_in_sheet(self, obj: Any) -> int | None:
        """
        Get IndexInSheet from an object's raw record.
        """
        raw = getattr(obj, "_raw_record", None)
        if raw:
            idx = raw.get("IndexInSheet", raw.get("INDEXINSHEET"))
            if idx is not None:
                return int(idx)
        return None

    def _set_index_in_sheet(self, obj: Any, value: int) -> None:
        """
        Set IndexInSheet in an object (both OOP attribute and raw record).
        """
        # Update OOP attribute if present
        if hasattr(obj, "index_in_sheet"):
            obj.index_in_sheet = value

        # Update raw record if present
        raw = getattr(obj, "_raw_record", None)
        if raw:
            if "IndexInSheet" in raw:
                raw["IndexInSheet"] = str(value)
            elif "INDEXINSHEET" in raw:
                raw["INDEXINSHEET"] = str(value)

    def _recalculate_indices(self) -> None:
        """
        Recalculate OwnerIndex and IndexInSheet for all objects after modifications.

        This uses direct parent references when available instead of searching
        by stale record indices. That keeps indices consistent regardless of
        object order in all_objects.

        OwnerIndex is written as the warehouse position of the parent container,
        and IndexInSheet is written as the position in the parent's child list.

        Both are recalculated from scratch based on current object tree structure.
        This is called automatically before save operations.
        """
        warehouse_position = self._warehouse_positions()
        children_by_owner = self._recalculate_owner_indices(warehouse_position)
        self._recalculate_index_in_sheet(children_by_owner)
        self._update_record_indices(warehouse_position)

        log.info(f"  Recalculated indices for {len(self.all_objects)} objects")

    def _warehouse_positions(self) -> dict[int, int]:
        """
        Map object identity to its current warehouse position.
        """
        return {id(obj): pos for pos, obj in enumerate(self.all_objects)}

    @staticmethod
    def _raw_record_has_owner_index(raw_record: Any) -> bool:
        return raw_record is not None and (
            "OwnerIndex" in raw_record or "OWNERINDEX" in raw_record
        )

    @classmethod
    def _object_has_owner_reference(cls, obj: Any, raw_record: Any) -> bool:
        if getattr(obj, "parent", None) is not None:
            return True
        return cls._raw_record_has_owner_index(raw_record)

    @staticmethod
    def _set_owner_index_on_object(
        obj: Any,
        raw_record: Any,
        parent_pos: int,
    ) -> None:
        obj.owner_index = parent_pos
        if raw_record:
            if "OwnerIndex" in raw_record:
                raw_record["OwnerIndex"] = str(parent_pos)
            if "OWNERINDEX" in raw_record:
                raw_record["OWNERINDEX"] = str(parent_pos)

    @staticmethod
    def _append_owner_child(
        children_by_owner: dict[int, list[Any]],
        parent_pos: int,
        obj: Any,
    ) -> None:
        if parent_pos not in children_by_owner:
            children_by_owner[parent_pos] = []
        children_by_owner[parent_pos].append(obj)

    def _assign_owner_index_from_parent(
        self,
        obj: Any,
        raw_record: Any,
        warehouse_position: dict[int, int],
        children_by_owner: dict[int, list[Any]],
    ) -> bool:
        parent_obj = getattr(obj, "parent", None)
        if parent_obj is None:
            return False

        parent_pos = warehouse_position.get(id(parent_obj))
        if parent_pos is None:
            return False

        self._set_owner_index_on_object(obj, raw_record, parent_pos)
        self._append_owner_child(children_by_owner, parent_pos, obj)
        return True

    def _assign_owner_index_from_legacy_index(
        self,
        obj: Any,
        raw_record: Any,
        warehouse_position: dict[int, int],
        children_by_owner: dict[int, list[Any]],
    ) -> bool:
        old_owner = getattr(obj, "owner_index", None)
        if old_owner is None or old_owner <= 0:
            return False

        for potential_parent in self.all_objects:
            old_idx = getattr(potential_parent, "_record_index", None)
            if old_idx is None or old_idx != old_owner:
                continue
            parent_pos = warehouse_position.get(id(potential_parent))
            if parent_pos is None:
                return False
            self._set_owner_index_on_object(obj, raw_record, parent_pos)
            self._append_owner_child(children_by_owner, parent_pos, obj)
            return True

        log.debug(f"  Parent at old index {old_owner} not found for object")
        return False

    def _recalculate_owner_indices(
        self,
        warehouse_position: dict[int, int],
    ) -> dict[int, list[Any]]:
        children_by_owner: dict[int, list[Any]] = {}
        for obj in self.all_objects:
            raw_record = getattr(obj, "_raw_record", None)
            if not self._object_has_owner_reference(obj, raw_record):
                continue
            if self._assign_owner_index_from_parent(
                obj,
                raw_record,
                warehouse_position,
                children_by_owner,
            ):
                continue
            self._assign_owner_index_from_legacy_index(
                obj,
                raw_record,
                warehouse_position,
                children_by_owner,
            )
        return children_by_owner

    def _recalculate_index_in_sheet(
        self,
        children_by_owner: dict[int, list[Any]],
    ) -> None:
        for children in children_by_owner.values():
            self._assign_index_in_sheet_sequence(children)
        self._assign_index_in_sheet_sequence(self._top_level_sheet_children())

    @staticmethod
    def _raw_record_has_index_in_sheet(raw_record: Any) -> bool:
        return raw_record is not None and (
            "IndexInSheet" in raw_record or "INDEXINSHEET" in raw_record
        )

    def _should_preserve_index_in_sheet(self, obj: Any) -> bool:
        current_index = getattr(obj, "index_in_sheet", None)
        if current_index is not None:
            try:
                return int(current_index) < 0
            except (TypeError, ValueError):
                return False

        raw_record = getattr(obj, "_raw_record", None)
        return raw_record is not None and not self._raw_record_has_index_in_sheet(
            raw_record
        )

    def _should_preserve_explicit_positive_index_in_sheet(self, obj: Any) -> bool:
        if not self._preserve_loaded_index_in_sheet:
            return False

        raw_record = getattr(obj, "_raw_record", None)
        if not self._raw_record_has_index_in_sheet(raw_record):
            return False

        current_index = getattr(obj, "index_in_sheet", None)
        if current_index is None:
            current_index = self._get_index_in_sheet(obj)
        if current_index is None:
            return False

        try:
            return int(current_index) >= 0
        except (TypeError, ValueError):
            return False

    def _assign_index_in_sheet_sequence(self, children: list[Any]) -> None:
        reserved_indices: set[int] = set()
        if self._preserve_loaded_index_in_sheet:
            for child in children:
                if not self._should_preserve_explicit_positive_index_in_sheet(child):
                    continue
                current_index = getattr(child, "index_in_sheet", None)
                if current_index is None:
                    current_index = self._get_index_in_sheet(child)
                try:
                    reserved_indices.add(int(current_index))
                except (TypeError, ValueError):
                    continue

        next_index = 1
        for child in children:
            if self._should_preserve_index_in_sheet(
                child
            ) or self._should_preserve_explicit_positive_index_in_sheet(child):
                continue
            while next_index in reserved_indices:
                next_index += 1
            self._set_index_in_sheet(child, next_index)
            next_index += 1

    def _top_level_sheet_children(self) -> list[Any]:
        root_children: list[Any] = []
        for obj in self.all_objects:
            if obj is self.sheet:
                continue
            if getattr(obj, "parent", None) is not None:
                continue
            owner_index = getattr(obj, "owner_index", 0)
            if owner_index is None or int(owner_index) <= 0:
                root_children.append(obj)
        return root_children

    def _update_record_indices(
        self,
        warehouse_position: dict[int, int],
    ) -> None:
        for obj in self.all_objects:
            pos = warehouse_position.get(id(obj))
            if pos is not None and hasattr(obj, "_record_index"):
                _as_dynamic(obj)._record_index = pos

    # =========================================================================
    # Font Management
    # =========================================================================

    @property
    def font_manager(self) -> FontIDManager:
        """
        Get font manager for this document.

        The FontIDManager provides font lookup and creation:
        - get_or_create_font(name, size, ...) - Find or create font, returns ID
        - get_font_info(font_id) - Get font attributes by ID
        """
        from .altium_font_manager import FontIDManager

        if not hasattr(self, "_font_manager") or self._font_manager is None:
            if self.sheet is None:
                raise ValueError("Cannot access font_manager without a Sheet record")
            self._font_manager = FontIDManager(self.sheet)
        return self._font_manager

    # =========================================================================
    # Object Management Methods
    # =========================================================================
    # Structural mutation happens through the authoritative object store only.
    # Typed convenience properties are read-only live query views over self.objects.
    # Indices (OwnerIndex, IndexInSheet) are recalculated automatically on save.

    def _invalidate_fileheader_weight(self) -> None:
        """
        Drop the preserved FileHeader Weight after structural mutations.

        Existing-file round-trip saves preserve the original Weight until the
        object inventory changes. Once records are added/removed/reordered, the
        native importer expects the FileHeader Weight to match the rebuilt
        FileHeader stream, so the preserved count is no longer valid.
        """
        self._file_weight = None
        self._preserve_loaded_index_in_sheet = False

    @staticmethod
    def _is_harness_child_object(obj: object) -> bool:
        return isinstance(obj, (AltiumSchHarnessEntry, AltiumSchHarnessType))

    @staticmethod
    def _is_sheet_symbol_child_object(obj: object) -> bool:
        return isinstance(
            obj,
            (AltiumSchSheetEntry, AltiumSchSheetName, AltiumSchFileName),
        )

    @staticmethod
    def _is_additional_stream_object(obj: object) -> bool:
        return isinstance(
            obj,
            (
                AltiumSchHarnessConnector,
                AltiumSchHarnessEntry,
                AltiumSchHarnessType,
                AltiumSchSignalHarness,
            ),
        )

    def _mark_default_source_stream(self, obj: object) -> None:
        if not self._is_additional_stream_object(obj):
            return
        source_stream = getattr(obj, "_source_stream", None)
        if source_stream is None:
            _as_dynamic(obj)._source_stream = "Additional"

    @staticmethod
    def _iter_harness_connector_children(
        connector: AltiumSchHarnessConnector,
    ) -> list[object]:
        children: list[object] = list(getattr(connector, "entries", []))
        type_label = getattr(connector, "type_label", None)
        if type_label is None:
            type_label = next(
                (
                    child
                    for child in getattr(connector, "children", [])
                    if isinstance(child, AltiumSchHarnessType)
                ),
                None,
            )
        if type_label is not None:
            children.append(type_label)
        return children

    @staticmethod
    def _iter_sheet_symbol_children(
        symbol: AltiumSchSheetSymbol,
    ) -> list[object]:
        children: list[object] = []
        seen_ids: set[int] = set()

        def _append_child(candidate: object | None) -> None:
            if candidate is None:
                return
            if not AltiumSchDoc._is_sheet_symbol_child_object(candidate):
                return
            candidate_id = id(candidate)
            if candidate_id in seen_ids:
                return
            seen_ids.add(candidate_id)
            children.append(candidate)

        for entry in list(getattr(symbol, "entries", [])):
            _append_child(entry)
        _append_child(getattr(symbol, "sheet_name", None))
        _append_child(getattr(symbol, "file_name", None))
        for child in list(getattr(symbol, "children", [])):
            _append_child(child)
        return children

    @staticmethod
    def _set_owned_object_owner_index(obj: object, owner_pos: int) -> None:
        if hasattr(obj, "owner_index"):
            obj.owner_index = owner_pos

        owner_index_text = str(owner_pos)
        raw_record = getattr(obj, "_raw_record", None)
        if raw_record is None:
            return

        if "OWNERINDEX" in raw_record:
            raw_record["OWNERINDEX"] = owner_index_text
        if "OwnerIndex" in raw_record or "OWNERINDEX" not in raw_record:
            raw_record["OwnerIndex"] = owner_index_text

    def _prepare_harness_connector_child(
        self,
        connector: AltiumSchHarnessConnector,
        child: object,
    ) -> None:
        self._mark_default_source_stream(child)
        self._bind_schematic_object(child)
        if hasattr(child, "parent"):
            child.parent = connector
        if isinstance(child, AltiumSchHarnessEntry):
            child.owner_index_additional_list = True
        elif isinstance(child, AltiumSchHarnessType):
            child.owner_index_additional_list = True
            child.not_auto_position = True
            if child not in connector.children:
                connector.children.append(child)
            connector.type_label = child

    def _prepare_sheet_symbol_child(
        self,
        symbol: AltiumSchSheetSymbol,
        child: object,
        *,
        owner_pos: int,
    ) -> None:
        self._bind_schematic_object(child)
        self._attach_to_owner_relationships(child, symbol)
        self._set_owned_object_owner_index(child, owner_pos)

    @staticmethod
    def _assign_default_sheet_symbol_child_indices(
        symbol: AltiumSchSheetSymbol,
    ) -> None:
        for index, entry in enumerate(getattr(symbol, "entries", [])):
            if getattr(entry, "index_in_sheet", None) is None:
                entry.index_in_sheet = -2 if index == 0 else index

        for label in (
            getattr(symbol, "sheet_name", None),
            getattr(symbol, "file_name", None),
        ):
            if label is not None and getattr(label, "index_in_sheet", None) is None:
                label.index_in_sheet = -1

    def _sync_harness_connector_group_objects(
        self,
        connector: AltiumSchHarnessConnector,
    ) -> None:
        """
        Keep the internal object store aligned with a connector-owned harness group.
        """
        self._mark_default_source_stream(connector)
        self._bind_schematic_object(connector)

        for child in self._iter_harness_connector_children(connector):
            self._prepare_harness_connector_child(connector, child)

        if connector not in self.all_objects:
            return

        existing_children = [
            obj
            for obj in list(self.all_objects)
            if self._is_harness_child_object(obj)
            and getattr(obj, "parent", None) is connector
        ]
        for child in existing_children:
            self.all_objects.remove(child)
            self._uncategorize_object(child)

        insert_at = self.all_objects.index(connector) + 1
        for child in self._iter_harness_connector_children(connector):
            if child in self.all_objects:
                self.all_objects.remove(child)
                self._uncategorize_object(child)
            self.all_objects.insert(insert_at, child)
            self._categorize_object(child)
            insert_at += 1

        self._invalidate_fileheader_weight()

    def _sync_sheet_symbol_group_objects(
        self,
        symbol: AltiumSchSheetSymbol,
    ) -> None:
        """
        Keep the internal object store aligned with a symbol-owned sheet group.
        """
        self._bind_schematic_object(symbol)
        if symbol not in self.all_objects:
            return

        owner_pos = self.all_objects.index(symbol)
        ordered_children = self._iter_sheet_symbol_children(symbol)
        for child in ordered_children:
            self._prepare_sheet_symbol_child(symbol, child, owner_pos=owner_pos)

        symbol.entries = [
            child
            for child in ordered_children
            if isinstance(child, AltiumSchSheetEntry)
        ]
        symbol.sheet_name = next(
            (
                child
                for child in ordered_children
                if isinstance(child, AltiumSchSheetName)
            ),
            None,
        )
        symbol.file_name = next(
            (
                child
                for child in ordered_children
                if isinstance(child, AltiumSchFileName)
            ),
            None,
        )
        symbol.children[:] = ordered_children
        self._assign_default_sheet_symbol_child_indices(symbol)

        ordered_child_ids = {id(child) for child in ordered_children}
        existing_children = [
            obj
            for obj in list(self.all_objects)
            if self._is_sheet_symbol_child_object(obj)
            and getattr(obj, "parent", None) is symbol
        ]
        for child in existing_children:
            self.all_objects.remove(child)
            self._uncategorize_object(child)
            if id(child) not in ordered_child_ids:
                self._detach_from_parent_relationships(child)
                self._clear_detached_object_state(child)

        insert_at = self.all_objects.index(symbol) + 1
        for child in ordered_children:
            if child in self.all_objects:
                self.all_objects.remove(child)
                self._uncategorize_object(child)
            self.all_objects.insert(insert_at, child)
            self._categorize_object(child)
            insert_at += 1

        self._invalidate_fileheader_weight()

    def _iter_component_children(
        self,
        component: AltiumSchComponent,
    ) -> list[object]:
        children = list(getattr(component, "children", []))
        if children:
            return children
        return [
            obj for obj in self.all_objects if getattr(obj, "parent", None) is component
        ]

    def _iter_component_group_objects(
        self,
        component: AltiumSchComponent,
    ) -> list[object]:
        ordered: list[object] = []

        def _append_child_tree(obj: object) -> None:
            ordered.append(obj)
            for child in list(getattr(obj, "children", [])):
                _append_child_tree(child)

        for child in self._iter_component_children(component):
            _append_child_tree(child)
        return ordered

    @staticmethod
    def _has_ancestor(obj: object, ancestor: object) -> bool:
        parent = getattr(obj, "parent", None)
        while parent is not None:
            if parent is ancestor:
                return True
            parent = getattr(parent, "parent", None)
        return False

    @staticmethod
    def _owning_component_for_object(obj: object | None) -> AltiumSchComponent | None:
        current = obj
        while current is not None:
            if isinstance(current, AltiumSchComponent):
                return current
            current = getattr(current, "parent", None)
        return None

    def _sync_component_group_objects(
        self,
        component: AltiumSchComponent,
    ) -> None:
        """
        Keep the internal object store aligned with a component-owned subtree.
        """
        self._bind_schematic_object(component)
        if component not in self.all_objects:
            return

        ordered_children = self._iter_component_children(component)
        ordered_group = self._iter_component_group_objects(component)
        ordered_group_ids = {id(child) for child in ordered_group}

        existing_children = [
            obj for obj in list(self.all_objects) if self._has_ancestor(obj, component)
        ]
        for child in existing_children:
            self.all_objects.remove(child)
            self._uncategorize_object(child)
            if id(child) not in ordered_group_ids:
                self._detach_from_parent_relationships(child)
                self._clear_detached_object_state(child)

        component_pos = self.all_objects.index(component)
        insert_at = component_pos + 1
        for child in ordered_children:
            self._bind_schematic_object(child)
            self._attach_to_owner_relationships(child, component)
            self._set_owned_object_owner_index(child, component_pos)
            if child in self.all_objects:
                self.all_objects.remove(child)
                self._uncategorize_object(child)
            self.all_objects.insert(insert_at, child)
            self._categorize_object(child)
            insert_at += 1

            if not isinstance(child, AltiumSchImplementationList):
                continue

            implementation_list_pos = self.all_objects.index(child)
            for implementation in list(getattr(child, "children", [])):
                self._bind_schematic_object(implementation)
                self._attach_to_owner_relationships(implementation, child)
                self._set_owned_object_owner_index(
                    implementation,
                    implementation_list_pos,
                )
                if implementation in self.all_objects:
                    self.all_objects.remove(implementation)
                    self._uncategorize_object(implementation)
                self.all_objects.insert(insert_at, implementation)
                self._categorize_object(implementation)
                insert_at += 1

                implementation_pos = self.all_objects.index(implementation)
                for implementation_child in list(
                    getattr(implementation, "children", [])
                ):
                    self._bind_schematic_object(implementation_child)
                    self._attach_to_owner_relationships(
                        implementation_child,
                        implementation,
                    )
                    self._set_owned_object_owner_index(
                        implementation_child,
                        implementation_pos,
                    )
                    if implementation_child in self.all_objects:
                        self.all_objects.remove(implementation_child)
                        self._uncategorize_object(implementation_child)
                    self.all_objects.insert(insert_at, implementation_child)
                    self._categorize_object(implementation_child)
                    insert_at += 1

        self._invalidate_fileheader_weight()

    @staticmethod
    def _set_parent_if_supported(obj: object, owner: object) -> None:
        if hasattr(obj, "parent"):
            obj.parent = owner

    @staticmethod
    def _append_unique(collection: object, obj: object) -> None:
        if isinstance(collection, list) and obj not in collection:
            collection.append(obj)

    @classmethod
    def _append_runtime_owner_child(cls, owner: object, obj: object) -> None:
        cls._append_unique(getattr(owner, "children", None), obj)

    @classmethod
    def _attach_component_child(cls, obj: object, owner: AltiumSchComponent) -> None:
        cls._set_parent_if_supported(obj, owner)
        cls._append_runtime_owner_child(owner, obj)
        if isinstance(obj, AltiumSchPin):
            cls._append_unique(owner.pins, obj)
            return
        if isinstance(
            obj,
            (AltiumSchParameter, AltiumSchDesignator, AltiumSchImplementationList),
        ):
            cls._append_unique(owner.parameters, obj)
            return
        if isinstance(obj, COMPONENT_GRAPHIC_CHILD_TYPES):
            cls._append_unique(owner.graphics, obj)

    @classmethod
    def _attach_simple_owner_child(cls, obj: object, owner: object) -> None:
        cls._set_parent_if_supported(obj, owner)
        cls._append_runtime_owner_child(owner, obj)

    @staticmethod
    def _attach_harness_child(
        obj: object,
        owner: AltiumSchHarnessConnector,
    ) -> bool:
        if isinstance(obj, AltiumSchHarnessEntry):
            owner.add_entry(obj)
            return True
        if isinstance(obj, AltiumSchHarnessType):
            owner.set_type_label(obj)
            return True
        return False

    @classmethod
    def _attach_sheet_symbol_child(
        cls,
        obj: object,
        owner: AltiumSchSheetSymbol,
    ) -> None:
        cls._set_parent_if_supported(obj, owner)
        if isinstance(obj, AltiumSchSheetEntry):
            cls._append_unique(owner.entries, obj)
        if isinstance(obj, AltiumSchSheetName):
            owner.sheet_name = obj
        if isinstance(obj, AltiumSchFileName):
            owner.file_name = obj
        cls._append_runtime_owner_child(owner, obj)

    @classmethod
    def _attach_generic_owner_child(cls, obj: object, owner: object) -> None:
        cls._set_parent_if_supported(obj, owner)
        cls._append_runtime_owner_child(owner, obj)
        cls._append_unique(getattr(owner, "parameters", None), obj)

    @classmethod
    def _attach_to_owner_relationships(cls, obj: object, owner: object) -> None:
        """
        Update in-memory parent/child links when an owner is supplied.
        """
        if isinstance(owner, AltiumSchComponent):
            cls._attach_component_child(obj, owner)
            return

        if isinstance(owner, (AltiumSchImplementationList, AltiumSchImplementation)):
            cls._attach_simple_owner_child(obj, owner)
            return

        if isinstance(owner, AltiumSchHarnessConnector) and cls._attach_harness_child(
            obj, owner
        ):
            return

        if isinstance(owner, AltiumSchSheetSymbol):
            cls._attach_sheet_symbol_child(obj, owner)
            return

        cls._attach_generic_owner_child(obj, owner)

    @staticmethod
    def _detach_from_parent_relationships(obj: object) -> None:
        """
        Remove an object from any immediate in-memory parent collections.
        """
        parent = getattr(obj, "parent", None)
        if parent is None:
            return

        if isinstance(parent, AltiumSchComponent):
            if isinstance(obj, AltiumSchPin):
                try:
                    parent.pins.remove(obj)
                except ValueError:
                    pass
            elif isinstance(
                obj,
                (AltiumSchParameter, AltiumSchDesignator, AltiumSchImplementationList),
            ):
                try:
                    parent.parameters.remove(obj)
                except ValueError:
                    pass
            elif isinstance(obj, COMPONENT_GRAPHIC_CHILD_TYPES):
                try:
                    parent.graphics.remove(obj)
                except ValueError:
                    pass
            try:
                parent.children.remove(obj)
            except ValueError:
                pass
            return

        if isinstance(parent, (AltiumSchImplementationList, AltiumSchImplementation)):
            try:
                parent.children.remove(obj)
            except ValueError:
                pass
            return

        if isinstance(parent, AltiumSchHarnessConnector):
            if isinstance(obj, AltiumSchHarnessEntry) and obj in parent.entries:
                parent.entries.remove(obj)
            if isinstance(obj, AltiumSchHarnessType):
                if getattr(parent, "type_label", None) is obj:
                    parent.type_label = None
                try:
                    parent.children.remove(obj)
                except ValueError:
                    pass

        if isinstance(parent, AltiumSchSheetSymbol):
            if isinstance(obj, AltiumSchSheetEntry) and obj in parent.entries:
                parent.entries.remove(obj)
            if isinstance(obj, AltiumSchSheetName):
                if getattr(parent, "sheet_name", None) is obj:
                    parent.sheet_name = None
            if isinstance(obj, AltiumSchFileName):
                if getattr(parent, "file_name", None) is obj:
                    parent.file_name = None

        for attr_name in ("children", "parameters"):
            siblings = getattr(parent, attr_name, None)
            if not isinstance(siblings, list):
                continue
            try:
                siblings.remove(obj)
            except ValueError:
                pass

    @staticmethod
    def _clear_detached_object_state(obj: object) -> None:
        if hasattr(obj, "owner_index"):
            obj.owner_index = 0
        if hasattr(obj, "index_in_sheet"):
            obj.index_in_sheet = None
        if hasattr(obj, "parent"):
            obj.parent = None
        if hasattr(obj, "_bound_schematic_context"):
            obj._bound_schematic_context = None
        raw_record = getattr(obj, "_raw_record", None)
        if raw_record:
            raw_record.pop("OwnerIndex", None)
            raw_record.pop("OWNERINDEX", None)
            raw_record.pop("IndexInSheet", None)
            raw_record.pop("INDEXINSHEET", None)

    @staticmethod
    def _validate_top_level_add_object(
        obj: object,
        owner: object | None = None,
    ) -> None:
        """
        Reject connector-owned and symbol-owned child records as top-level
        document objects.
        """
        if isinstance(obj, (AltiumSchHarnessEntry, AltiumSchHarnessType)):
            raise ValueError(
                "Harness entries and harness type labels are connector-owned. "
                "Add them through the harness connector and then add the "
                "connector to the document with schdoc.add_object(connector)."
            )
        if owner is None and isinstance(
            obj,
            (AltiumSchSheetEntry, AltiumSchSheetName, AltiumSchFileName),
        ):
            raise ValueError(
                "Sheet entries, sheet name labels, and file name labels are "
                "symbol-owned. Add them through the sheet symbol and then add "
                "the sheet symbol to the document with "
                "schdoc.add_object(sheet_symbol)."
            )
        if owner is None and isinstance(
            obj,
            (
                AltiumSchPin,
                AltiumSchDesignator,
                AltiumSchImplementationList,
                AltiumSchImplementation,
                AltiumSchMapDefinerList,
                AltiumSchImplParams,
            ),
        ):
            raise ValueError(
                "Component-owned pin and implementation records must be added "
                "through a component-owned mutation API or with "
                "schdoc.add_object(child, owner=component)."
            )

    def add_object(self, obj: object, owner: object | None = None) -> None:
        """
        Add an object to the schematic.

        Adds the object to the document object store and sets owner_index
        when an owner is supplied.

        Args:
            obj: The object to add (OOP record instance)
            owner: Optional owner object (for setting OwnerIndex)
        """
        self._validate_top_level_add_object(obj, owner)
        self._mark_default_source_stream(obj)
        self._bind_schematic_object(obj)

        # Set ownership if owner specified
        if owner is not None:
            self._attach_to_owner_relationships(obj, owner)
            owner_pos = self._get_object_position(owner)
            if owner_pos is not None:
                # OwnerIndex is 0-based position in all_objects list
                # Reference file shows OwnerIndex=28 for children when parent is at position 28
                self._set_owned_object_owner_index(obj, owner_pos)

        # Add to all_objects
        self.objects.append(obj)
        self._invalidate_fileheader_weight()

        # Typed query views recompute from self.objects automatically.
        self._categorize_object(obj)
        component_owner = self._owning_component_for_object(owner)
        if component_owner is None and isinstance(obj, AltiumSchComponent):
            component_owner = obj
        if component_owner is not None:
            self._sync_component_group_objects(component_owner)
        if isinstance(owner, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(owner)
        elif isinstance(obj, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(obj)
        if isinstance(owner, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(owner)
        elif isinstance(obj, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(obj)

    def insert_object(
        self, obj: object, index: int, owner: object | None = None
    ) -> None:
        """
        Insert an object at a specific position in the schematic.

        Indices are recalculated automatically on save.

        Args:
            obj: The object to insert
            index: Position in all_objects to insert at
            owner: Optional owner object (for setting OwnerIndex)
        """
        self._validate_top_level_add_object(obj, owner)
        self._mark_default_source_stream(obj)
        self._bind_schematic_object(obj)

        # Set ownership if owner specified
        if owner is not None:
            self._attach_to_owner_relationships(obj, owner)
            owner_pos = self._get_object_position(owner)
            if owner_pos is not None:
                # OwnerIndex is 0-based position (Issue #1 fix)
                self._set_owned_object_owner_index(obj, owner_pos)

        # Insert at position
        self.all_objects.insert(index, obj)
        self._invalidate_fileheader_weight()

        # Typed query views recompute from self.objects automatically.
        self._categorize_object(obj)
        component_owner = self._owning_component_for_object(owner)
        if component_owner is None and isinstance(obj, AltiumSchComponent):
            component_owner = obj
        if component_owner is not None:
            self._sync_component_group_objects(component_owner)
        if isinstance(owner, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(owner)
        elif isinstance(obj, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(obj)
        if isinstance(owner, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(owner)
        elif isinstance(obj, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(obj)

    def remove_object(self, obj: object) -> bool:
        """
        Remove an object from the schematic.

        Removes the object from all_objects and clears ownership flags.

        Args:
            obj: The object to remove

        Returns:
            True if object was found and removed, False otherwise
        """
        parent = getattr(obj, "parent", None)
        if isinstance(parent, AltiumSchHarnessConnector):
            if isinstance(obj, AltiumSchHarnessEntry):
                return parent.remove_entry(obj)
            if isinstance(obj, AltiumSchHarnessType):
                return parent.clear_type_label()

        if obj not in self.all_objects:
            return False

        if isinstance(
            obj,
            (AltiumSchComponent, AltiumSchImplementationList, AltiumSchImplementation),
        ):
            owned_children = [
                child
                for child in list(self.all_objects)
                if self._has_ancestor(child, obj)
            ]
            for child in owned_children:
                self.all_objects.remove(child)
                self._uncategorize_object(child)
                self._detach_from_parent_relationships(child)
                self._clear_detached_object_state(child)
        elif isinstance(obj, AltiumSchHarnessConnector):
            harness_children = [
                child
                for child in list(self.all_objects)
                if self._is_harness_child_object(child)
                and getattr(child, "parent", None) is obj
            ]
            for child in harness_children:
                self.all_objects.remove(child)
                self._uncategorize_object(child)
                self._detach_from_parent_relationships(child)
                self._clear_detached_object_state(child)
        elif isinstance(obj, AltiumSchSheetSymbol):
            sheet_symbol_children = [
                child
                for child in list(self.all_objects)
                if self._is_sheet_symbol_child_object(child)
                and getattr(child, "parent", None) is obj
            ]
            for child in sheet_symbol_children:
                self.all_objects.remove(child)
                self._uncategorize_object(child)
                self._detach_from_parent_relationships(child)
                self._clear_detached_object_state(child)

        # Remove from all_objects
        self.all_objects.remove(obj)
        self._invalidate_fileheader_weight()

        # Typed query views recompute from self.objects automatically.
        self._uncategorize_object(obj)
        self._detach_from_parent_relationships(obj)

        # Clear ownership flags on the detached object.
        self._clear_detached_object_state(obj)

        return True

    def add_component(
        self,
        lib_reference: str,
        designator: str = "U?",
        x: int = 0,
        y: int = 0,
        library_path: str = "*",
        orientation: int = 0,
        is_mirrored: bool = False,
        part_id: int = 1,
        part_count: int | None = None,
        display_mode: int = 0,
        display_mode_count: int = 1,
        designator_x: int = 0,
        designator_y: int = 100,
        comment_x: int = 0,
        comment_y: int = -100,
    ) -> AltiumSchComponent:
        """
        Add a placed component and return the live component record.
        """
        from .altium_record_sch__parameter import AltiumSchParameter
        from .altium_sch_enums import Rotation90
        from .altium_symbol_transform import generate_unique_id

        resolved_part_id = int(part_id)
        if resolved_part_id < 1:
            raise ValueError("part_id must be >= 1")
        resolved_part_count = (
            max(1, resolved_part_id) if part_count is None else int(part_count)
        )
        if resolved_part_count < 1:
            raise ValueError("part_count must be >= 1")
        if resolved_part_id > resolved_part_count:
            raise ValueError(
                f"part_id {resolved_part_id} exceeds part_count {resolved_part_count}"
            )
        resolved_display_mode_count = int(display_mode_count)
        if resolved_display_mode_count < 1:
            raise ValueError("display_mode_count must be >= 1")

        component = AltiumSchComponent()
        component.lib_reference = lib_reference
        component.library_path = library_path
        component.source_library_name = (
            Path(library_path).name if library_path != "*" else "*"
        )
        component.location = CoordPoint.from_mils(x, y)
        component.orientation = Rotation90(int(orientation))
        component.is_mirrored = is_mirrored
        component.current_part_id = resolved_part_id
        component.part_count = resolved_part_count
        component.display_mode = int(display_mode)
        component.display_mode_count = resolved_display_mode_count
        component.unique_id = generate_unique_id()
        component._has_part_count = True
        component._has_current_part_id = True
        component._has_display_mode_count = True

        self.add_object(component)

        designator_record = AltiumSchDesignator()
        designator_record.text = designator
        designator_record.location = CoordPoint.from_mils(
            x + designator_x,
            y + designator_y,
        )
        designator_record.font_id = self.font_manager.get_or_create_font(
            font_name="Arial",
            font_size=12,
            bold=True,
        )
        designator_record.unique_id = generate_unique_id()
        self.add_object(designator_record, owner=component)

        comment_record = AltiumSchParameter()
        comment_record.name = "Comment"
        comment_record.text = "=Value"
        comment_record.location = CoordPoint.from_mils(
            x + comment_x,
            y + comment_y,
        )
        comment_record.font_id = self.font_manager.get_or_create_font(
            font_name="Arial",
            font_size=10,
            bold=False,
        )
        comment_record.unique_id = generate_unique_id()
        self.add_object(comment_record, owner=component)

        component.index_in_sheet = self._get_object_position(component)
        return component

    def add_component_from_library(
        self,
        library_path: str | Path,
        symbol_name: str,
        designator: str,
        x: int,
        y: int,
        orientation: int = 0,
        is_mirrored: bool = False,
        part_id: int = 1,
        display_mode: int = 0,
    ) -> AltiumSchComponent:
        """
        Place a component from a SchLib symbol and return the live component record.
        """
        from .altium_sch_enums import Rotation90
        from .altium_symbol_transform import generate_unique_id

        library_path = Path(library_path)
        if not library_path.exists():
            raise FileNotFoundError(f"Library not found: {library_path}")

        schlib = load_or_cache_schlib(self._schlib_cache, library_path)
        font_id_map = merge_schlib_fonts(self.font_manager, schlib)
        symbol = schlib.get_symbol(symbol_name)
        if symbol is None:
            available = [record.name for record in schlib.symbols]
            raise ValueError(
                f"Symbol '{symbol_name}' not found in {library_path}. "
                f"Available symbols: {available}"
            )

        resolved_part_id = int(part_id)
        if resolved_part_id < 1:
            raise ValueError("part_id must be >= 1")
        resolved_part_count = int(getattr(symbol, "part_count", 1) or 1)
        if resolved_part_id > resolved_part_count:
            raise ValueError(
                f"part_id {resolved_part_id} exceeds symbol part_count {resolved_part_count}"
            )

        component = AltiumSchComponent()
        component.lib_reference = symbol_name
        component.library_path = str(library_path)
        component.source_library_name = library_path.name
        component.location = CoordPoint.from_mils(x, y)
        component.orientation = Rotation90(int(orientation))
        component.is_mirrored = is_mirrored
        component.part_count = resolved_part_count
        component.current_part_id = resolved_part_id
        component.display_mode = display_mode
        component.display_mode_count = int(
            getattr(symbol, "display_mode_count", 1) or 1
        )
        component.unique_id = generate_unique_id()
        if getattr(symbol, "description", ""):
            component.component_description = str(symbol.description)
            component._has_component_description = True
        component._has_part_count = True
        component._has_current_part_id = True
        component._has_display_mode_count = True

        self.add_object(component)

        (
            graphics,
            pins,
            parameters,
            images,
            labels,
            text_frames,
        ) = clone_symbol_children(
            symbol,
            component,
            designator=designator,
            part_id=resolved_part_id,
            font_id_map=font_id_map,
        )

        for child in graphics:
            self.add_object(child, owner=component)
        for child in pins:
            self.add_object(child, owner=component)
        for child in parameters:
            self.add_object(child, owner=component)
        for child in images:
            self.add_object(child, owner=component)
        for child in labels:
            self.add_object(child, owner=component)
        for child in text_frames:
            self.add_object(child, owner=component)

        component.index_in_sheet = self._get_object_position(component)
        component.all_pin_count = len(component.pins)
        component._has_all_pin_count = True
        return component

    def _get_object_position(self, obj: Any) -> int | None:
        """
        Get the current position of an object in all_objects.
        """
        try:
            return self.all_objects.index(obj)
        except ValueError:
            return None

    def _categorize_object(self, obj: Any) -> None:
        """
        No-op: objects are already in self.objects.

                Type-specific access is now provided via properties.
                This method preserves the existing call surface but does not
                append (the caller already appends to self.objects).
        """

    def _uncategorize_object(self, obj: Any) -> None:
        """
        Remove object from the collection.

                Since this uses a single ObjectCollection, this just removes
                from self.objects.
        """
        if obj in self.objects:
            self.objects.remove(obj)

    def get_parameter(self, name: str) -> str | None:
        """
        Get document parameter value by name (case-insensitive).

        Args:
            name: Parameter name to look up

        Returns:
            Parameter text value, or None if not found
        """
        name_lower = name.lower()
        for param in self.parameters:
            if hasattr(param, "name") and param.name.lower() == name_lower:
                return param.text if hasattr(param, "text") else None
        return None

    def get_parameter_dict(self) -> dict[str, str]:
        """
        Get all document parameters as a dictionary.

        Returns:
            Dict mapping parameter names to their values
        """
        result = {}
        for param in self.parameters:
            if hasattr(param, "name") and hasattr(param, "text"):
                result[param.name] = param.text
        return result

    def get_template(self) -> AltiumSchTemplate | None:
        """
        Get the template object from the document.

        Template is stored as a top-level object with record type 39 (eTemplate).
        Its children contain the actual template graphics (polylines, labels,
        text frames, images) which are rendered when ShowTemplateGraphics=True.

        Returns:
            The template object, or None if no template exists.
        """
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchTemplate):
                return obj
        return None

    def _template_child_objects(self, template: AltiumSchTemplate) -> list[object]:
        """
        Return records currently owned by a template object.
        """
        template_owner_indexes = {
            index
            for index in (
                self._get_object_position(template),
                getattr(template, "_record_index", None),
            )
            if isinstance(index, int) and index >= 0
        }

        def is_template_child(obj: object) -> bool:
            if obj is template:
                return False
            if getattr(obj, "parent", None) is template:
                return True
            owner_index = getattr(obj, "owner_index", None)
            try:
                return int(owner_index) in template_owner_indexes
            except (TypeError, ValueError):
                return False

        return [obj for obj in list(self.all_objects) if is_template_child(obj)]

    @staticmethod
    def _is_top_level_parameter(obj: object) -> bool:
        """
        Return whether a parameter is sheet/document-level instead of owned.
        """
        if not isinstance(obj, AltiumSchParameter):
            return False
        if getattr(obj, "parent", None) is not None:
            return False
        owner_index = getattr(obj, "owner_index", 0)
        try:
            return int(owner_index) <= 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _clone_detached_schematic_object(obj: object) -> object:
        """
        Clone a source record and remove document-specific owner/index state.
        """
        cloned = deepcopy(obj)
        AltiumSchDoc._clear_detached_object_state(cloned)
        if hasattr(cloned, "_record_index"):
            _as_dynamic(cloned)._record_index = None
        if hasattr(cloned, "_raw_record_index"):
            _as_dynamic(cloned)._raw_record_index = None
        return cloned

    def _merge_template_fonts(self, template_doc: "AltiumSchDoc") -> dict[int, int]:
        """
        Merge template font table entries into this document and return ID map.
        """
        font_id_map: dict[int, int] = {}
        if self.sheet is None or template_doc.sheet is None:
            return font_id_map

        for source_id, font_data in template_doc.sheet.fonts.items():
            target_id = self.font_manager.get_or_create_font(
                font_name=font_data.get("name", "Times New Roman"),
                font_size=font_data.get("size", 10),
                bold=font_data.get("bold", False),
                italic=font_data.get("italic", False),
                rotation=font_data.get("rotation", 0),
                underline=font_data.get("underline", False),
                strikeout=font_data.get("strikeout", False),
            )
            font_id_map[int(source_id)] = target_id
        return font_id_map

    def _template_content_from_document(
        self,
        template_doc: "AltiumSchDoc",
        template_filename: str,
    ) -> tuple[AltiumSchTemplate, list[object], list[AltiumSchParameter]]:
        """
        Extract the container, visual children, and document parameters from SchDot.
        """
        source_template = template_doc.get_template()
        if source_template is None:
            template = AltiumSchTemplate()
            child_objects = [
                obj
                for obj in template_doc.all_objects
                if obj is not template_doc.sheet
                and not isinstance(obj, AltiumSchParameter)
            ]
        else:
            template = self._clone_detached_schematic_object(source_template)
            child_objects = template_doc._template_child_objects(source_template)

        template.filename = template_filename
        template._has_filename = True

        template_parameters = [
            obj for obj in template_doc.all_objects if self._is_top_level_parameter(obj)
        ]
        return template, child_objects, template_parameters

    def _existing_top_level_parameter_names(self) -> set[str]:
        """
        Return lower-case sheet/document-level parameter names already present.
        """
        names: set[str] = set()
        for param in self.parameters:
            if not self._is_top_level_parameter(param):
                continue
            name = getattr(param, "name", "")
            if name:
                names.add(str(name).lower())
        return names

    def _merge_missing_template_parameters(
        self,
        source_parameters: list[AltiumSchParameter],
        font_id_map: dict[int, int],
    ) -> int:
        """
        Add missing SchDot document parameters without overwriting target values.
        """
        existing_names = self._existing_top_level_parameter_names()
        inserted_count = 0
        for source_param in source_parameters:
            name = str(getattr(source_param, "name", "") or "")
            if not name:
                continue
            key = name.lower()
            if key in existing_names:
                continue
            param = self._clone_detached_schematic_object(source_param)
            remap_font_ids(param, font_id_map)
            self.add_object(param)
            existing_names.add(key)
            inserted_count += 1
        return inserted_count

    def clear_template(self) -> int:
        """
        Remove the schematic template and all template-owned objects.

        This removes the `AltiumSchTemplate` record, child graphics/text/images
        whose owner is the template, and sheet-level template metadata such as
        `TemplateFileName` and `ShowTemplateGraphics`. Template-owned embedded
        image storage is resynchronized immediately after removal.

        Returns:
            Number of records removed, including the template record itself.
            Returns 0 when the document has no template record.
        """
        template = self.get_template()
        if template is None:
            if self.sheet is not None:
                self.sheet.clear_template_references()
            return 0

        template_children = self._template_child_objects(template)

        removed_count = 0
        for obj in reversed(template_children):
            if self.remove_object(obj):
                removed_count += 1
        if self.remove_object(template):
            removed_count += 1

        if self.sheet is not None:
            self.sheet.clear_template_references()
        self._sync_embedded_images_from_objects()
        return removed_count

    def apply_template(
        self,
        template_path: str | Path,
        *,
        clear_existing: bool = True,
        merge_parameters: bool = True,
        template_filename: str | Path | None = None,
    ) -> int:
        """
        Apply a schematic `.SchDot` template to this document.

        The `.SchDot` file is parsed as a normal schematic, because Altium uses
        the same binary format for `.SchDoc` and `.SchDot`. Existing template
        graphics are cleared by default, the new template content is inserted
        after the sheet record, template font IDs are remapped into this
        document's font table, and sheet-level template metadata is updated.

        Args:
            template_path: Path to the `.SchDot` template file.
            clear_existing: If True, clear the current template before applying.
            merge_parameters: If True, add missing document-level parameters
                from the template without overwriting existing target values.
            template_filename: Optional filename/path to store in the SchDoc
                template metadata. If omitted, stores ``template_path`` exactly
                as passed after normal ``Path`` string conversion.

        Returns:
            Number of records inserted, including the template container,
            template-owned children, and any newly added document parameters.
        """
        if self.sheet is None:
            raise ValueError("apply_template requires a schematic sheet record")

        resolved_template_path = Path(template_path)
        if not resolved_template_path.exists():
            raise FileNotFoundError(f"Template not found: {resolved_template_path}")

        if clear_existing:
            self.clear_template()

        template_doc = AltiumSchDoc(resolved_template_path)
        stored_template_filename = str(
            template_filename
            if template_filename is not None
            else resolved_template_path
        )
        font_id_map = self._merge_template_fonts(template_doc)
        template, child_objects, source_parameters = (
            self._template_content_from_document(
                template_doc,
                stored_template_filename,
            )
        )

        insert_index = self.all_objects.index(self.sheet) + 1
        self.insert_object(template, insert_index)

        inserted_count = 1
        for offset, source_child in enumerate(child_objects, start=1):
            child = self._clone_detached_schematic_object(source_child)
            remap_font_ids(child, font_id_map)
            if (
                isinstance(child, AltiumSchImage)
                and child.filename
                and not child.image_data
            ):
                child.image_data = template_doc.embedded_images.get(child.filename)
            self.insert_object(child, insert_index + offset, owner=template)
            inserted_count += 1

        if merge_parameters:
            inserted_count += self._merge_missing_template_parameters(
                source_parameters,
                font_id_map,
            )

        self.sheet.clear_template_references()
        self.sheet.show_template_graphics = True
        self.sheet.template_filename = stored_template_filename
        self._sync_embedded_images_from_objects()
        return inserted_count

    def extract_template(
        self,
        output_path: str | Path,
        *,
        include_parameters: bool = True,
    ) -> int:
        """
        Extract the embedded schematic template to a standalone `.SchDot` file.

        The output `.SchDot` is written as a normal schematic-template document:
        the sheet record is copied from the source schematic with template
        metadata cleared, template-owned visual objects are unwrapped back to
        top-level objects, and embedded image storage is copied from the image
        records. This is the inverse of `apply_template()` for template
        graphics.

        Args:
            output_path: Destination `.SchDot` path.
            include_parameters: If True, copy document-level parameters from
                the source schematic into the output template file.

        Returns:
            Number of records extracted, excluding the sheet record.

        Raises:
            ValueError: If the document has no sheet or no embedded template.
        """
        if self.sheet is None:
            raise ValueError("extract_template requires a schematic sheet record")

        template = self.get_template()
        if template is None:
            raise ValueError("extract_template requires an embedded template record")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        template_doc = AltiumSchDoc(create_sheet=False)
        sheet = deepcopy(self.sheet)
        sheet.clear_template_references()
        sheet.owner_index = 0
        sheet.index_in_sheet = -1
        sheet.parent = None
        if hasattr(sheet, "_bound_schematic_context"):
            sheet._bound_schematic_context = None
        template_doc.sheet = sheet
        template_doc.all_objects = [sheet]
        template_doc._file_unique_id = self._file_unique_id

        extracted_count = 0
        if include_parameters:
            for source_param in self.all_objects:
                if not self._is_top_level_parameter(source_param):
                    continue
                param = self._clone_detached_schematic_object(source_param)
                template_doc.add_object(param)
                extracted_count += 1

        for source_child in self._template_child_objects(template):
            child = self._clone_detached_schematic_object(source_child)
            if (
                isinstance(child, AltiumSchImage)
                and child.filename
                and not child.image_data
            ):
                child.image_data = self.embedded_images.get(child.filename)
            template_doc.add_object(child)
            extracted_count += 1

        template_doc._sync_embedded_images_from_objects()
        template_doc.save(output_path)
        return extracted_count

    def get_summary(self) -> str:
        """
        Get a summary string of the schematic contents.

        Returns:
            Multi-line summary string
        """
        lines = []
        lines.append(
            f"SchDoc File: {self.filepath.name if self.filepath else 'Unknown'}"
        )

        if self.sheet:
            lines.append(f"  Sheet: {self.sheet.sheet_name or 'Unnamed'}")
            lines.append(
                f"  Size: {self.sheet.custom_x} x {self.sheet.custom_y} (style={self.sheet.sheet_style})"
            )
            lines.append(f"  Fonts: {self.sheet.font_id_count}")

        lines.append("\nObjects:")
        lines.append(f"  Components: {len(self.components)}")
        lines.append(f"  Wires: {len(self.wires)}")
        lines.append(f"  Buses: {len(self.buses)}")
        lines.append(f"  Net Labels: {len(self.net_labels)}")
        lines.append(f"  Power Ports: {len(self.power_ports)}")
        lines.append(f"  Cross-Sheet Connectors: {len(self.cross_sheet_connectors)}")
        lines.append(f"  Junctions: {len(self.junctions)}")
        lines.append(f"  Ports: {len(self.ports)}")
        lines.append(f"  Sheet Symbols: {len(self.sheet_symbols)}")
        lines.append(f"  Sheet Entries: {len(self.sheet_entries)}")
        lines.append(f"  Labels: {len(self.labels)}")
        lines.append(f"  Graphics: {len(self.graphics)}")
        lines.append(f"  Images: {len(self.images)}")
        lines.append(f"  Embedded Images: {len(self.embedded_images)}")
        lines.append(f"\nTotal Objects: {len(self.all_objects)}")

        return "\n".join(lines)

    def to_netlist(
        self,
        format: str = "wirelist",
        tolerance: int = 0,
        options: NetlistOptions | None = None,
    ) -> str:
        """
        Generate a WireList-format netlist from this schematic document.

        Args:
            format: Output format. Currently only `"wirelist"` is supported.
            tolerance: Connection tolerance in internal units (default: 0 = exact match).
                      Use 400000 for imperial 4-mil tolerance,
                      or 787402 for metric ~0.2mm tolerance.
            options: Netlist generation options from project settings.
                    If None, uses free document defaults (no project).
                    Use NetlistOptions.from_prjpcb() to load from a PrjPcb file.

        Returns:
            Netlist as string in requested format.

        Raises:
            ValueError: If format is not supported.

            # With project options:
        """
        if format.lower() != "wirelist":
            raise ValueError(
                f"Unsupported netlist format: {format}. Supported formats: wirelist"
            )

        from .altium_netlist_options import NetlistOptions
        from .altium_netlist_single_sheet import AltiumNetlistSingleSheetCompiler

        effective_options = options or NetlistOptions()

        generator = AltiumNetlistSingleSheetCompiler(
            self,
            tolerance=tolerance,
            options=effective_options,
        )
        netlist = generator.generate()

        # Convert Netlist to WireList format string
        # Pass allow_single_pin_nets option to control filtering
        return netlist.to_wirelist(
            strict=True,
            allow_single_pin_nets=effective_options.allow_single_pin_nets,
        )

    def _append_top_level_geometry_records(
        self,
        records: list[Any],
        objects: Any,
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        is_component_owned: Any,
        is_template_owned: Any,
    ) -> None:
        """
        Append top-level geometry records for a sorted object collection.
        """
        for obj in sorted(
            objects, key=lambda item: str(getattr(item, "unique_id", ""))
        ):
            if is_component_owned(obj) or is_template_owned(obj):
                continue
            geometry_record = obj.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

    def _append_callable_geometry_records(
        self,
        records: list[Any],
        objects: Any,
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        should_skip: Any,
    ) -> None:
        """
        Append geometry for a sorted collection with optional ownership filtering.
        """
        for obj in sorted(
            objects, key=lambda item: str(getattr(item, "unique_id", ""))
        ):
            if should_skip(obj):
                continue
            to_geometry = getattr(obj, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

    def _append_image_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        native_svg_hidden_image_ids: set[str],
    ) -> None:
        """
        Append image geometry while tracking hidden template-owned image ids.
        """
        for image in sorted(
            self.images, key=lambda obj: str(getattr(obj, "unique_id", ""))
        ):
            if ownership.is_component_owned(image):
                continue
            if ownership.is_template_owned(image):
                if not ownership.show_template_graphics:
                    native_svg_hidden_image_ids.add(
                        str(getattr(image, "unique_id", "") or "")
                    )
                continue
            if (
                ownership.template_idx is not None
                and getattr(image, "owner_index", None) == ownership.template_idx
                and not ownership.show_template_graphics
            ):
                native_svg_hidden_image_ids.add(
                    str(getattr(image, "unique_id", "") or "")
                )
            geometry_record = image.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

    def _append_special_graphic_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append note/text-frame/no-erc/parameter-set geometry records.
        """
        special_graphics = (
            obj
            for obj in self.objects
            if isinstance(
                obj,
                (
                    AltiumSchTextFrame,
                    AltiumSchNote,
                    AltiumSchNoErc,
                    AltiumSchParameterSet,
                ),
            )
        )
        self._append_callable_geometry_records(
            records,
            special_graphics,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            should_skip=lambda obj: (
                ownership.is_component_owned(obj) or ownership.is_template_owned(obj)
            ),
        )

    def _build_geometry_ownership_state(
        self, geometry_ctx: Any
    ) -> _SchGeometryOwnershipState:
        """
        Collect ownership and multipart component metadata for geometry export.
        """
        from collections import defaultdict

        template_obj = self.get_template()
        template_idx_raw = (
            getattr(template_obj, "_record_index", None)
            if template_obj is not None
            else None
        )
        try:
            template_idx = (
                int(template_idx_raw) if template_idx_raw is not None else None
            )
        except (TypeError, ValueError):
            template_idx = None

        show_template_graphics = bool(self.sheet and self.sheet.show_template_graphics)
        design_item_parts: dict[str, set[int]] = defaultdict(set)
        component_content_parts: dict[int, set[int]] = {}
        for comp in self.components:
            design_item_parts[comp.design_item_id].add(comp.current_part_id)
            content_part_ids: set[int] = set()
            for child in (*getattr(comp, "pins", []), *getattr(comp, "graphics", [])):
                owner_part = getattr(child, "owner_part_id", None)
                try:
                    owner_part_id = int(owner_part)
                except (TypeError, ValueError):
                    continue
                if owner_part_id > 0:
                    content_part_ids.add(owner_part_id)
            component_content_parts[id(comp)] = content_part_ids

        multipart_design_items = {
            did for did, part_ids in design_item_parts.items() if len(part_ids) > 1
        }
        _as_dynamic(geometry_ctx).multipart_design_items = multipart_design_items

        component_owner_indexes: set[int] = set()
        pin_owner_indexes: set[int] = set()
        for index, obj in enumerate(self.all_objects, start=1):
            if isinstance(obj, AltiumSchComponent):
                component_owner_indexes.add(index)
                for attr_name in ("_record_index", "index_in_sheet"):
                    attr_value = getattr(obj, attr_name, None)
                    if attr_value is None:
                        continue
                    try:
                        component_owner_indexes.add(int(attr_value))
                    except (TypeError, ValueError):
                        continue
            elif isinstance(obj, AltiumSchPin):
                pin_owner_indexes.add(index)
                attr_value = getattr(obj, "_record_index", None)
                if attr_value is None:
                    continue
                try:
                    pin_owner_indexes.add(int(attr_value))
                except (TypeError, ValueError):
                    continue

        return _SchGeometryOwnershipState(
            template_obj=template_obj,
            template_idx=template_idx,
            show_template_graphics=show_template_graphics,
            multipart_design_items=multipart_design_items,
            component_content_parts=component_content_parts,
            component_owner_indexes=component_owner_indexes,
            pin_owner_indexes=pin_owner_indexes,
        )

    def _append_passive_top_level_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        requested_render_options: Any,
        native_svg_hidden_image_ids: set[str],
    ) -> None:
        """
        Append top-level non-component records that do not need part-aware handling.
        """
        self._append_callable_geometry_records(
            records,
            self.buses,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            should_skip=lambda _obj: False,
        )
        self._append_callable_geometry_records(
            records,
            self.graphics,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            should_skip=lambda obj: (
                ownership.is_component_owned(obj) or ownership.is_template_owned(obj)
            ),
        )
        self._append_image_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            ownership=ownership,
            native_svg_hidden_image_ids=native_svg_hidden_image_ids,
        )
        self._append_special_graphic_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )

        self._append_template_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            ownership=ownership,
            requested_render_options=requested_render_options,
        )

        self._append_top_level_geometry_records(
            records,
            self.labels,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )
        self._append_top_level_geometry_records(
            records,
            self.net_labels,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )

    def _append_template_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        requested_render_options: Any,
    ) -> None:
        """
        Append template-owned geometry and the wrapper template record.
        """
        if not (
            ownership.show_template_graphics
            and ownership.template_obj is not None
            and ownership.template_idx is not None
        ):
            return

        template_geometry_ctx = geometry_ctx
        if (
            requested_render_options.truncate_font_size_for_baseline
            and not requested_render_options.fallback_project_parameters_for_star
        ):
            template_geometry_ctx = replace(
                geometry_ctx,
                options=replace(
                    requested_render_options,
                    fallback_project_parameters_for_star=True,
                ),
            )

        template_child_records = []
        for obj in self.all_objects:
            if getattr(obj, "owner_index", None) != ownership.template_idx:
                continue
            if (
                isinstance(obj, AltiumSchLabel)
                and str(getattr(obj, "text", "") or "").startswith("=PCB_")
                and not requested_render_options.fallback_project_parameters_for_star
            ):
                continue
            to_geometry = getattr(obj, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                template_geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is None:
                continue
            records.append(geometry_record)
            template_child_records.append(geometry_record)

        template_record = ownership.template_obj.to_geometry(
            template_geometry_ctx,
            document_id=document_id,
            child_records=template_child_records,
            unique_id_override=f"TPL{int(ownership.template_idx):05d}",
            units_per_px=units_per_px,
        )
        if template_record is not None:
            records.append(template_record)

    def _append_component_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append component wrappers plus their part-aware child geometry.
        """
        for comp in sorted(
            self.components, key=lambda obj: str(getattr(obj, "unique_id", ""))
        ):
            comp_ctx = geometry_ctx
            if getattr(comp, "override_colors", False):
                comp_ctx = comp_ctx.with_color_overrides(
                    area_color=getattr(comp, "area_color", None),
                    line_color=getattr(comp, "color", None),
                    pin_color=getattr(comp, "pin_color", None),
                )
            comp_ctx = comp_ctx.with_component_masking(
                self._component_is_compile_masked(
                    comp, geometry_ctx.compile_mask_bounds
                )
            )

            comp_params = {
                "LibReference": comp.lib_reference,
                "ComponentDescription": comp.component_description,
            }
            if not getattr(comp_ctx, "native_svg_export", False):
                comp_params["Value"] = comp.lib_reference
            for param in getattr(comp, "parameters", []):
                if (
                    hasattr(param, "name")
                    and hasattr(param, "text")
                    and not param.text.startswith("=")
                ):
                    comp_params[param.name] = param.text

            has_explicit_value_param = any(
                hasattr(param, "name")
                and isinstance(getattr(param, "name", None), str)
                and param.name.lower() == "value"
                for param in getattr(comp, "parameters", [])
            )

            def resolve_component_param_value(name: str) -> str | None:
                name_lower = name.lower()
                for key, value in comp_params.items():
                    if key.lower() == name_lower:
                        return value
                return None

            uses_non_default_part = comp.current_part_id > 1
            multiple_parts_placed = (
                comp.design_item_id in ownership.multipart_design_items
            )
            content_part_ids = ownership.component_content_parts.get(id(comp), set())
            content_spans_multiple_parts = len(content_part_ids) > 1
            show_multipart_suffix = comp.part_count > 1 and (
                uses_non_default_part
                or multiple_parts_placed
                or content_spans_multiple_parts
            )

            text_restores: list[tuple[object, str]] = []
            for param in getattr(comp, "parameters", []):
                if hasattr(param, "text") and param.text and param.text.startswith("="):
                    param_name = param.text[1:]
                    resolved_text = resolve_component_param_value(param_name)
                    if resolved_text is not None:
                        text_restores.append((param, param.text))
                        param.text = resolved_text
                    elif (
                        getattr(comp_ctx, "native_svg_export", False)
                        and param_name.lower() == "value"
                        and not has_explicit_value_param
                    ):
                        text_restores.append((param, param.text))
                        param.text = ""
                if (
                    isinstance(param, AltiumSchDesignator)
                    and show_multipart_suffix
                    and hasattr(param, "text")
                    and param.text
                ):
                    text_restores.append((param, param.text))
                    part_suffix = chr(ord("A") + comp.current_part_id - 1)
                    param.text = param.text + part_suffix

            try:
                self._append_single_component_geometry_records(
                    records,
                    comp,
                    comp_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
            finally:
                for param, original_text in reversed(text_restores):
                    param.text = original_text

    def _append_single_component_geometry_records(
        self,
        records: list[Any],
        comp: Any,
        comp_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
    ) -> None:
        """
        Append geometry for one component and its nested children.
        """
        geometry_record = comp.to_geometry(
            comp_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        if geometry_record is not None:
            records.append(geometry_record)

        for impl_list in sorted(
            (
                param
                for param in getattr(comp, "parameters", [])
                if isinstance(param, AltiumSchImplementationList)
            ),
            key=lambda obj: str(getattr(obj, "_record_index", "")),
        ):
            for implementation in sorted(
                getattr(impl_list, "children", []),
                key=lambda obj: str(getattr(obj, "_record_index", "")),
            ):
                geometry_record = implementation.to_geometry(
                    comp_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                if geometry_record is not None:
                    records.append(geometry_record)

                for child in sorted(
                    getattr(implementation, "children", []),
                    key=lambda obj: str(getattr(obj, "_record_index", "")),
                ):
                    if not isinstance(child, AltiumSchMapDefinerList):
                        continue
                    geometry_record = child.to_geometry(
                        comp_ctx,
                        document_id=document_id,
                        units_per_px=units_per_px,
                    )
                    if geometry_record is not None:
                        records.append(geometry_record)

        self._append_component_graphics_parameters_and_pins(
            records,
            comp,
            comp_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )

    def _append_component_graphics_parameters_and_pins(
        self,
        records: list[Any],
        comp: Any,
        comp_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
    ) -> None:
        """
        Append geometry for component graphics, parameters, and pins.
        """
        for graphic in sorted(
            getattr(comp, "graphics", []),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            owner_part = getattr(graphic, "owner_part_id", None)
            oracle_only_record = (
                owner_part is not None
                and owner_part > 0
                and owner_part != comp.current_part_id
            )
            to_geometry = getattr(graphic, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                comp_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                if oracle_only_record:
                    geometry_record = replace(
                        geometry_record,
                        extras={
                            **dict(getattr(geometry_record, "extras", {}) or {}),
                            "skip_svg": True,
                        },
                    )
                records.append(geometry_record)

        for param in sorted(
            getattr(comp, "parameters", []),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            to_geometry = getattr(param, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                comp_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

        for pin in sorted(
            getattr(comp, "pins", []),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            owner_part = getattr(pin, "owner_part_id", None)
            oracle_only_record = (
                owner_part is not None
                and owner_part > 0
                and owner_part != comp.current_part_id
            )
            if getattr(pin, "is_hidden", False) and not getattr(
                comp, "show_hidden_pins", False
            ):
                continue
            geometry_record = pin.to_geometry(
                comp_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                if oracle_only_record:
                    geometry_record = replace(
                        geometry_record,
                        extras={
                            **dict(getattr(geometry_record, "extras", {}) or {}),
                            "skip_svg": True,
                        },
                    )
                records.append(geometry_record)

    def _append_parameter_and_connector_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append top-level parameters plus port/power/cross-sheet geometry.
        """
        emitted_parameter_ids: set[str] = set()
        for param in sorted(
            self.parameters, key=lambda obj: str(getattr(obj, "unique_id", ""))
        ):
            if (
                ownership.is_component_owned(param)
                or ownership.is_template_owned(param)
                or ownership.is_pin_owned(param)
            ):
                continue
            records.append(
                param.to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
            )
            emitted_parameter_ids.add(str(getattr(param, "unique_id", "") or ""))

        for parameter_set in sorted(
            (
                obj
                for obj in self.graphics
                if isinstance(obj, AltiumSchParameterSet)
                and not ownership.is_component_owned(obj)
                and not ownership.is_template_owned(obj)
            ),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            for child_param in sorted(
                getattr(parameter_set, "parameters", []),
                key=lambda obj: str(getattr(obj, "unique_id", "")),
            ):
                child_unique_id = str(getattr(child_param, "unique_id", "") or "")
                if child_unique_id in emitted_parameter_ids:
                    continue
                emitted_parameter_ids.add(child_unique_id)
                records.append(
                    child_param.to_geometry(
                        geometry_ctx,
                        document_id=document_id,
                        units_per_px=units_per_px,
                    )
                )

        self._append_top_level_geometry_records(
            records,
            self.ports,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )
        self._append_top_level_geometry_records(
            records,
            self.power_ports,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )
        self._append_top_level_geometry_records(
            records,
            self.cross_sheet_connectors,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )

    def _append_hierarchy_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append harness connector and sheet symbol geometry.
        """
        for harness_connector in sorted(
            self.harness_connectors,
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if ownership.is_component_owned(
                harness_connector
            ) or ownership.is_template_owned(harness_connector):
                continue
            geometry_record = harness_connector.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

            parent_x, parent_y = geometry_ctx.transform_point(
                harness_connector.location.x,
                harness_connector.location.y,
            )
            parent_width = harness_connector.xsize * geometry_ctx.scale
            parent_height = harness_connector.ysize * geometry_ctx.scale
            connector_side = int(getattr(harness_connector, "side", 1))

            for entry in sorted(
                getattr(harness_connector, "entries", []),
                key=lambda obj: str(getattr(obj, "unique_id", "")),
            ):
                brace_orientation = (
                    connector_side
                    if connector_side in (2, 3)
                    else 1 - int(getattr(entry, "side", 0))
                )
                entry_record = entry.to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    parent_x=parent_x,
                    parent_y=parent_y,
                    parent_width=parent_width,
                    parent_height=parent_height,
                    parent_orientation=brace_orientation,
                    units_per_px=units_per_px,
                )
                if entry_record is not None:
                    records.append(entry_record)

            type_labels = []
            type_label = getattr(harness_connector, "type_label", None)
            if type_label is not None:
                type_labels.append(type_label)
            else:
                type_labels.extend(
                    child
                    for child in getattr(harness_connector, "children", [])
                    if getattr(getattr(child, "record_type", None), "name", "")
                    == "HARNESS_TYPE"
                )
            for harness_type in sorted(
                type_labels,
                key=lambda obj: str(getattr(obj, "unique_id", "")),
            ):
                geometry_record = harness_type.to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                if geometry_record is not None:
                    records.append(geometry_record)

        for sheet_symbol in sorted(
            self.sheet_symbols,
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            geometry_record = sheet_symbol.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

            parent_x, parent_y = geometry_ctx.transform_point(
                sheet_symbol.location.x,
                sheet_symbol.location.y,
            )
            parent_width = sheet_symbol.x_size * geometry_ctx.scale
            parent_height = sheet_symbol.y_size * geometry_ctx.scale

            for entry in sorted(
                getattr(sheet_symbol, "entries", []),
                key=lambda obj: str(getattr(obj, "unique_id", "")),
            ):
                entry_record = entry.to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    parent_x=parent_x,
                    parent_y=parent_y,
                    parent_width=parent_width,
                    parent_height=parent_height,
                    units_per_px=units_per_px,
                )
                if entry_record is not None:
                    records.append(entry_record)

            for child in sorted(
                [
                    child
                    for child in getattr(sheet_symbol, "children", [])
                    if child not in getattr(sheet_symbol, "entries", [])
                ],
                key=lambda obj: str(getattr(obj, "unique_id", "")),
            ):
                if not hasattr(child, "to_geometry"):
                    continue
                child_record = child.to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                if child_record is not None:
                    records.append(child_record)

    def _append_signal_and_wire_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append signal harness and wire geometry.
        """
        for signal_harness in sorted(
            self.signal_harnesses,
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if ownership.is_component_owned(
                signal_harness
            ) or ownership.is_template_owned(signal_harness):
                continue
            geometry_record = signal_harness.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

        for wire in sorted(
            self.wires, key=lambda obj: str(getattr(obj, "unique_id", ""))
        ):
            geometry_record = wire.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(geometry_record)

    def _build_sheet_geometry_setup(
        self,
        *,
        doc_unique_id: str,
        units_per_px: int,
        workspace_bottom_units: int,
    ) -> _SchSheetGeometrySetup:
        """
        Resolve sheet-size and border settings for geometry export.
        """
        from .altium_record_sch__sheet import ONSCREEN_SHEET_ZONES

        if self.sheet:
            sheet_raw = getattr(self.sheet, "_raw_record", {}) or {}
            has_explicit_border_on = any(
                str(key).lower() == "borderon" for key in sheet_raw
            )
            sheet_width_mils, sheet_height_mils = self.sheet.get_sheet_size_units()
            sheet_unique_id = getattr(self.sheet, "unique_id", "") or doc_unique_id
            area_color = int(self.sheet.area_color)
            margin = int(self.sheet.get_margin_units())
            reference_zones_on = bool(self.sheet.reference_zones_on)
            reference_zone_style = int(self.sheet.reference_zone_style)
            title_block_on = bool(self.sheet.title_block_on)
            border_on = (
                bool(self.sheet.border_on)
                if (has_explicit_border_on or reference_zones_on or title_block_on)
                else False
            )
            default_x_zones, default_y_zones, _default_margin = (
                ONSCREEN_SHEET_ZONES.get(
                    self.sheet.sheet_style,
                    (4, 4, 20),
                )
            )
            if self.sheet.use_custom_sheet:
                x_zones = (
                    self.sheet.custom_x_zones
                    if self.sheet.custom_x_zones > 0
                    else default_x_zones
                )
                y_zones = (
                    self.sheet.custom_y_zones
                    if self.sheet.custom_y_zones > 0
                    else default_y_zones
                )
            else:
                x_zones = default_x_zones
                y_zones = default_y_zones
        else:
            sheet_width_mils = 1100
            sheet_height_mils = 850
            sheet_unique_id = doc_unique_id
            area_color = 16317695
            border_on = True
            margin = 20
            reference_zones_on = False
            reference_zone_style = 0
            title_block_on = False
            x_zones = 4
            y_zones = 4
            has_explicit_border_on = True

        outer_top_units = (
            (workspace_bottom_units // units_per_px) - sheet_height_mils
        ) * units_per_px
        outer_right_units = sheet_width_mils * units_per_px
        return _SchSheetGeometrySetup(
            sheet_width_mils=sheet_width_mils,
            sheet_height_mils=sheet_height_mils,
            sheet_unique_id=sheet_unique_id,
            area_color=area_color,
            use_custom_sheet=bool(self.sheet.use_custom_sheet)
            if self.sheet is not None
            else False,
            border_on=border_on,
            margin=margin,
            reference_zones_on=reference_zones_on,
            reference_zone_style=reference_zone_style,
            title_block_on=title_block_on,
            x_zones=x_zones,
            y_zones=y_zones,
            units_per_px=units_per_px,
            workspace_bottom_units=workspace_bottom_units,
            outer_top_units=outer_top_units,
            outer_right_units=outer_right_units,
            has_explicit_border_on=has_explicit_border_on,
        )

    def _collect_top_level_geometry_parameters(self) -> dict[str, str]:
        """
        Collect top-level schematic parameters for geometry rendering.
        """
        param_dict: dict[str, str] = {}
        for param in self.parameters:
            if not hasattr(param, "name") or not hasattr(param, "text"):
                continue
            parent = getattr(param, "parent", None)
            if isinstance(
                parent, (AltiumSchComponent, AltiumSchPin, AltiumSchTemplate)
            ):
                continue
            param_dict[param.name] = param.text
        return param_dict

    def _append_reference_zone_geometry(
        self,
        sheet_operations: list[Any],
        setup: _SchSheetGeometrySetup,
        *,
        parameters: dict[str, str],
        project_parameters: dict[str, str] | None,
    ) -> None:
        """
        Append reference-zone primitives to the sheet record.
        """
        from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions
        from .altium_text_metrics import measure_text_height, measure_text_width

        if not (
            setup.reference_zones_on
            and setup.x_zones > 0
            and setup.y_zones > 0
            and self.sheet is not None
        ):
            return

        def svg_x_to_geometry(x: float) -> float:
            return float(x) * setup.units_per_px

        def svg_y_to_geometry(y: float) -> float:
            return (
                float(y) - float(setup.sheet_height_mils)
            ) * setup.units_per_px + setup.workspace_bottom_units

        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_pen,
            make_solid_brush,
        )

        ctx = SchSvgRenderContext(
            scale=1.0,
            flip_y=True,
            sheet_height=setup.sheet_height_mils,
            sheet_width=setup.sheet_width_mils,
            font_manager=self.font_manager,
            options=SchSvgRenderOptions.native_altium(),
            parameters=parameters,
            project_parameters=project_parameters or {},
        )
        if ctx.font_manager is None:
            raise ValueError("Reference zone geometry requires a font manager")

        font_id = int(self.sheet.system_font or 0)
        if font_id <= 0:
            font_id = ctx.font_manager.get_default_font_id()

        font_spec = ctx.font_manager.get_font_info(font_id)
        if font_spec is None:
            raise ValueError(
                f"Reference zone font ID {font_id} is not present in the document font table"
            )

        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            font_id
        )
        render_font_size = ctx.get_baseline_font_size(font_size_px)
        text_height_px = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        text_height_int = max(1, int(text_height_px))
        text_height_box_int = max(1, math.ceil(text_height_px))
        zone_text_baseline_offset = (text_height_int - 1) // 2
        zone_w = setup.sheet_width_mils / setup.x_zones
        zone_h = setup.sheet_height_mils / setup.y_zones
        zone_pen = make_pen(0x000000)
        zone_brush = make_solid_brush(0x000000)
        zone_font = {
            "name": str(font_spec.get("name", font_name)),
            "size": float(font_size_px) * setup.units_per_px,
            "rotation": float(font_spec.get("rotation", 0) or 0.0),
            "underline": bool(font_spec.get("underline", is_underline)),
            "italic": bool(font_spec.get("italic", is_italic)),
            "bold": bool(font_spec.get("bold", is_bold)),
            "strikeout": bool(font_spec.get("strikeout", False)),
        }

        def append_zone_line(x1: float, y1: float, x2: float, y2: float) -> None:
            sheet_operations.append(
                SchGeometryOp.lines(
                    [
                        [svg_x_to_geometry(x1), svg_y_to_geometry(y1)],
                        [svg_x_to_geometry(x2), svg_y_to_geometry(y2)],
                    ],
                    pen=zone_pen,
                )
            )

        def append_zone_text(label: str, x_text: float, y_baseline: float) -> None:
            sheet_operations.append(
                SchGeometryOp.string(
                    x=svg_x_to_geometry(x_text),
                    y=svg_y_to_geometry(y_baseline - render_font_size),
                    text=label,
                    font=zone_font,
                    brush=zone_brush,
                )
            )

        if setup.reference_zone_style == 1:
            column_specs = [
                (
                    setup.sheet_width_mils - zone_index * zone_w,
                    str(zone_index),
                    setup.sheet_width_mils - (zone_index - 0.5) * zone_w,
                )
                for zone_index in range(1, setup.x_zones + 1)
            ]
            row_specs = [
                (
                    zone_index * zone_h,
                    chr(ord("A") + setup.y_zones - zone_index),
                    (zone_index - 0.5) * zone_h
                    + zone_text_baseline_offset
                    + text_height_box_int
                    + (1 if setup.use_custom_sheet else 0),
                )
                for zone_index in range(1, setup.y_zones + 1)
            ]
        else:
            column_specs = [
                (
                    zone_index * zone_w,
                    str(zone_index),
                    (zone_index - 0.5) * zone_w,
                )
                for zone_index in range(1, setup.x_zones + 1)
            ]
            row_specs = [
                (
                    (setup.y_zones - zone_index) * zone_h,
                    chr(ord("A") + setup.y_zones - zone_index),
                    (setup.y_zones - zone_index + 0.5) * zone_h
                    + zone_text_baseline_offset,
                )
                for zone_index in range(1, setup.y_zones + 1)
            ]

        for x_divider, label, x_center in column_specs:
            text_width = measure_text_width(
                label,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            x_text = (
                x_center + text_width
                if setup.reference_zone_style == 1
                else x_center - text_width
            )
            append_zone_line(
                x_divider,
                setup.sheet_height_mils,
                x_divider,
                setup.sheet_height_mils - setup.margin,
            )
            append_zone_text(
                label,
                x_text,
                setup.sheet_height_mils - setup.margin / 2 + zone_text_baseline_offset,
            )
            append_zone_line(x_divider, 0, x_divider, setup.margin)
            append_zone_text(
                label, x_text, setup.margin / 2 + zone_text_baseline_offset
            )

        for y_divider, letter, y_center in row_specs:
            text_width = measure_text_width(
                letter,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            append_zone_line(0, y_divider, setup.margin, y_divider)
            append_zone_text(letter, setup.margin / 2 - text_width / 2, y_center)
            append_zone_line(
                setup.sheet_width_mils,
                y_divider,
                setup.sheet_width_mils - setup.margin,
                y_divider,
            )
            append_zone_text(
                letter,
                setup.sheet_width_mils - setup.margin / 2 - text_width / 2,
                y_center,
            )

    def _build_sheet_operations(
        self,
        setup: _SchSheetGeometrySetup,
        *,
        parameters: dict[str, str],
        project_parameters: dict[str, str] | None,
        doc_unique_id: str,
    ) -> list[Any]:
        """
        Build the sheet wrapper operations for the geometry document.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_pen,
            make_solid_brush,
        )

        sheet_operations = [
            SchGeometryOp.push_transform(
                [1, 0, 0, 1, 0, -setup.workspace_bottom_units]
            ),
            SchGeometryOp.begin_group(),
            SchGeometryOp.begin_group("DocumentMainGroup"),
            SchGeometryOp.begin_group(doc_unique_id),
            SchGeometryOp.rounded_rectangle(
                x1=0,
                y1=setup.outer_top_units,
                x2=setup.outer_right_units,
                y2=setup.workspace_bottom_units,
                brush=make_solid_brush(setup.area_color),
            ),
        ]

        if setup.render_border_rects:
            sheet_operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=0,
                    y1=setup.outer_top_units,
                    x2=setup.outer_right_units,
                    y2=setup.workspace_bottom_units,
                    pen=make_pen(0x000000),
                )
            )
        elif not setup.border_on:
            sheet_operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=0,
                    y1=setup.outer_top_units,
                    x2=setup.outer_right_units,
                    y2=setup.workspace_bottom_units,
                    brush=make_solid_brush(setup.area_color),
                )
            )

        if setup.border_on:
            inset_units = setup.working_margin * setup.units_per_px
            sheet_operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=inset_units,
                    y1=setup.outer_top_units + inset_units,
                    x2=setup.outer_right_units - inset_units,
                    y2=setup.workspace_bottom_units - inset_units,
                    brush=make_solid_brush(setup.area_color),
                )
            )
            if setup.render_border_rects:
                sheet_operations.append(
                    SchGeometryOp.rounded_rectangle(
                        x1=inset_units,
                        y1=setup.outer_top_units + inset_units,
                        x2=setup.outer_right_units - inset_units,
                        y2=setup.workspace_bottom_units - inset_units,
                        pen=make_pen(0x000000),
                    )
                )

        self._append_reference_zone_geometry(
            sheet_operations,
            setup,
            parameters=parameters,
            project_parameters=project_parameters,
        )

        if setup.title_block_on and self.sheet is not None:
            self._append_title_block_geometry(
                sheet_operations,
                sheet_width_mils=setup.sheet_width_mils,
                sheet_height_mils=setup.sheet_height_mils,
                margin=setup.margin,
                units_per_px=setup.units_per_px,
                parameters=parameters,
                project_parameters=project_parameters,
            )

        sheet_operations.extend(
            [
                SchGeometryOp.begin_group("DocumentItemsGroup"),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.pop_transform(),
            ]
        )
        return sheet_operations

    def _resolve_geometry_render_options(
        self,
        *,
        render_options: SchSvgRenderOptions | None,
        ir_profile: str | SchIrRenderProfile | None,
    ) -> tuple[Any, Any, Any]:
        """
        Resolve geometry profile and render options for IR generation.
        """
        from .altium_sch_geometry_oracle import (
            SchIrRenderProfile,
            normalize_sch_ir_render_profile,
        )
        from .altium_sch_svg_renderer import SchSvgRenderOptions

        if ir_profile is None:
            resolved_ir_profile = (
                SchIrRenderProfile.ONSCREEN
                if render_options is not None
                else SchIrRenderProfile.ORACLE
            )
        else:
            resolved_ir_profile = normalize_sch_ir_render_profile(ir_profile)

        if resolved_ir_profile == SchIrRenderProfile.ONSCREEN:
            base_render_options = SchSvgRenderOptions.onscreen_compiled()
        else:
            base_render_options = replace(
                SchSvgRenderOptions.onscreen(),
                image_background_to_alpha=False,
            )

        requested_render_options = render_options or base_render_options
        geometry_render_options = replace(requested_render_options)
        return resolved_ir_profile, requested_render_options, geometry_render_options

    def _build_geometry_context(
        self,
        setup: _SchSheetGeometrySetup,
        *,
        geometry_render_options: Any,
        requested_render_options: Any,
        parameters: dict[str, str],
        project_parameters: dict[str, str] | None,
        connection_points: set[tuple[int, int]],
        explicit_junction_points: set[tuple[int, int]],
        harness_junction_points: set[tuple[int, int]],
        harness_port_colors: dict[str, int],
        harness_sheet_entry_colors: dict[str, int],
        wire_segments: list[Any],
    ) -> Any:
        """
        Build the shared rendering context used by geometry exporters.
        """
        from .altium_sch_svg_renderer import SchSvgRenderContext

        return SchSvgRenderContext(
            scale=1.0,
            flip_y=True,
            sheet_height=setup.sheet_height_mils,
            sheet_width=setup.sheet_width_mils,
            font_manager=self.font_manager,
            options=geometry_render_options,
            sheet_area_color=setup.area_color
            if setup.area_color is not None
            else 0xFFFFFF,
            parameters=parameters,
            project_parameters=project_parameters or {},
            connection_points=connection_points,
            explicit_junction_points=explicit_junction_points,
            harness_junction_points=harness_junction_points,
            harness_port_colors=harness_port_colors,
            harness_sheet_entry_colors=harness_sheet_entry_colors,
            compile_mask_bounds=self._collect_compile_mask_bounds(),
            wire_segments=wire_segments,
            document_path=str(self.filepath) if self.filepath else None,
            object_definitions=self.object_definitions,
            native_svg_export=bool(
                requested_render_options.truncate_font_size_for_baseline
            ),
        )

    def _build_sheet_geometry_record(
        self,
        *,
        setup: _SchSheetGeometrySetup,
        doc_unique_id: str,
        sheet_operations: list[Any],
    ) -> Any:
        """
        Build the wrapper sheet record for the geometry document.
        """
        from .altium_sch_geometry_oracle import SchGeometryBounds, SchGeometryRecord

        return SchGeometryRecord(
            handle=f"{doc_unique_id}\\{setup.sheet_unique_id}",
            unique_id=setup.sheet_unique_id,
            kind="sheet",
            object_id="eSheet",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=sheet_operations,
        )

    def _build_geometry_render_hints(
        self,
        *,
        resolved_ir_profile: Any,
        geometry_render_options: Any,
        geometry_ctx: Any,
    ) -> dict[str, Any] | None:
        """
        Build optional render hints for IR consumers.
        """
        from .altium_sch_geometry_oracle import SchIrRenderProfile
        from .altium_sch_svg_renderer import (
            COMPILED_COMPILE_MASK_OVERLAY_OPACITY,
            SchCompileMaskRenderMode,
        )

        render_hints = None
        if resolved_ir_profile == SchIrRenderProfile.ONSCREEN:
            render_hints = {"ir_profile": SchIrRenderProfile.ONSCREEN.value}
        if (
            geometry_render_options.compile_mask_render_mode
            == SchCompileMaskRenderMode.COMPILED_VISUAL
            and geometry_ctx.compile_mask_bounds
        ):
            if render_hints is None:
                render_hints = {}
            render_hints["compile_mask"] = {
                "render_mode": "compiled_visual",
                "bounds": [list(bounds) for bounds in geometry_ctx.compile_mask_bounds],
                "background_color": str(geometry_ctx.background_color),
                "overlay_opacity": COMPILED_COMPILE_MASK_OVERLAY_OPACITY,
            }
        return render_hints

    def _build_runtime_image_hrefs(self, geometry_ctx: Any) -> dict[str, str]:
        """
        Build runtime image data URIs for image-backed geometry records.
        """
        import base64

        runtime_image_hrefs: dict[str, str] = {}
        for image in self.all_objects:
            if not isinstance(image, AltiumSchImage) or not getattr(
                image, "image_data", None
            ):
                continue
            background_color = None
            alpha_tolerance = 5
            if geometry_ctx.options.image_background_to_alpha:
                from .altium_record_types import color_to_hex

                background_color = color_to_hex(
                    int(getattr(geometry_ctx, "sheet_area_color", 0xFFFFFF) or 0xFFFFFF)
                )
                alpha_tolerance = geometry_ctx.options.image_alpha_tolerance
            png_data = image._convert_to_png(
                background_color=background_color,
                alpha_tolerance=alpha_tolerance,
                document_path=str(self.filepath) if self.filepath else None,
            )
            if not png_data:
                continue
            runtime_image_hrefs[str(getattr(image, "unique_id", "") or "")] = (
                "data:image/png;base64," + base64.b64encode(png_data).decode("ascii")
            )
        return runtime_image_hrefs

    def _build_geometry_document(
        self,
        *,
        records: list[Any],
        setup: _SchSheetGeometrySetup,
        doc_unique_id: str,
        resolved_ir_profile: Any,
        geometry_render_options: Any,
        geometry_ctx: Any,
        native_svg_hidden_image_ids: set[str],
    ) -> SchGeometryDocument:
        """
        Finalize the geometry document from collected records.
        """
        from .altium_sch_geometry_oracle import SchGeometryDocument

        records.sort(
            key=lambda record: (
                GEOMETRY_KIND_ORDER.get(str(record.kind or ""), 999),
                str(record.unique_id or ""),
            )
        )
        render_hints = self._build_geometry_render_hints(
            resolved_ir_profile=resolved_ir_profile,
            geometry_render_options=geometry_render_options,
            geometry_ctx=geometry_ctx,
        )
        document = SchGeometryDocument(
            records=records,
            source_path=str(self.filepath) if self.filepath else None,
            source_kind="SCH",
            include_kinds=["all"],
            coordinate_space={
                "kind": "screen_px_fixed",
                "units_per_px": setup.units_per_px,
                "y_axis_down": True,
            },
            canvas={
                "width_px": setup.sheet_width_mils,
                "height_px": setup.sheet_height_mils,
            },
            document_id=doc_unique_id,
            workspace_background_color="#E3E3E3",
            render_hints=render_hints,
            extras={
                "native_svg_hidden_image_ids": sorted(native_svg_hidden_image_ids),
            },
        )
        object.__setattr__(
            document,
            "_runtime_image_hrefs",
            self._build_runtime_image_hrefs(geometry_ctx),
        )
        return document

    def to_ir(
        self,
        project_parameters: dict[str, str] | None = None,
        *,
        profile: str | SchIrRenderProfile = "onscreen",
        render_options: SchSvgRenderOptions | None = None,
    ) -> SchGeometryDocument:
        """
        Build schematic IR using a named render profile.

        `onscreen` is the default application profile. `oracle` preserves the
        stricter geometry contract used by comparison and export flows.
        """
        return self.to_geometry(
            project_parameters=project_parameters,
            render_options=render_options,
            ir_profile=profile,
        )

    def to_geometry(
        self,
        project_parameters: dict[str, str] | None = None,
        render_options: SchSvgRenderOptions | None = None,
        ir_profile: str | SchIrRenderProfile | None = None,
    ) -> SchGeometryDocument:
        """
        Build a schematic IR document.

        The current implementation covers:
        - sheet/document borders and reference-zone primitives
        - sheet title-block geometry (standard and ANSI)
        - template wrappers and template-owned geometry children
        - arc, elliptical-arc, and pie primitives
        - simple shape primitives (lines, polygons, polylines, rectangles, rounded rectangles)
        - free labels
        - net labels, wires, buses, bus entries
        - standard ports and cross-sheet connectors
        - wrapper-only document parameters

        Use `to_ir()` when you want the named-profile wrapper. `to_geometry()`
        remains the lower-level IR construction entry point.
        """
        units_per_px = 64
        workspace_bottom_px = 1000
        workspace_bottom_units = workspace_bottom_px * units_per_px
        doc_unique_id = self._file_unique_id or "AAAAAAAA"
        setup = self._build_sheet_geometry_setup(
            doc_unique_id=doc_unique_id,
            units_per_px=units_per_px,
            workspace_bottom_units=workspace_bottom_units,
        )
        param_dict = self._collect_top_level_geometry_parameters()
        resolved_ir_profile, requested_render_options, geometry_render_options = (
            self._resolve_geometry_render_options(
                render_options=render_options,
                ir_profile=ir_profile,
            )
        )
        sheet_operations = self._build_sheet_operations(
            setup,
            parameters=param_dict,
            project_parameters=project_parameters,
            doc_unique_id=doc_unique_id,
        )

        self._compute_port_connected_ends()
        connection_points = self._compute_connection_points()
        explicit_junction_points = {
            (junction.location.x, junction.location.y)
            for junction in self.junctions
            if getattr(junction, "index_in_sheet", None) is not None
            and int(getattr(junction, "index_in_sheet", -1)) >= 0
        }
        harness_junction_points = self._compute_harness_junction_points()
        harness_port_colors = self._compute_signal_harness_port_colors()
        harness_sheet_entry_colors = self._compute_signal_harness_sheet_entry_colors()
        wire_segments = self._collect_wire_segments()

        records: list[Any] = []
        geometry_ctx = self._build_geometry_context(
            setup,
            geometry_render_options=geometry_render_options,
            requested_render_options=requested_render_options,
            parameters=param_dict,
            project_parameters=project_parameters,
            connection_points=connection_points,
            explicit_junction_points=explicit_junction_points,
            harness_junction_points=harness_junction_points,
            harness_port_colors=harness_port_colors,
            harness_sheet_entry_colors=harness_sheet_entry_colors,
            wire_segments=wire_segments,
        )
        ownership = self._build_geometry_ownership_state(geometry_ctx)
        native_svg_hidden_image_ids: set[str] = set()

        self._append_passive_top_level_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
            requested_render_options=requested_render_options,
            native_svg_hidden_image_ids=native_svg_hidden_image_ids,
        )
        self._append_component_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        self._append_parameter_and_connector_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        self._append_hierarchy_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        records.append(
            self._build_sheet_geometry_record(
                setup=setup,
                doc_unique_id=doc_unique_id,
                sheet_operations=sheet_operations,
            )
        )
        self._append_signal_and_wire_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        return self._build_geometry_document(
            records=records,
            setup=setup,
            doc_unique_id=doc_unique_id,
            resolved_ir_profile=resolved_ir_profile,
            geometry_render_options=geometry_render_options,
            geometry_ctx=geometry_ctx,
            native_svg_hidden_image_ids=native_svg_hidden_image_ids,
        )

    def _append_title_block_geometry(
        self,
        sheet_operations: list[object],
        *,
        sheet_width_mils: int,
        sheet_height_mils: int,
        margin: int,
        units_per_px: int,
        parameters: dict[str, str] | None = None,
        project_parameters: dict[str, str] | None = None,
    ) -> None:
        """
        Append title-block primitives to the sheet geometry record.
        """
        import datetime
        import os

        from .altium_record_sch__sheet import (
            DocumentBorderStyle,
            SHEET_STYLE_DESCRIPTIONS,
        )
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_font_payload,
            make_pen,
            make_solid_brush,
        )
        from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions

        if self.sheet is None or not self.sheet.title_block_on:
            return

        workspace_bottom_units = 1000 * units_per_px
        title_ctx = SchSvgRenderContext(
            scale=1.0,
            flip_y=True,
            sheet_height=sheet_height_mils,
            sheet_width=sheet_width_mils,
            font_manager=self.font_manager,
            options=SchSvgRenderOptions.native_altium(),
            parameters=parameters or {},
            project_parameters=project_parameters or {},
            document_path=str(self.filepath) if self.filepath else None,
        )

        font_id = int(getattr(self.sheet, "system_font", 0) or 0)
        if font_id > 0 and title_ctx.font_manager is not None:
            font_spec = title_ctx.font_manager.get_font_info(font_id)
            if font_spec is not None:
                font_name, font_size_px, is_bold, is_italic, is_underline = (
                    title_ctx.get_font_info(font_id)
                )
            else:
                font_name, font_size_px, is_bold, is_italic, is_underline = (
                    title_ctx.get_system_font_info()
                )
        else:
            font_name, font_size_px, is_bold, is_italic, is_underline = (
                title_ctx.get_system_font_info()
            )

        render_font_size = title_ctx.get_baseline_font_size(font_size_px)
        text_brush = make_solid_brush(0x000000)
        line_pen = make_pen(0x000000)
        font_payload = make_font_payload(
            name=font_name,
            size_px=font_size_px,
            units_per_px=units_per_px,
            underline=is_underline,
            italic=is_italic,
            bold=is_bold,
        )

        def svg_x_to_geometry(x: float) -> float:
            return float(x) * units_per_px

        def svg_y_to_geometry(y: float) -> float:
            return (
                float(y) - float(sheet_height_mils)
            ) * units_per_px + workspace_bottom_units

        def append_line(x1: float, y1: float, x2: float, y2: float) -> None:
            sheet_operations.append(
                SchGeometryOp.lines(
                    [
                        [svg_x_to_geometry(x1), svg_y_to_geometry(y1)],
                        [svg_x_to_geometry(x2), svg_y_to_geometry(y2)],
                    ],
                    pen=line_pen,
                )
            )

        def append_text(text: str, x: float, y: float) -> None:
            display_text = title_ctx.substitute_parameters(text)
            sheet_operations.append(
                SchGeometryOp.string(
                    x=svg_x_to_geometry(x),
                    y=svg_y_to_geometry(y - render_font_size),
                    text=display_text,
                    font=font_payload,
                    brush=text_brush,
                )
            )

        def build_truncated_file_path() -> str:
            if not self.filepath:
                return ""
            file_path = str(self.filepath)
            if len(file_path) <= 25:
                return file_path
            drive, tail = os.path.splitdrive(file_path)
            parts = [part for part in tail.split("\\") if part]
            first_segment = parts[0] if parts else ""
            filename = os.path.basename(file_path)
            if drive and first_segment:
                return f"{drive}\\{first_segment}\\..\\{filename}"
            if drive:
                return f"{drive}\\..\\{filename}"
            return f"..\\{filename}"

        if self.sheet.document_border_style == DocumentBorderStyle.ANSI:
            tb_height = 175
            tb_width = 625
            tb_right_section = 425
            row1_top = 25
            row2_top = 63
            row3_top = 125

            tb_right = sheet_width_mils - margin
            tb_bottom = sheet_height_mils - margin
            tb_left = tb_right - tb_width
            tb_top = tb_bottom - tb_height
            tb_middle = tb_right - tb_right_section

            y_row1 = tb_bottom - row1_top
            y_row2 = tb_bottom - row2_top
            y_row3 = tb_bottom - row3_top
            x_size_fcsm = tb_right - 387
            x_scale_sheet = tb_right - 175
            x_fcsm_dwg = tb_right - 276
            x_dwg_rev = tb_right - 36

            append_line(tb_right, tb_top, tb_left, tb_top)
            append_line(tb_left, tb_bottom, tb_left, tb_top)
            append_line(tb_middle, tb_bottom, tb_middle, tb_top)
            append_line(tb_right, y_row3, tb_middle, y_row3)
            append_line(tb_right, y_row2, tb_middle, y_row2)
            append_line(tb_right, y_row1, tb_middle, y_row1)
            append_line(tb_middle, y_row2, tb_right - 0.00625, y_row2)
            append_line(tb_middle, y_row1, tb_right - 0.00625, y_row1)
            append_line(x_size_fcsm, y_row1, x_size_fcsm, y_row2)
            append_line(tb_right - 325, tb_bottom, tb_right - 325, y_row1)
            append_line(x_scale_sheet, tb_bottom, x_scale_sheet, y_row1)
            append_line(x_fcsm_dwg, y_row1, x_fcsm_dwg, y_row2)
            append_line(x_dwg_rev, y_row1, x_dwg_rev, y_row2)

            append_text("Scale", tb_middle + 5, y_row1 + 9)
            append_text("Sheet", x_scale_sheet + 5, y_row1 + 9)
            append_text("Size", tb_middle + 5, y_row2 + 9)
            append_text("FCSM No.", x_size_fcsm + 5, y_row2 + 9)
            append_text("DWG No.", x_fcsm_dwg + 5, y_row2 + 9)
            append_text("Rev", x_dwg_rev + 5, y_row2 + 9)
            append_text(
                SHEET_STYLE_DESCRIPTIONS.get(self.sheet.sheet_style, "Custom"),
                tb_middle + 5,
                y_row2 + 22,
            )
            return

        tb_width = 350
        tb_height = 80
        tb_right = sheet_width_mils - margin
        tb_bottom = sheet_height_mils - margin
        tb_left = tb_right - tb_width
        tb_top = tb_bottom - tb_height
        x_size_number = tb_left + 50
        x_number_revision = tb_right - 100
        x_sheet_divider = tb_right - 150
        now = datetime.datetime.now()
        date_str = f"{now.month}/{now.day:02d}/{now.year}"
        file_path = build_truncated_file_path()

        append_line(tb_left, tb_top, tb_right, tb_top)
        append_line(tb_left, tb_top + 30, tb_right, tb_top + 30)
        append_line(tb_left, tb_top + 60, tb_right, tb_top + 60)
        append_line(tb_left, tb_top + 70, tb_right, tb_top + 70)
        append_line(tb_left, tb_top, tb_left, tb_bottom)
        append_line(x_number_revision, tb_top + 30, x_number_revision, tb_top + 60)
        append_line(x_size_number, tb_top + 30, x_size_number, tb_top + 60)
        append_line(x_sheet_divider, tb_top + 60, x_sheet_divider, tb_bottom)

        append_text("Title", tb_left + 5, tb_top + 9)
        append_text("Number", x_size_number + 5, tb_top + 39)
        append_text("Revision", x_number_revision + 5, tb_top + 39)
        append_text("Size", tb_left + 5, tb_top + 39)
        append_text(
            SHEET_STYLE_DESCRIPTIONS.get(self.sheet.sheet_style, "Custom"),
            tb_left + 10,
            tb_top + 54,
        )
        append_text("Date:", tb_left + 5, tb_top + 69)
        append_text(date_str, x_size_number, tb_top + 69)
        append_text("Sheet   of", x_sheet_divider + 5, tb_top + 69)
        append_text("File:", tb_left + 5, tb_top + 79)
        append_text(file_path, x_size_number, tb_top + 79)
        append_text("Drawn By:", x_sheet_divider + 5, tb_top + 79)

    def to_svg(
        self,
        include_border: bool = True,
        scale: float = 1.0,
        options: SchSvgRenderOptions | None = None,
        project_parameters: dict[str, str] | None = None,
        wrap_components: bool = False,
    ) -> str:
        """
        Render schematic to SVG.

        Args:
            include_border: Include sheet border, reference zones, and title block
            scale: Scale factor (1.0 = default)
            options: SchSvgRenderOptions for customizing rendering (junction colors, z-order, etc.)
            project_parameters: Project-level parameters (from PrjPcb) for substitution.
                               These are used when schematic-level parameters don't have a match.
            wrap_components: If True, wrap each component in a `<g>` element
                with `id=unique_id` and `data-designator` metadata.

        Returns:
            Complete SVG document as string
        """
        from .altium_sch_geometry_renderer import (
            SchGeometrySvgRenderOptions,
            SchGeometrySvgRenderer,
        )
        from .altium_sch_svg_renderer import SchSvgRenderOptions

        if abs(scale - 1.0) > 1e-9:
            raise ValueError("Schematic IR SVG rendering currently requires scale=1.0")

        render_options = options if options is not None else SchSvgRenderOptions()
        ir_profile = (
            "oracle" if render_options.truncate_font_size_for_baseline else "onscreen"
        )

        document = self.to_ir(
            project_parameters=project_parameters,
            profile=ir_profile,
            render_options=render_options,
        )
        runtime_image_hrefs = getattr(document, "_runtime_image_hrefs", None)

        if not include_border:
            document = replace(
                document,
                records=[
                    record
                    for record in document.records
                    if str(getattr(record, "kind", "") or "") != "sheet"
                ],
                workspace_background_color=None,
            )
            if isinstance(runtime_image_hrefs, dict):
                object.__setattr__(
                    document, "_runtime_image_hrefs", runtime_image_hrefs
                )

        if include_border and render_options.truncate_font_size_for_baseline:
            manual_junction_status = (
                self._build_native_manual_junction_status_render_hints(
                    sheet_height_px=int(document.canvas.get("height_px", 0) or 0)
                )
            )
            if manual_junction_status:
                render_hints = dict(document.render_hints or {})
                render_hints["manual_junction_status"] = manual_junction_status
                document = replace(document, render_hints=render_hints)
                if isinstance(runtime_image_hrefs, dict):
                    object.__setattr__(
                        document, "_runtime_image_hrefs", runtime_image_hrefs
                    )

        return SchGeometrySvgRenderer(
            SchGeometrySvgRenderOptions(
                include_workspace_background=include_border,
                text_mode=(
                    "native_svg_export"
                    if render_options.truncate_font_size_for_baseline
                    else "onscreen"
                ),
                compile_mask_render_mode=render_options.compile_mask_render_mode,
                text_as_polygons=render_options.text_as_polygons,
                polygon_text_tolerance=render_options.polygon_text_tolerance,
            )
        ).render(document)

    def _collect_compile_mask_bounds(self) -> list[tuple[int, int, int, int]]:
        """
        Collect bounds of all expanded compile masks.

        Returns:
            List of (min_x, min_y, max_x, max_y) tuples in Altium coordinates.
            Collapsed compile masks are excluded (they don't cover objects).
        """
        from .altium_record_sch__compile_mask import AltiumSchCompileMask

        bounds = []

        for obj in self.all_objects:
            if isinstance(obj, AltiumSchCompileMask):
                # Skip collapsed compile masks - they don't cover anything
                if obj.is_collapsed:
                    continue

                # Get bounds from location and corner
                x1 = obj.location.x
                y1 = obj.location.y
                x2 = obj.corner.x
                y2 = obj.corner.y

                min_x = min(x1, x2)
                max_x = max(x1, x2)
                min_y = min(y1, y2)
                max_y = max(y1, y2)

                bounds.append((min_x, min_y, max_x, max_y))

        return bounds

    def _collect_wire_segments(self) -> list[tuple[int, int, int, int]]:
        """
        Collect all wire segments in Altium coordinates.
        """
        segments: list[tuple[int, int, int, int]] = []

        for wire in self.wires:
            for start, end in zip(wire.points, wire.points[1:], strict=False):
                segments.append((start.x, start.y, end.x, end.y))

        return segments

    @staticmethod
    def _point_in_compile_mask(
        bounds: list[tuple[int, int, int, int]],
        x: int,
        y: int,
    ) -> bool:
        return any(
            min_x <= x <= max_x and min_y <= y <= max_y
            for min_x, min_y, max_x, max_y in bounds
        )

    @staticmethod
    def _point_on_segment_inclusive(
        px: int,
        py: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> bool:
        """
        Return True when a point touches a segment endpoint or interior.
        """
        cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
        if abs(cross) > 1:
            return False

        if x1 == x2 and y1 == y2:
            return px == x1 and py == y1
        if x1 != x2:
            t = (px - x1) / (x2 - x1)
        else:
            t = (py - y1) / (y2 - y1)
        return -0.01 <= t <= 1.01

    @staticmethod
    def _port_is_vertical(port: object) -> bool:
        style = getattr(port, "style", 0)
        try:
            style_value = int(style)
        except (TypeError, ValueError):
            return False
        return style_value in {4, 5, 6, 7}

    def _port_connection_endpoints(
        self, port: object
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        location = getattr(port, "location", None)
        if location is None:
            return ((0, 0), (0, 0))

        x = int(getattr(location, "x", 0))
        y = int(getattr(location, "y", 0))
        width = int(getattr(port, "width", 0) or 0)
        if self._port_is_vertical(port):
            return ((x, y), (x, y + width))
        return ((x, y), (x + width, y))

    def _compute_signal_harness_port_colors(self) -> dict[str, int]:
        """
        Map page-port IDs to the signal-harness color that makes them render
        as compiled harness objects.
        """
        harness_port_colors: dict[str, int] = {}
        signal_harnesses = list(self.signal_harnesses)
        if not signal_harnesses:
            return harness_port_colors

        for port in self.ports:
            port_id = str(getattr(port, "unique_id", "") or "")
            if not port_id:
                continue
            endpoints = self._port_connection_endpoints(port)
            for signal_harness in signal_harnesses:
                points = list(getattr(signal_harness, "points", []) or [])
                if len(points) < 2:
                    continue
                for start, end in zip(points, points[1:], strict=False):
                    if any(
                        self._point_on_segment_inclusive(
                            endpoint_x,
                            endpoint_y,
                            int(start.x),
                            int(start.y),
                            int(end.x),
                            int(end.y),
                        )
                        for endpoint_x, endpoint_y in endpoints
                    ):
                        harness_port_colors[port_id] = int(
                            getattr(signal_harness, "color", None) or 0
                        )
                        break
                if port_id in harness_port_colors:
                    break

        return harness_port_colors

    @staticmethod
    def _sheet_entry_connection_point(
        sheet_symbol: object,
        entry: object,
    ) -> tuple[int, int]:
        side = int(getattr(entry, "side", 0) or 0)
        offset = float(entry._distance_from_top_native_units())
        location = getattr(sheet_symbol, "location", None)
        parent_x = int(getattr(location, "x", 0)) if location is not None else 0
        parent_y = int(getattr(location, "y", 0)) if location is not None else 0
        parent_width = int(getattr(sheet_symbol, "x_size", 0) or 0)
        parent_height = int(getattr(sheet_symbol, "y_size", 0) or 0)

        if side == 1:
            return (parent_x + parent_width, int(round(parent_y - offset)))
        if side == 2:
            return (int(round(parent_x + offset)), parent_y)
        if side == 3:
            return (int(round(parent_x + offset)), parent_y - parent_height)
        return (parent_x, int(round(parent_y - offset)))

    def _compute_signal_harness_sheet_entry_colors(self) -> dict[str, int]:
        """
        Map sheet-entry IDs to connected signal-harness colors.
        """
        harness_entry_colors: dict[str, int] = {}
        signal_harnesses = list(self.signal_harnesses)
        if not signal_harnesses:
            return harness_entry_colors

        for sheet_symbol in self.sheet_symbols:
            for entry in getattr(sheet_symbol, "entries", []):
                entry_id = str(getattr(entry, "unique_id", "") or "")
                if not entry_id:
                    continue
                endpoint_x, endpoint_y = self._sheet_entry_connection_point(
                    sheet_symbol,
                    entry,
                )
                for signal_harness in signal_harnesses:
                    points = list(getattr(signal_harness, "points", []) or [])
                    if len(points) < 2:
                        continue
                    for start, end in zip(points, points[1:], strict=False):
                        if self._point_on_segment_inclusive(
                            endpoint_x,
                            endpoint_y,
                            int(start.x),
                            int(start.y),
                            int(end.x),
                            int(end.y),
                        ):
                            harness_entry_colors[entry_id] = int(
                                getattr(signal_harness, "color", None) or 0
                            )
                            break
                    if entry_id in harness_entry_colors:
                        break

        return harness_entry_colors

    def _component_is_compile_masked(
        self,
        comp: AltiumSchComponent,
        compile_mask_bounds: list[tuple[int, int, int, int]],
    ) -> bool:
        """
        Approximate the component-level compilation-masked state for SVG rendering.

        Components are treated as masked only when the placement location and all
        rendered pin endpoints/hotspots fall inside compile masks. This preserves
        the observed native distinction between fully-contained components and
        straddling ones.
        """
        if not compile_mask_bounds:
            return False

        if not self._point_in_compile_mask(
            compile_mask_bounds, comp.location.x, comp.location.y
        ):
            return False

        relevant_pins = [
            pin
            for pin in comp.pins
            if getattr(pin, "owner_part_id", None)
            in (None, 0, -1, comp.current_part_id)
        ]
        if not relevant_pins:
            return False

        for pin in relevant_pins:
            hot_spot = pin.get_hot_spot()
            if not self._point_in_compile_mask(
                compile_mask_bounds, pin.location.x, pin.location.y
            ):
                return False
            if not self._point_in_compile_mask(
                compile_mask_bounds, hot_spot.x, hot_spot.y
            ):
                return False

        return True

    def _compute_port_connected_ends(self) -> None:
        """
        Compute which end of each port is connected to a wire.

        Port endpoint mapping behavior:
        For horizontal ports, pin mapping is:
          Pin1/PortLocation1 = RIGHT end (Location.X + Width) = "Extremity"
          Pin2/PortLocation2 = LEFT end (Location.X) = "Origin"

        Sets _computed_connected_end on each port:
          0 = None (not connected to any wire)
          1 = Origin (LEFT end has wire endpoint)
          2 = Extremity (RIGHT end has wire endpoint)
          3 = Both ends connected
        """
        # Gather all wire endpoints
        wire_endpoints: set[tuple[int, int]] = set()
        for wire in self.wires:
            for pt in wire.points:
                wire_endpoints.add((pt.x, pt.y))
        for bus in self.buses:
            for pt in bus.points:
                wire_endpoints.add((pt.x, pt.y))

        for port in self.ports:
            # Origin = LEFT end = (Location.X, Location.Y)
            origin = (port.location.x, port.location.y)
            # Extremity = RIGHT end = (Location.X + Width, Location.Y)
            extremity = (port.location.x + port.width, port.location.y)

            has_origin = origin in wire_endpoints
            has_extremity = extremity in wire_endpoints

            if has_origin and has_extremity:
                _as_dynamic(port)._computed_connected_end = 3
            elif has_origin:
                _as_dynamic(port)._computed_connected_end = 1
            elif has_extremity:
                _as_dynamic(port)._computed_connected_end = 2
            else:
                _as_dynamic(port)._computed_connected_end = 0

    def _compute_connection_points(
        self,
        *,
        include_explicit_junctions: bool = True,
    ) -> set[tuple[int, int]]:
        """
        Compute all connection points where junctions should be rendered.

        Uses the current ObjectsCount >= 3 junction rule:
        - 3+ wire/bus endpoints meeting at same point -> render junction
        - Wire endpoint lying on another wire's segment (T-junction) -> render junction
        - 2 wire endpoints meeting (L-junction or continuation) -> no junction

        The key insight is that native Altium counts a wire passing through a point
        as 2 objects (segments before and after), while endpoints count as 1 each.

        Returns:
            Set of (x, y) tuples in Altium coordinates where junctions render
        """
        from collections import Counter

        def point_on_segment(
            px: int, py: int, x1: int, y1: int, x2: int, y2: int
        ) -> bool:
            """
            Check if point (px, py) lies strictly BETWEEN segment endpoints.
            """
            # Check if point is collinear with segment
            cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
            if abs(cross) > 1:  # Allow small tolerance for rounding
                return False

            # Check if point is within segment bounds
            if x1 != x2:
                t = (px - x1) / (x2 - x1)
            elif y1 != y2:
                t = (py - y1) / (y2 - y1)
            else:
                return False  # Degenerate segment (zero length)

            # Point must be strictly BETWEEN endpoints (not at endpoints)
            # This ensures we only detect T-junctions, not endpoint meetings
            return 0.01 < t < 0.99

        # Count wire SEGMENTS at each point, not wire objects
        # - Endpoint (first/last vertex): contributes 1 segment
        # - Corner (intermediate vertex): contributes 2 segments (end of one, start of next)
        # This matches native Altium's ObjectsCount which counts segments, not objects
        point_counts: Counter[tuple[int, int]] = Counter()

        # Collect all wire/bus/harness objects for segment checking
        all_connectable = (
            list(self.wires) + list(self.buses) + list(self.signal_harnesses)
        )

        def count_object_points(obj: object) -> None:
            """
            Count points from a wire/bus/harness object.
            """
            if len(obj.points) == 0:
                return
            elif len(obj.points) == 1:
                # Single point - counts as 1
                pt = obj.points[0]
                point_counts[(pt.x, pt.y)] += 1
            else:
                # Multi-point: endpoints count 1, intermediate points count 2
                for i, pt in enumerate(obj.points):
                    if i == 0 or i == len(obj.points) - 1:
                        # Endpoint: 1 segment
                        point_counts[(pt.x, pt.y)] += 1
                    else:
                        # Intermediate/corner: 2 segments (end of one, start of next)
                        point_counts[(pt.x, pt.y)] += 2

        # Collect points from wires
        for wire in self.wires:
            count_object_points(wire)

        # Collect points from buses
        for bus in self.buses:
            count_object_points(bus)

        # Collect points from signal harnesses
        for harness in self.signal_harnesses:
            count_object_points(harness)

        # Find T-junctions: wire/bus endpoint lies ON another object's segment
        # These count as 3+ objects in native Altium (endpoint + 2 segment halves)
        t_junctions: set[tuple[int, int]] = set()

        for obj in all_connectable:
            if len(obj.points) < 1:
                continue
            # Check each endpoint of this object
            for pt in obj.points:
                px, py = pt.x, pt.y
                # Check if this point lies on any other object's segment
                for other in all_connectable:
                    if other is obj:
                        continue
                    if len(other.points) < 2:
                        continue
                    # Check each segment of the other object
                    for i in range(len(other.points) - 1):
                        p1 = other.points[i]
                        p2 = other.points[i + 1]
                        if point_on_segment(px, py, p1.x, p1.y, p2.x, p2.y):
                            t_junctions.add((px, py))
                            break  # Found T-junction for this point

        # Connection points where junctions render:
        # 1. Points with 3+ endpoints (star junctions)
        # 2. T-junctions (endpoint on another's segment)
        # 3. Explicit junction records
        connection_points = {pt for pt, count in point_counts.items() if count >= 3}
        connection_points.update(t_junctions)

        # Add explicit junction locations (manual junctions always render)
        if include_explicit_junctions:
            for junction in self.junctions:
                connection_points.add((junction.location.x, junction.location.y))

        return connection_points

    def _build_native_manual_junction_status_render_hints(
        self,
        *,
        sheet_height_px: int,
    ) -> list[dict[str, float | str]]:
        """
        Build native SVG manual-junction-status overlays.

        Native Altium emits an extra document-level junction pass before
        ``DocumentItemsGroup`` for persisted manual junction records that are
        also active connection points. Imported/grid-generated junction dots
        in the FRDM corpus are serialized as transient ``IndexInSheet=-1``
        records and do not participate in this pass. The dots use the
        painter's manual-junction preference color (``0x800000`` -> ``#000080``)
        in stored BGR byte order, not the junction record color.
        """
        if sheet_height_px <= 0:
            return []

        from .altium_record_types import color_to_hex

        connection_points = self._compute_connection_points(
            include_explicit_junctions=False
        )
        overlays: list[dict[str, float | str]] = []
        seen: set[tuple[int, int]] = set()
        manual_junction_color_hex = color_to_hex(0x800000)

        for junction in self.junctions:
            junction_index = getattr(junction, "index_in_sheet", None)
            if junction_index is None or int(junction_index) < 0:
                continue
            center = (junction.location.x, junction.location.y)
            if center not in connection_points or center in seen:
                continue
            seen.add(center)
            overlays.append(
                {
                    "x": float(junction.location.x - 2),
                    "y": float(sheet_height_px - junction.location.y - 2),
                    "width": 4.0,
                    "height": 4.0,
                    "rx": 2.0,
                    "ry": 2.0,
                    "fill": manual_junction_color_hex,
                    "stroke": manual_junction_color_hex,
                }
            )

        return overlays

    def _compute_harness_junction_points(self) -> set[tuple[int, int]]:
        """
        Compute signal harness junction points (T-junctions).

        A harness junction is rendered when one harness's START point lies
        geometrically on another harness's line segment. This creates a T-junction
        where the "stem" harness connects to the "cross" harness.

        Unlike wire/bus junctions (which require explicit vertex sharing),
        signal harness junctions are determined by geometric intersection.

        Returns:
            Set of (x, y) tuples in Altium coordinates where junctions should render
        """
        if len(self.signal_harnesses) < 2:
            return set()

        junction_points: set[tuple[int, int]] = set()

        def point_on_segment(
            px: int, py: int, x1: int, y1: int, x2: int, y2: int
        ) -> bool:
            """
            Check if point (px, py) lies on line segment (x1,y1)-(x2,y2).
            """
            # Check if point is collinear with segment
            cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
            if abs(cross) > 1:  # Allow small tolerance
                return False

            # Check if point is within segment bounds
            if x1 != x2:
                t = (px - x1) / (x2 - x1)
            elif y1 != y2:
                t = (py - y1) / (y2 - y1)
            else:
                return px == x1 and py == y1  # Degenerate segment

            # Point must be strictly BETWEEN endpoints (not at endpoints)
            return 0.01 < t < 0.99

        # For each harness, check if its start point lies on any other harness's segment
        for harness in self.signal_harnesses:
            if len(harness.points) < 1:
                continue
            start_pt = harness.points[0]
            start_x, start_y = start_pt.x, start_pt.y

            for other in self.signal_harnesses:
                if other is harness:
                    continue
                # Check each segment of the other harness
                for i in range(len(other.points) - 1):
                    p1 = other.points[i]
                    p2 = other.points[i + 1]
                    if point_on_segment(start_x, start_y, p1.x, p1.y, p2.x, p2.y):
                        junction_points.add((start_x, start_y))
                        break

        return junction_points

    def _save_roundtrip(self, filepath: Path, debug: bool = False) -> bool:
        """
        Save SchDoc to file (round-trip support).

        Uses OleWriter (same approach as SchLibCleaner) to write modified OLE file.

        Args:
            filepath: Output path (defaults to self.filepath)
            debug: Enable debug output

        Returns:
            True if save was successful, False otherwise
        """
        if filepath is None:
            filepath = self.filepath

        if filepath is None:
            raise ValueError("No filepath specified for save")

        filepath = Path(filepath)
        log.info(f"Saving SchDoc to: {filepath}")

        try:
            self._bind_all_objects_to_context()
            self._sync_embedded_images_from_objects()
            # Recalculate OwnerIndex and IndexInSheet before saving.
            log.info("  Recalculating object indices...")
            self._recalculate_indices()

            # Import AltiumOleWriter
            from .altium_ole import AltiumOleWriter

            # Open original file
            with AltiumOleFile(str(self.filepath)) as ole:
                # Build modified streams
                log.info("  Building FileHeader stream...")
                fileheader_data = self._build_fileheader_stream(debug)
                log.info("  Building Additional stream...")
                additional_data = self._build_additional_stream(debug)

                # Create OleWriter from original file
                ole_writer = AltiumOleWriter()
                ole_writer.fromOleFile(ole)

                # Update FileHeader stream
                ole_writer.editEntry("FileHeader", data=fileheader_data)
                if ole.exists("Additional") and ole.get_type("Additional") == 2:
                    ole_writer.editEntry("Additional", data=additional_data)
                else:
                    ole_writer.addEntry("Additional", data=additional_data)

                # Update Storage stream (handles embedded images)
                # If embedded_images is empty, this writes a minimal empty stream
                if ole.exists("Storage") and ole.get_type("Storage") == 2:
                    log.info("  Building Storage stream...")
                    storage_data = self._build_storage_stream(debug)
                    ole_writer.editEntry("Storage", data=storage_data)

                # Write to output
                ole_writer.write(str(filepath))

            log.info(f"  Saved successfully: {len(self.all_objects)} objects")
            return True

        except Exception as e:
            log.error(f"  Error saving {filepath.name}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            return False

    def to_schdoc(self, filepath: Path | str, debug: bool = False) -> bool:
        """
        Write SchDoc to file.

        This is the single API for saving SchDoc files to disk.

        Behavior:
            - If loaded from an existing file via the constructor, uses round-trip mode
              to preserve the original OLE structure as much as possible.
            - If created fresh via `AltiumSchDoc()`, creates a new OLE file
              from scratch with proper FileHeader and Storage streams.

        Args:
            filepath: Output path for .SchDoc file
            debug: Enable debug output

        Returns:
            True if save was successful, False otherwise

        Raises:
            ValueError: If SchDoc doesn't meet minimum requirements (no Sheet)
        """
        # Validate minimum requirements
        if self.sheet is None:
            raise ValueError(
                "SchDoc must have a Sheet record. Use AltiumSchDoc() constructor to auto-create one."
            )

        filepath = Path(filepath)

        # Auto-detect mode: use round-trip if we have an original file
        if self.filepath and Path(self.filepath).exists():
            log.info(f"Saving SchDoc (round-trip mode): {filepath}")
            return self._save_roundtrip(filepath, debug)
        else:
            log.info(f"Creating SchDoc (from scratch): {filepath}")
            return self._create_new(filepath, debug)

    def save(self, filepath: Path | str, debug: bool = False) -> bool:
        """
        Save to binary SchDoc format.

        This is the canonical public write path. Prefer `save()` over
        format-specific helpers such as `to_schdoc()`.

                Args:
                    filepath: Output file path.
                    debug: Enable debug output.

                Returns:
                    True if successful.
        """
        return self.to_schdoc(Path(filepath), debug=debug)

    def _create_new(self, filepath: Path, debug: bool = False) -> bool:
        """
        Create new SchDoc from scratch (internal method).

        Called by to_schdoc() when no original file exists.

        Harness objects (RECORD 215-218) are written to the Additional stream,
        all other objects go to FileHeader stream. This is required for proper
        parent-child linking via OwnerIndexAdditionalList.
        """
        from .altium_ole import AltiumOleWriter

        try:
            self._bind_all_objects_to_context()
            self._sync_embedded_images_from_objects()
            # Recalculate indices before save
            log.info("  Recalculating object indices...")
            self._recalculate_indices()

            ole_writer = AltiumOleWriter()

            fileheader_objects = [
                obj
                for obj in self.all_objects
                if getattr(obj, "_source_stream", "FileHeader") != "Additional"
            ]
            additional_objects = self._build_additional_stream_objects()

            log.info("  Building FileHeader stream...")
            fileheader_data = self._build_fileheader_stream(debug)
            ole_writer.addEntry("FileHeader", data=fileheader_data)

            log.info("  Building Additional stream...")
            additional_data = self._build_additional_stream(debug)
            ole_writer.addEntry("Additional", data=additional_data)

            # Build Storage stream (empty if no embedded images)
            log.info("  Building Storage stream...")
            if self.embedded_images:
                storage_data = self._build_storage_stream(debug)
            else:
                # Minimal empty storage: just header with no Weight field
                # Reference files show: |HEADER=Icon storage (no Weight when empty)
                from .altium_utilities import encode_altium_record

                storage_record = {"HEADER": "Icon storage"}
                storage_data = encode_altium_record(storage_record)
            ole_writer.addEntry("Storage", data=storage_data)

            # Write to output
            ole_writer.write(str(filepath))

            log.info(
                f"  Created successfully: {len(self.all_objects)} objects "
                f"(FileHeader: {len(fileheader_objects)}, Additional: {len(additional_objects)})"
            )
            return True

        except Exception as e:
            log.error(f"  Error creating {filepath.name}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            return False

    def _build_stream_data(
        self,
        objects: list[Any],
        stream_type: str,
        debug: bool = False,
    ) -> bytes:
        """
        Build stream data for either FileHeader or Additional stream.

        Args:
            objects: List of objects to serialize
            stream_type: 'FileHeader' or 'Additional'
            debug: Enable debug output

        Returns:
            Bytes for the stream
        """
        from .altium_utilities import encode_altium_record

        records_data = []

        # Record 0: Stream header
        # Weight calculation:
        # - For round-trip (FileHeader with preserved _file_weight), use original Weight
        # - For new files or Additional stream, count all objects
        if stream_type == "FileHeader" and self._file_weight is not None:
            # Round-trip mode: preserve original Weight (Altium may have specific counting rules)
            weight = self._file_weight
        else:
            # New file or Additional stream: count all objects
            weight = len(objects)

        if stream_type == "FileHeader":
            # FileHeader uses full header with UniqueID
            unique_id = self._file_unique_id or "AAAAAAAA"
            header_record = {
                "HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0",
                "Weight": str(weight),
                "MinorVersion": "13",
                "UniqueID": unique_id,
            }
        else:
            # Additional stream uses simpler header (observed from harness_example.SchDoc)
            header_record = {
                "HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0",
                "Weight": str(weight),
            }
        records_data.append(encode_altium_record(header_record))

        # Remaining records: All objects
        for obj in objects:
            try:
                if hasattr(obj, "serialize_to_record"):
                    # OOP record class
                    record = obj.serialize_to_record()
                    records_data.append(encode_altium_record(record))
                elif isinstance(obj, dict):
                    # Raw dict (component, PIN, etc.)
                    records_data.append(encode_altium_record(obj))
                else:
                    if debug:
                        log.error(f"    Cannot serialize object: {type(obj)}")
            except Exception as e:
                if debug:
                    log.error(f"    Error serializing object: {e}")

        # Combine all records
        return b"".join(records_data)

    def _build_additional_stream_objects(self) -> list[Any]:
        """
        Build the canonical Additional-stream object order from connector-owned state.
        """
        additional_objects: list[Any] = []
        top_level_additional = [
            obj
            for obj in self.all_objects
            if isinstance(obj, (AltiumSchHarnessConnector, AltiumSchSignalHarness))
        ]

        for obj in top_level_additional:
            self._mark_default_source_stream(obj)
            if isinstance(obj, AltiumSchSignalHarness):
                additional_objects.append(obj)
                continue

            connector_index = len(additional_objects)
            additional_objects.append(obj)

            for entry_index, entry in enumerate(list(getattr(obj, "entries", []))):
                self._prepare_harness_connector_child(obj, entry)
                entry.owner_index = connector_index if connector_index > 0 else 0
                entry.owner_index_additional_list = True
                entry.index_in_sheet = -2 if entry_index == 0 else entry_index
                additional_objects.append(entry)

            type_label = getattr(obj, "type_label", None)
            if type_label is not None:
                self._prepare_harness_connector_child(obj, type_label)
                type_label.owner_index = connector_index if connector_index > 0 else 0
                type_label.owner_index_additional_list = True
                type_label.index_in_sheet = -1
                type_label.not_auto_position = True
                additional_objects.append(type_label)

        return additional_objects

    def _build_additional_stream(self, debug: bool = False) -> bytes:
        """
        Build the Additional stream from current harness and signal-harness objects.
        """
        additional_objects = self._build_additional_stream_objects()
        if additional_objects:
            return self._build_stream_data(additional_objects, "Additional", debug)

        from .altium_utilities import encode_altium_record

        additional_header = {
            "HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"
        }
        return encode_altium_record(additional_header)

    def _build_fileheader_stream(self, debug: bool = False) -> bytes:
        """
        Build the FileHeader stream from all_objects.

        Note: This method is used for round-trip mode where the Additional stream
        is preserved from the original file. Only objects originally from FileHeader
        should be included here.

        Returns:
            Bytes for FileHeader stream
        """
        # Exclude objects that came from Additional stream (they're preserved separately)
        fileheader_objects = [
            obj
            for obj in self.all_objects
            if getattr(obj, "_source_stream", "FileHeader") != "Additional"
        ]
        return self._build_stream_data(fileheader_objects, "FileHeader", debug)

    def _build_storage_stream(self, debug: bool = False) -> bytes:
        """
        Build the Storage stream from embedded_images.

        Uses raw storage entries if available (byte-perfect round-trip),
        otherwise falls back to recompressing image data.

        Storage stream format (from Altium .NET source):
        - Header record: 4-byte length + "|HEADER=Icon storage|Weight=N"
        - For each embedded object:
            - 4 bytes: binary header (record_size | 0x01000000)
            - 1 byte: 0xD0 (208) - BINARY marker
            - 1 byte: filename length
            - N bytes: filename (full path, UTF-8)
            - 4 bytes: compressed data length (uint32 LE)
            - M bytes: zlib-compressed image data

        Returns:
            Bytes for Storage stream
        """
        import zlib

        storage_data = bytearray()

        # Header record: |HEADER=Icon storage|Weight=N\x00
        weight = len(self.embedded_images)
        header_text = f"|HEADER=Icon storage|Weight={weight}\x00".encode()
        header_len = struct.pack("<I", len(header_text))
        storage_data.extend(header_len)
        storage_data.extend(header_text)

        # Embedded files - use raw entries if available for byte-perfect round-trip
        for filename, image_data in self.embedded_images.items():
            try:
                # Check if we have the original raw entry (binary_header + compressed data)
                if filename in self._raw_storage_entries:
                    binary_header, compressed_data = self._raw_storage_entries[filename]
                else:
                    # Fallback: compress and calculate proper binary header
                    compressed_data = zlib.compress(image_data)
                    filename_bytes = filename.encode("utf-8")
                    # record_size = 0xD0 (1) + filename_len (1) + filename + compressed_len (4) + compressed
                    record_size = 1 + 1 + len(filename_bytes) + 4 + len(compressed_data)
                    # Binary record header: (record_size | 0x01000000) as uint32 LE
                    binary_header = struct.pack("<I", record_size | 0x01000000)

                # Write binary header
                storage_data.extend(binary_header)

                # 0xD0 (208) - BINARY marker
                storage_data.append(0xD0)

                # Filename length (1-byte pascal string)
                filename_bytes = filename.encode("utf-8")
                if len(filename_bytes) > 255:
                    log.warning(f"Filename too long, truncating: {filename}")
                    filename_bytes = filename_bytes[:255]
                storage_data.append(len(filename_bytes))
                storage_data.extend(filename_bytes)

                # Compressed length
                compressed_len = struct.pack("<I", len(compressed_data))
                storage_data.extend(compressed_len)

                # Compressed data
                storage_data.extend(compressed_data)

            except Exception as e:
                if debug:
                    log.error(f"    Error building storage entry for {filename}: {e}")

        return bytes(storage_data)

    def to_json(self, filepath: Path | str | None = None) -> dict:
        """
        Export SchDoc to JSON format for interoperability testing.

        Creates a JSON structure compatible with native AltiumInterop.JsonTests.
        Format:
            {
                "Header": {
                    "Filename": "schematic.SchDoc",
                    "ObjectCount": N,
                },
                "Sheet": {...},  # Sheet properties
                "Objects": [...]  # All records as JSON
            }

        Args:
            filepath: Optional path to write JSON file.
                     If provided, writes to file and returns dict.
                     If None, just returns the dict.

        Returns:
            JSON-serializable dict of the schematic structure
        """
        import base64
        import json
        import zlib
        from pathlib import Path

        def object_to_json(obj: object, object_index: int) -> dict[str, object] | None:
            """
            Convert an OOP object or raw dict to JSON format.
            """
            # Handle OOP objects with serialize_to_record
            if hasattr(obj, "serialize_to_record"):
                record = obj.serialize_to_record()
            elif isinstance(obj, dict):
                record = obj
            else:
                return None

            # Handle binary records (like PIN)
            if record.get("__BINARY_RECORD__"):
                binary_data = record.get("__BINARY_DATA__", b"")
                if binary_data and len(binary_data) > 0:
                    record_type = binary_data[0]
                    object_type = sch_json_object_type_from_record(
                        {
                            "__BINARY_RECORD__": True,
                            "__BINARY_DATA__": binary_data,
                        }
                    )
                    if object_type is None:
                        object_type_name = f"Unknown_{record_type}"
                    else:
                        object_type_name = object_type.value
                    compressed = zlib.compress(binary_data)
                    encoded = base64.b64encode(compressed).decode("ascii")
                    return {
                        "ObjectType": object_type_name,
                        "ObjectIndex": object_index,
                        "BinaryData": encoded,
                        "_binary_record_type": record_type,
                    }
                return None

            # Normal text record
            record_num = record.get("RECORD")
            if record_num is None:
                # Check for HEADER record
                if "HEADER" in record:
                    return {
                        "ObjectType": SchJsonObjectType.FILE_HEADER.value,
                        "ObjectIndex": object_index,
                        **{k: v for k, v in record.items()},
                    }
                return None

            object_type = sch_json_object_type_from_record(record)
            if object_type is None:
                record_type_int = int(record_num)
                object_type_name = f"Unknown_{record_type_int}"
            else:
                object_type_name = object_type.value

            json_obj = {
                "ObjectType": object_type_name,
                "ObjectIndex": object_index,
            }

            # Convert record fields to JSON
            for key, value in record.items():
                if key == "RECORD":
                    continue

                # Convert boolean strings to booleans
                if value == "T":
                    json_obj[key] = True
                elif value == "F":
                    json_obj[key] = False
                # Try to convert numeric strings to integers (conservative approach)
                # Note: We DON'T convert to float because it loses precision (e.g., '8.00' -> '8.0')
                # and can cause issues with text like part numbers ('603_100' -> '603100.0')
                elif isinstance(value, str):
                    try:
                        stripped = value.lstrip("-")
                        # Integer: all digits, no leading zeros (unless it's just '0')
                        # Leading zeros indicate text like part numbers ('0402')
                        if stripped.isdigit() and (
                            len(stripped) == 1 or not stripped.startswith("0")
                        ):
                            json_obj[key] = int(value)
                        else:
                            # Keep as string - preserves formatting like '8.00', '603_100', '0402'
                            json_obj[key] = value
                    except ValueError:
                        json_obj[key] = value
                else:
                    json_obj[key] = value

            return json_obj

        result = {
            "Header": {
                "Filename": self.filepath.name if self.filepath else "unknown.SchDoc",
                "ObjectCount": len(self.all_objects),
                "ComponentCount": len(self.components),
                "WireCount": len(self.wires),
                "BusCount": len(self.buses),
                "NetLabelCount": len(self.net_labels),
                "PowerPortCount": len(self.power_ports),
                "CrossSheetConnectorCount": len(self.cross_sheet_connectors),
                "JunctionCount": len(self.junctions),
                "PortCount": len(self.ports),
                "SheetSymbolCount": len(self.sheet_symbols),
                "ImageCount": len(self.images),
                "EmbeddedImageCount": len(self.embedded_images),
            },
            "Objects": [],
        }

        # Add font table if present
        if self.sheet and self.font_manager.fonts:
            result["Header"]["FontCount"] = len(self.font_manager.fonts)
            fonts_list = []
            for font_id, font_info in sorted(self.font_manager.fonts.items()):
                font_entry = {
                    "FontID": font_id,
                    "FontName": font_info.get("name", "Unknown"),
                    "FontSize": font_info.get("size", 10),
                }
                # Only include bold/italic if True
                if font_info.get("bold"):
                    font_entry["Bold"] = True
                if font_info.get("italic"):
                    font_entry["Italic"] = True
                fonts_list.append(font_entry)
            result["Header"]["Fonts"] = fonts_list

        # Export all objects in order
        for i, obj in enumerate(self.all_objects):
            json_obj = object_to_json(obj, i)
            if json_obj:
                result["Objects"].append(json_obj)

        # Write to file if path provided
        if filepath is not None:
            filepath = Path(filepath)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            log.info(f"Exported SchDoc to JSON: {filepath}")

        return result

    @classmethod
    def from_json(
        cls,
        source: Path | str | dict,
    ) -> AltiumSchDoc:
        """
        Create a new SchDoc from JSON data.

                This is the public alternate-format ingest path. The returned document
                is reconstructed from the JSON payload without requiring a binary
                template file.
                To mutate an existing binary-backed document or template, instantiate
                ``AltiumSchDoc`` first and then call ``apply_json()``.

                Args:
                    source: JSON file path (Path or str) or parsed dict.

                Returns:
                    AltiumSchDoc instance with data loaded from JSON.
        """
        data = cls._load_json_source(source)
        instance = cls(create_sheet=False)
        instance._load_from_json_document(data)
        return instance

    @staticmethod
    def _load_json_source(source: Path | str | dict) -> dict:
        """
        Load and validate a SchDoc JSON payload.
        """
        import json as json_mod

        if isinstance(source, dict):
            data = source
        else:
            source_path = Path(source)
            with open(source_path, encoding="utf-8") as f:
                data = json_mod.load(f)

        if "Objects" not in data:
            raise ValueError("Invalid JSON format: missing 'Objects' key")

        return data

    def _load_from_json_document(self, data: dict) -> None:
        """
        Rebuild this SchDoc from JSON without requiring a binary template.
        """
        import base64
        import zlib

        self.filepath = None
        self.sheet = None
        self.objects = ObjectCollection()
        self.embedded_images = {}
        self._raw_storage_entries = {}
        self.object_definitions = {}

        header = data.get("Header", {})
        self._file_unique_id = header.get("UniqueID") or header.get("UNIQUEID")
        weight_value = header.get("Weight") or header.get("WEIGHT")
        self._file_weight = int(weight_value) if weight_value is not None else None

        records: list[dict[str, Any]] = []
        for json_obj in data.get("Objects", []):
            object_type = json_obj.get("ObjectType", "")
            if object_type == SchJsonObjectType.FILE_HEADER.value:
                if not self._file_unique_id:
                    self._file_unique_id = json_obj.get("UniqueID")
                if self._file_weight is None and json_obj.get("Weight") is not None:
                    self._file_weight = int(json_obj["Weight"])
                continue

            record_type = sch_record_type_from_json_object_type(object_type)
            if record_type is None:
                log.warning(f"Skipping unknown SchDoc JSON object type: {object_type}")
                continue

            if json_obj.get("BinaryData"):
                try:
                    compressed = base64.b64decode(json_obj["BinaryData"])
                    binary_data = zlib.decompress(compressed)
                except Exception as exc:
                    raise ValueError(
                        f"Invalid binary JSON payload for {object_type}"
                    ) from exc

                records.append(
                    {
                        "RECORD": str(record_type.value),
                        "__BINARY_RECORD__": True,
                        "__BINARY_DATA__": binary_data,
                    }
                )
                continue

            record: dict[str, Any] = {"RECORD": str(record_type.value)}
            for key, value in json_obj.items():
                if key in ("ObjectType", "ObjectIndex", "_binary_record_type"):
                    continue
                if isinstance(value, bool):
                    record[key] = "T" if value else "F"
                else:
                    record[key] = str(value)
            records.append(record)

        self._parse_records(records, source_stream="FileHeader")
        self._build_component_hierarchy()
        self._build_parameterset_hierarchy()
        self._build_implementation_hierarchy()
        self._build_harness_and_sheet_hierarchy()
        self._link_embedded_images()
        self._set_all_parent_references()
        self._preserve_loaded_index_in_sheet = True

    def _update_from_json(self, data: dict) -> None:
        """
        Update this SchDoc's data from a JSON dict.

        Internal object-population step used by ``from_json()`` and
        ``apply_json()`` after source loading and validation.
        """
        import base64
        import zlib

        json_objects = data.get("Objects", [])
        header = data.get("Header", {})
        header_unique_id = header.get("UniqueID") or header.get("UNIQUEID")
        if header_unique_id:
            self._file_unique_id = str(header_unique_id)
        header_weight = header.get("Weight") or header.get("WEIGHT")
        if header_weight is not None:
            self._file_weight = int(header_weight)

        if len(json_objects) != len(self.all_objects):
            log.warning(
                f"Object count mismatch: JSON has {len(json_objects)}, "
                f"template has {len(self.all_objects)}"
            )
            # Still try to update what we can
            json_objects = json_objects[: len(self.all_objects)]

        for i, (json_obj, raw_obj) in enumerate(
            zip(json_objects, self.all_objects, strict=False)
        ):
            # Handle binary data
            if json_obj.get("BinaryData"):
                try:
                    encoded = json_obj["BinaryData"]
                    compressed = base64.b64decode(encoded)
                    binary_data = zlib.decompress(compressed)

                    # Update raw object if it's a dict with binary data
                    if isinstance(raw_obj, dict) and raw_obj.get("__BINARY_RECORD__"):
                        raw_obj["__BINARY_DATA__"] = binary_data
                except Exception as e:
                    log.warning(f"Failed to decode binary data for object {i}: {e}")
            else:
                # Update OOP object or raw dict
                if hasattr(raw_obj, "parse_from_record"):
                    # Convert JSON back to record format
                    record_type = sch_record_type_from_json_object_type(
                        json_obj.get("ObjectType", "")
                    )
                    if record_type is None:
                        raise ValueError(
                            "Unknown schematic JSON object type during apply_json: "
                            f"{json_obj.get('ObjectType', '')!r}"
                        )
                    record = {"RECORD": str(record_type.value)}
                    for key, value in json_obj.items():
                        if key in ("ObjectType", "ObjectIndex"):
                            continue
                        if isinstance(value, bool):
                            record[key] = "T" if value else "F"
                        else:
                            record[key] = str(value)
                    raw_obj.parse_from_record(record)
                elif isinstance(raw_obj, dict):
                    # Update raw dict
                    for key, value in json_obj.items():
                        if key in ("ObjectType", "ObjectIndex"):
                            continue
                        if isinstance(value, bool):
                            raw_obj[key] = "T" if value else "F"
                        else:
                            raw_obj[key] = str(value)
        self._preserve_loaded_index_in_sheet = True

    def _json_object_type_to_record_num(self, object_type: str) -> int:
        """
        Convert ObjectType string back to RECORD number.
        """
        record_type = sch_record_type_from_json_object_type(object_type)
        if record_type is None:
            return 0
        return record_type.value

    def extract_symbols(
        self,
        output_dir: Path,
        combined_schlib: bool = False,
        split_schlibs: bool = True,
        debug: bool = False,
        strip_parameters: bool = True,
        strip_implementations: bool = True,
    ) -> dict[str, bool]:
        """
        Extract symbols from placed components in this schematic.

        This implements Altium's "Make SchLib" functionality - extracting the
        embedded symbol definitions from placed component instances.

        Args:
            output_dir: Directory to save extracted SchLib files
            combined_schlib: If True, create a single multi-symbol SchLib file
                            named after the schematic (e.g., myschematic.SchLib)
            split_schlibs: If True, create individual SchLib files for each symbol
            debug: Enable debug output
            strip_parameters: If True (default), omit component PARAMETER records
                              from the extracted symbols. Use False when the
                              extracted SchLib should preserve source metadata.
            strip_implementations: If True (default), omit implementation/model
                                   records from the extracted symbols. Use False
                                   when the extracted SchLib should preserve
                                   footprint/model links.

        Returns:
            Dict mapping symbol name -> success status

        Notes:
            - Uses DesignItemId (actual part number) over LibReference (symbol name)
              for database library components
            - Emits one symbol per DesignItemId/LibReference. Multiple placed
              components with the same identifier share one extracted symbol even
              if raw Comment expressions, source library metadata, or extra
              parameters differ. Altium's interactive exporter may optionally emit
              suffixed duplicate variants for those cases; this API currently
              treats them as one functional symbol.
            - Handles coordinate translation and rotation un-rotation
            - Preserves multipart symbol structure (PartCount)
            - Embedded images from symbols are extracted to the SchLib files
        """
        from .altium_schdoc_symbol_extractor import extract_symbols_from_schdoc_file
        from .altium_schlib import AltiumSchLib
        from .altium_schlib_merger import merge_directory

        if self.filepath is None:
            raise ValueError("Cannot extract symbols: SchDoc has no filepath")

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Directory for individual SchLib files
        if split_schlibs:
            split_dir = output_dir
        else:
            # Create temporary directory for intermediate files if only combined is needed
            split_dir = output_dir / "_temp_split"
            split_dir.mkdir(exist_ok=True)

        # Extract to individual SchLib files using existing extractor
        log.info(f"Extracting symbols from: {self.filepath.name}")
        results = extract_symbols_from_schdoc_file(
            self.filepath,
            split_dir,
            debug=debug,
            strip_parameters=strip_parameters,
            strip_implementations=strip_implementations,
        )
        successful = sum(1 for value in results.values() if value)

        # Create combined SchLib if requested
        if combined_schlib:
            # Name after the schematic file
            combined_name = self.filepath.stem + ".SchLib"
            combined_path = output_dir / combined_name

            if successful == 0:
                log.info(f"Creating empty combined SchLib: {combined_name}")
                AltiumSchLib().save(combined_path)
            else:
                log.info(f"Creating combined SchLib: {combined_name}")
                success = merge_directory(
                    split_dir,
                    combined_path,
                    pattern="*.SchLib",
                    handle_conflicts="skip",  # Skip duplicates in combined file
                    verbose=debug,
                )
                if not success:
                    log.warning(f"Failed to create combined SchLib: {combined_name}")

        # Clean up temp directory if we created one
        if not split_schlibs and split_dir.exists():
            import shutil

            shutil.rmtree(split_dir)

        return results

    # Clean SchDoc API - user-friendly access methods

    # Component access

    def get_components(self) -> list[SchComponentInfo]:
        """
        Get all components with resolved children.

                Returns:
                    List of SchComponentInfo wrappers with designator, pins,
                    parameters, and footprint pre-resolved.
        """
        return [SchComponentInfo(record=comp) for comp in self.components]

    def get_component(self, designator: str) -> SchComponentInfo | None:
        """
        Find component by designator.

                Args:
                    designator: Component designator (e.g., "U1", "R1")

                Returns:
                    SchComponentInfo if found, None otherwise.
        """
        for comp in self.get_components():
            if comp.designator == designator:
                return comp
        return None

    # Pin access

    def get_all_pins(self) -> list[SchPinInfo]:
        """
        Get all pins across all components with component context.

                Returns:
                    List of SchPinInfo with pin data and parent component context.
        """
        result = []
        for comp_info in self.get_components():
            for pin in comp_info.pins:
                result.append(SchPinInfo(pin=pin, component=comp_info))
        return result

    def get_pins_for_component(self, designator: str) -> list[AltiumSchPin]:
        """
        Get pins for a specific component.

                Args:
                    designator: Component designator

                Returns:
                    List of AltiumSchPin records, empty if component not found.
        """
        comp = self.get_component(designator)
        return comp.pins if comp else []

    # Connectivity objects

    def get_wires(self) -> list[AltiumSchWire]:
        """
        Get all wire segments.
        """
        return list(self.wires)

    def get_net_labels(self) -> list[SchNetLabelInfo]:
        """
        Get all net labels with connection points.
        """
        return [SchNetLabelInfo(record=nl) for nl in self.net_labels]

    def get_power_ports(self) -> list[SchPowerPortInfo]:
        """
        Get all power port symbols with connection points.
        """
        return [SchPowerPortInfo(record=pp) for pp in self.power_ports]

    def get_cross_sheet_connectors(self) -> list[SchCrossSheetConnectorInfo]:
        """
        Get all off-sheet connectors with connection points.
        """
        return [
            SchCrossSheetConnectorInfo(record=connector)
            for connector in self.cross_sheet_connectors
        ]

    def get_ports(self) -> list[SchPortInfo]:
        """
        Get all sheet ports with connection points.
        """
        return [SchPortInfo(record=p) for p in self.ports]

    def get_junctions(self) -> list[AltiumSchJunction]:
        """
        Get all junction points.
        """
        return list(self.junctions)

    def get_buses(self) -> list[AltiumSchBus]:
        """
        Get all buses.
        """
        return list(self.buses)

    # Harness objects

    def get_harness_connectors(self) -> list[SchHarnessInfo]:
        """
        Get all harness connectors with their entries.
        """
        result = []
        for hc in self.harness_connectors:
            info = SchHarnessInfo(record=hc, entries=list(getattr(hc, "entries", [])))
            result.append(info)
        return result

    def get_signal_harnesses(self) -> list[AltiumSchSignalHarness]:
        """
        Get all signal harness wires.
        """
        return list(self.signal_harnesses)

    # -----------------------------------------------------------------
    # Hierarchy Objects
    # -----------------------------------------------------------------

    def get_sheet_symbols(self) -> list[SchSheetSymbolInfo]:
        """
        Get all sheet symbols with resolved entries.
        """
        result = []
        for ss in self.sheet_symbols:
            info = SchSheetSymbolInfo(
                record=ss, entries=list(getattr(ss, "entries", []))
            )
            result.append(info)
        return result

    # -----------------------------------------------------------------
    # Graphics Objects
    # -----------------------------------------------------------------

    def get_labels(self) -> list[AltiumSchLabel]:
        """
        Get all text labels (sheet-level only).
        """
        return list(self.labels)

    def get_rectangles(self) -> list[AltiumSchRectangle]:
        """
        Get all rectangles (sheet-level).
        """
        return [obj for obj in self.graphics if isinstance(obj, AltiumSchRectangle)]

    def get_lines(self) -> list[AltiumSchLine]:
        """
        Get all lines (sheet-level).
        """
        return [obj for obj in self.graphics if isinstance(obj, AltiumSchLine)]

    def __repr__(self) -> str:
        return f"<AltiumSchDoc {self.filepath.name if self.filepath else 'Unknown'}: {len(self.all_objects)} objects>"
