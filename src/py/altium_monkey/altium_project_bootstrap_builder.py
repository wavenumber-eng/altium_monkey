"""
High-level project bootstrap builder.

This composes the existing SchDoc, PcbDoc, and PrjPcb builders into one
minimal "create a blank Altium project skeleton" API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .altium_api_markers import public_api

from .altium_pcbdoc_builder import PcbDocBuilder
from .altium_pcbdoc_layer_stack_builder import PcbDocLayerStackTemplate
from .altium_prjpcb_builder import AltiumPrjPcbBuilder
from .altium_schdoc import AltiumSchDoc


@dataclass(frozen=True, slots=True)
class ProjectBootstrapPaths:
    """
    Materialized output paths for a bootstrapped project.
    """

    output_dir: Path
    project_path: Path
    schematic_path: Path
    board_path: Path


@public_api
class ProjectBootstrapBuilder:
    """
    Create a minimal one-sheet, one-board Altium project skeleton.
    """

    def __init__(
        self,
        project_name: str = "project",
        *,
        schematic_filename: str | None = None,
        board_filename: str | None = None,
        project_filename: str | None = None,
        layer_stack_template: str | PcbDocLayerStackTemplate = "2-layer",
    ) -> None:
        self.project_name = project_name
        self.schematic_filename = schematic_filename or f"{project_name}.SchDoc"
        self.board_filename = board_filename or f"{project_name}.PcbDoc"
        self.project_filename = project_filename or f"{project_name}.PrjPcb"

        self.schematic_builder = AltiumSchDoc()
        self.board_builder = PcbDocBuilder()
        self.project_builder = AltiumPrjPcbBuilder(project_name)

        self.board_builder.set_layer_stack_template(layer_stack_template)

    @public_api
    def set_board_outline_rectangle_mils(
        self,
        left_mils: float,
        bottom_mils: float,
        right_mils: float,
        top_mils: float,
    ) -> "ProjectBootstrapBuilder":
        """
        Set the board outline rectangle in mils.
        """
        self.board_builder.set_outline_rectangle_mils(
            left_mils,
            bottom_mils,
            right_mils,
            top_mils,
        )
        return self

    @public_api
    def set_board_layer_stack_template(
        self,
        template: str | PcbDocLayerStackTemplate,
    ) -> "ProjectBootstrapBuilder":
        """
        Select the layer-stack template used for the generated board.
        """
        self.board_builder.set_layer_stack_template(template)
        return self

    @public_api
    def set_board_sheet_frame_mils(
        self,
        x_mils: float,
        y_mils: float,
        width_mils: float,
        height_mils: float,
    ) -> "ProjectBootstrapBuilder":
        """
        Set the initial board sheet frame rectangle in mils.
        """
        self.board_builder.set_sheet_frame_mils(
            x_mils,
            y_mils,
            width_mils,
            height_mils,
        )
        return self

    def _build_project_wrapper(self) -> AltiumPrjPcbBuilder:
        project = AltiumPrjPcbBuilder(
            self.project_name,
            net_identifier_scope=self.project_builder.net_identifier_scope,
            open_outputs=self.project_builder.open_outputs,
        )
        project.extend_documents(self.project_builder.documents)

        existing_paths = {entry.path.lower() for entry in project.documents}
        schematic_path = self.schematic_filename.replace("/", "\\")
        board_path = self.board_filename.replace("/", "\\")
        if schematic_path.lower() not in existing_paths:
            project.add_schdoc(schematic_path)
        if board_path.lower() not in existing_paths:
            project.add_pcbdoc(board_path)
        return project

    @public_api
    def save(self, output_dir: Path | str) -> ProjectBootstrapPaths:
        """
        Write the schematic, board, and project files into `output_dir`.

        This is the canonical public write path for generated project bundles.
        """
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)

        schematic_path = output_root / self.schematic_filename
        board_path = output_root / self.board_filename
        project_path = output_root / self.project_filename

        schematic_path.parent.mkdir(parents=True, exist_ok=True)
        board_path.parent.mkdir(parents=True, exist_ok=True)
        project_path.parent.mkdir(parents=True, exist_ok=True)

        # Policy defaults for bootstrap boards:
        # - origin at lower-left corner of outline
        # - default board view should open in 2D Top Layer
        self.board_builder.set_origin_to_outline_lower_left()
        self.board_builder.set_current_view_state("2D")
        self.board_builder.set_2d_current_layer("TOP")

        self.schematic_builder.save(schematic_path)
        self.board_builder.save(board_path)
        self._build_project_wrapper().save(project_path)

        return ProjectBootstrapPaths(
            output_dir=output_root,
            project_path=project_path,
            schematic_path=schematic_path,
            board_path=board_path,
        )
