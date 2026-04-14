"""
Read Altium integrated libraries and extract their embedded source files.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, replace
from pathlib import Path

from .altium_ole import AltiumOleFile

_COMPRESSED_PREFIX = 0x02
_ZLIB_PREFIX = b"\x78"
_SOURCE_STREAM_ROOTS = frozenset({"SchLib", "PCBLib", "PCB3DLib"})
_METADATA_STREAMS = frozenset({"Version.Txt", "LibCrossRef.Txt", "Parameters   .bin"})


@dataclass(frozen=True)
class IntLibModel:
    """
    Model entry referenced by one component in an integrated library.

    Attributes:
        name: Model name stored in `LibCrossRef.Txt`.
        model_type: Native model type label such as `PCBLIB` or `SI`.
        virtual_path: Integrated-library stream path, or `None` for models
            stored only as component metadata.
        source_path: Original source path captured by Altium at compile time,
            or `None` when the model has no embedded source stream.
    """

    name: str
    model_type: str
    virtual_path: str | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class IntLibComponent:
    """
    Component entry referenced by an integrated library.

    Attributes:
        name: Component library reference.
        virtual_path: Integrated-library schematic source stream path.
        description: Component description from `LibCrossRef.Txt`.
        source_path: Original source path captured by Altium at compile time.
        models: Model entries associated with this component.
    """

    name: str
    virtual_path: str
    description: str
    source_path: str
    models: tuple[IntLibModel, ...]


@dataclass(frozen=True)
class IntLibSource:
    """
    One extractable source stream inside an integrated library.

    Attributes:
        kind: Top-level source family, for example `SchLib`, `PCBLib`, or
            `PCB3DLib`.
        stream_path: OLE stream path using `/` separators.
        original_path: Original path recorded by Altium, if present.
        suggested_filename: Safe output filename inferred from the original
            path or stream name.
        output_path: File path written by `AltiumIntLib.extract_sources`, or
            `None` before extraction.
    """

    kind: str
    stream_path: str
    original_path: str | None
    suggested_filename: str
    output_path: Path | None = None


@dataclass(frozen=True)
class IntLibExtractionResult:
    """
    Result returned by `AltiumIntLib.extract_sources`.

    Attributes:
        output_dir: Directory that received the extracted source files.
        sources: Extracted source files with their output paths populated.
        libpkg_path: Generated LibPkg path when requested, otherwise `None`.
    """

    output_dir: Path
    sources: tuple[IntLibSource, ...]
    libpkg_path: Path | None


class _BinaryCursor:
    """Small bounds-checked cursor for IntLib metadata streams."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.position = 0

    def read_u32(self) -> int:
        if self.position + 4 > len(self._data):
            raise ValueError("Unexpected end of IntLib cross-reference stream")
        value = int.from_bytes(self._data[self.position : self.position + 4], "little")
        self.position += 4
        return value

    def read_altium_string(self) -> str:
        byte_count = self.read_u32()
        payload = self._read_bytes(byte_count)
        if not payload:
            return ""
        text_length = payload[0]
        text_bytes = payload[1 : 1 + text_length]
        return _decode_metadata_string(text_bytes)

    def _read_bytes(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self._data):
            raise ValueError("Unexpected end of IntLib cross-reference stream")
        value = self._data[self.position : self.position + size]
        self.position += size
        return value


