"""
Write Altium `.PrjScr` script project files.
"""

import logging
from pathlib import Path

from .altium_api_markers import public_api

log = logging.getLogger(__name__)

_DESIGN_SECTION = """\
[Design]
Version=1.0
HierarchyMode=0
ChannelRoomNamingStyle=0
OutputPath=
ChannelDesignatorFormatString=$Component_$RoomName
ChannelRoomLevelSeperator=_
ReleasesFolder=
AddToQueue=FALSE
OpenOutputs=FALSE
ManagedPrjGUID=
TemplateVaultGUID=
"""

_DOCUMENT_SECTION = """\
[Document{index}]
DocumentPath={filename}
AnnotationEnabled=1
AnnotateStartValue=1
AnnotationIndexControlEnabled=0
AnnotateSuffix=
AnnotateScope=All
AnnotateOrder=-1
DoLibraryUpdate=1
DoDatabaseUpdate=1
ClassGenCCAutoEnabled=1
ClassGenCCAutoRoomEnabled=1
ClassGenNCAutoScope=None
DItemRevisionGUID=
GenerateClassCluster=0
DocumentUniqueId=
"""


@public_api
class AltiumPrjScr:
    """
    Writer for Altium `.PrjScr` script project files.
    """

    def __init__(self) -> None:
        self._scripts: list[str] = []

    @classmethod
    def create(cls, script_filename: str) -> "AltiumPrjScr":
        """
        Create a script project with one `.pas` document.
        """
        prjscr = cls()
        prjscr.add_script(script_filename)
        return prjscr

    @classmethod
    def create_multi(cls, script_filenames: list[str]) -> "AltiumPrjScr":
        """
        Create a script project with multiple `.pas` documents.
        """
        prjscr = cls()
        for name in script_filenames:
            prjscr.add_script(name)
        return prjscr

    def add_script(self, filename: str) -> None:
        """
        Add one `.pas` file to the project.
        """
        self._scripts.append(filename)

    def save(self, filepath: Path | str) -> None:
        """
        Save the script project to disk.

        This is the canonical public write path for PrjScr files.
        """
        filepath = Path(filepath)
        parts = [_DESIGN_SECTION]
        for idx, filename in enumerate(self._scripts, start=1):
            parts.append(_DOCUMENT_SECTION.format(index=idx, filename=filename))
        filepath.write_text("".join(parts), encoding="utf-8")
        log.info("Generated: %s", filepath)

    def to_string(self) -> str:
        """
        Return the serialized project content.
        """
        parts = [_DESIGN_SECTION]
        for idx, filename in enumerate(self._scripts, start=1):
            parts.append(_DOCUMENT_SECTION.format(index=idx, filename=filename))
        return "".join(parts)