class AltiumIntLib:
    """
    Read an Altium `.IntLib` and extract the source libraries it contains.

    The current API is intentionally read-only. It exposes component/model
    metadata and can recover embedded SchLib, PcbLib, and PCB3DLib source
    streams to normal files.
    """

    def __init__(self, filepath: str | Path) -> None:
        """
        Open and parse an integrated library file.

        Args:
            filepath: Path to the `.IntLib` file.
        """
        self.filepath = Path(filepath)
        self._ole = AltiumOleFile(str(self.filepath))
        self._components = self._parse_components()

    @classmethod
    def from_file(cls, filepath: str | Path) -> "AltiumIntLib":
        """
        Open an integrated library from disk.

        Args:
            filepath: Path to the `.IntLib` file.

        Returns:
            Parsed `AltiumIntLib` instance.
        """
        return cls(filepath)

    @property
    def components(self) -> tuple[IntLibComponent, ...]:
        """
        Component records parsed from `LibCrossRef.Txt`.
        """
        return self._components

    @property
    def stream_paths(self) -> tuple[str, ...]:
        """
        OLE stream paths in the integrated library using `/` separators.
        """
        return tuple("/".join(parts) for parts in self._ole.listdir(streams=True))

    def get_source_entries(self) -> tuple[IntLibSource, ...]:
        """
        Return unique embedded source streams that can be extracted.

        The returned entries are deduplicated by stream path. Original Altium
        source paths are used only to infer friendly basenames; source
        directories are never reused.
        """
        sources: dict[str, IntLibSource] = {}
        for component in self.components:
            self._add_source_entry(
                sources,
                component.virtual_path,
                component.source_path,
            )
            for model in component.models:
                self._add_source_entry(sources, model.virtual_path, model.source_path)

        for stream_path in self.stream_paths:
            if _is_source_stream_path(stream_path):
                self._add_source_entry(sources, stream_path, None)

        return tuple(sources.values())

    def read_stream(self, stream_path: str, *, decompress: bool = True) -> bytes:
        """
        Read one OLE stream from the integrated library.

        Args:
            stream_path: OLE stream path. Both native IntLib virtual paths such
                as `:\\SchLib\\0.schlib` and normalized OLE paths such as
                `SchLib/0.schlib` are accepted.
            decompress: When true, remove Altium's IntLib compression wrapper
                from compressed streams.

        Returns:
            Stream bytes. Source streams are returned as normal SchLib/PcbLib
            file bytes when `decompress` is true.
        """
        ole_path = _normalize_virtual_path(stream_path)
        data = self._ole.openstream(ole_path)
        if not decompress:
            return data
        return _decompress_intlib_stream(data)

    def extract_sources(
        self,
        output_dir: str | Path,
        *,
        overwrite: bool = True,
        use_original_filenames: bool = True,
        write_libpkg: bool = True,
    ) -> IntLibExtractionResult:
        """
        Extract embedded source streams from the integrated library.

        Args:
            output_dir: Destination directory.
            overwrite: Replace existing files when true.
            use_original_filenames: Use original source basenames recorded by
                Altium when available. When false, stream basenames such as
                `0.schlib` are used.
            write_libpkg: Also write a simple `.LibPkg` that references the
                extracted source files with relative paths.

        Returns:
            `IntLibExtractionResult` describing the extracted files.
        """
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)

        extracted = self._extract_source_files(
            destination,
            overwrite=overwrite,
            use_original_filenames=use_original_filenames,
        )
        libpkg_path = None
        if write_libpkg:
            libpkg_path = self._write_libpkg(destination, extracted)

        return IntLibExtractionResult(
            output_dir=destination,
            sources=tuple(extracted),
            libpkg_path=libpkg_path,
        )

    def close(self) -> None:
        """Release the parsed OLE container state."""
        self._ole.close()

    def __enter__(self) -> "AltiumIntLib":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _parse_components(self) -> tuple[IntLibComponent, ...]:
        if not self._ole.exists("LibCrossRef.Txt"):
            return ()

        data = self.read_stream("LibCrossRef.Txt")
        cursor = _BinaryCursor(data)
        component_count = cursor.read_u32()
        components = [self._parse_component(cursor) for _ in range(component_count)]
        if cursor.position != len(data):
            raise ValueError("IntLib cross-reference stream has trailing data")
        return tuple(components)

    def _parse_component(self, cursor: _BinaryCursor) -> IntLibComponent:
        name = cursor.read_altium_string()
        virtual_path = cursor.read_altium_string()
        cursor.read_u32()
        description = cursor.read_altium_string()
        source_path = cursor.read_altium_string()
        models = tuple(self._parse_model(cursor) for _ in range(cursor.read_u32()))
        return IntLibComponent(
            name=name,
            virtual_path=virtual_path,
            description=description,
            source_path=source_path,
            models=models,
        )

    def _parse_model(self, cursor: _BinaryCursor) -> IntLibModel:
        name = cursor.read_altium_string()
        model_type = cursor.read_altium_string()
        has_source_stream = cursor.read_u32() != 0
        if not has_source_stream:
            return IntLibModel(name=name, model_type=model_type)

        return IntLibModel(
            name=name,
            model_type=model_type,
            virtual_path=cursor.read_altium_string(),
            source_path=cursor.read_altium_string(),
        )

    def _add_source_entry(
        self,
        sources: dict[str, IntLibSource],
        virtual_path: str | None,
        original_path: str | None,
    ) -> None:
        if not virtual_path:
            return
        stream_path = _normalize_virtual_path(virtual_path)
        if stream_path in sources or not _is_source_stream_path(stream_path):
            return
        kind = stream_path.split("/", 1)[0]
        sources[stream_path] = IntLibSource(
            kind=kind,
            stream_path=stream_path,
            original_path=original_path,
            suggested_filename=_suggest_filename(stream_path, original_path),
        )

    def _extract_source_files(
        self,
        output_dir: Path,
        *,
        overwrite: bool,
        use_original_filenames: bool,
    ) -> list[IntLibSource]:
        extracted: list[IntLibSource] = []
        used_by_kind: dict[str, set[str]] = {}
        for source in self.get_source_entries():
            kind_dir = output_dir / source.kind
            kind_dir.mkdir(parents=True, exist_ok=True)
            filename = _choose_output_filename(source, use_original_filenames)
            filename = _dedupe_filename(
                filename, used_by_kind.setdefault(source.kind, set())
            )
            output_path = kind_dir / filename
            if output_path.exists() and not overwrite:
                raise FileExistsError(output_path)
            output_path.write_bytes(self.read_stream(source.stream_path))
            extracted.append(
                replace(source, suggested_filename=filename, output_path=output_path)
            )
        return extracted

    def _write_libpkg(self, output_dir: Path, sources: list[IntLibSource]) -> Path:
        libpkg_path = output_dir / f"{self.filepath.stem}.LibPkg"
        lines = [
            "[Design]",
            "Version=1.0",
            "HierarchyMode=0",
            "OutputPath=Project Outputs",
            "",
        ]
        for index, source in enumerate(sources, start=1):
            if source.output_path is None:
                continue
            relative_path = source.output_path.relative_to(output_dir)
            document_path = "\\".join(relative_path.parts)
            lines.extend(
                [
                    f"[Document{index}]",
                    f"DocumentPath={document_path}",
                    "DocumentUniqueId=",
                    "",
                ]
            )
        libpkg_path.write_text("\n".join(lines), encoding="utf-8")
        return libpkg_path


def _normalize_virtual_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith(":/"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _is_source_stream_path(path: str) -> bool:
    if path in _METADATA_STREAMS:
        return False
    root, _, name = path.partition("/")
    return bool(name) and root in _SOURCE_STREAM_ROOTS


def _decompress_intlib_stream(data: bytes) -> bytes:
    if len(data) >= 3 and data[0] == _COMPRESSED_PREFIX and data[1:2] == _ZLIB_PREFIX:
        return zlib.decompress(data[1:])
    return data


def _decode_metadata_string(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _suggest_filename(stream_path: str, original_path: str | None) -> str:
    if original_path:
        basename = _portable_basename(original_path)
        if basename:
            return _safe_filename(basename)
    return _safe_filename(stream_path.rsplit("/", 1)[-1])


def _portable_basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _safe_filename(filename: str) -> str:
    cleaned = "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in filename)
    return cleaned or "source.bin"


def _choose_output_filename(source: IntLibSource, use_original_filenames: bool) -> str:
    if use_original_filenames:
        return source.suggested_filename
    return _safe_filename(source.stream_path.rsplit("/", 1)[-1])


def _dedupe_filename(filename: str, used: set[str]) -> str:
    path = Path(filename)
    candidate = filename
    index = 2
    while candidate.casefold() in used:
        candidate = f"{path.stem}_{index}{path.suffix}"
        index += 1
    used.add(candidate.casefold())
    return candidate


__all__ = [
    "AltiumIntLib",
    "IntLibComponent",
    "IntLibExtractionResult",
    "IntLibModel",
    "IntLibSource",
]
