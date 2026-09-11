"""Schematic SVG bundled-font attachment: embed, omit, and sidecar files."""

from __future__ import annotations

from pathlib import Path

import pytest

from altium_monkey import (
    AltiumSchDoc,
    SchFontSpec,
    SchPointMils,
    SchSvgFontOutput,
    SchSvgRenderOptions,
    make_sch_text_string,
)
from altium_monkey.altium_font_resolver import (
    clear_font_resolution_result_cache,
    get_bundled_font_search_dirs,
)
from altium_monkey.altium_sch_geometry_oracle import (
    SchGeometryDocument,
    SchGeometryOp,
    SchGeometryRecord,
    make_font_payload,
    make_solid_brush,
)
from altium_monkey.altium_sch_geometry_renderer import (
    SchGeometrySvgRenderOptions,
    SchGeometrySvgRenderer,
    _normalize_font_url_prefix,
)
from altium_monkey.altium_sch_svg_renderer import normalize_sch_svg_font_output


@pytest.fixture
def force_bundled_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTIUM_FONT_DISABLE_SYSTEM", "1")
    monkeypatch.delenv("ALTIUM_FONT_DIRS", raising=False)
    clear_font_resolution_result_cache()
    yield
    clear_font_resolution_result_cache()


def _text_document(*families: str) -> SchGeometryDocument:
    operations = [
        SchGeometryOp.string(
            x=64.0,
            y=64.0,
            text=family,
            font=make_font_payload(name=family, size_px=10.0),
            brush=make_solid_brush(0),
        )
        for family in families
    ]
    return SchGeometryDocument(
        records=[
            SchGeometryRecord(
                handle="1",
                unique_id="FONTTEST",
                kind="label",
                object_id="FONTTEST",
                operations=operations,
            )
        ],
        canvas={"width_px": 200, "height_px": 100},
        coordinate_space={"units_per_px": 64},
        document_id="FONTTEST",
        workspace_background_color="#FFFFFF",
    )


def _render(document: SchGeometryDocument, **kwargs: object) -> str:
    return SchGeometrySvgRenderer(SchGeometrySvgRenderOptions(**kwargs)).render(
        document
    )


def test_normalize_font_output_labels() -> None:
    assert normalize_sch_svg_font_output(None) is SchSvgFontOutput.EMBED
    assert normalize_sch_svg_font_output("OMIT") is SchSvgFontOutput.OMIT
    assert normalize_sch_svg_font_output("files") is SchSvgFontOutput.FILES
    with pytest.raises(ValueError, match="embed, omit, files"):
        normalize_sch_svg_font_output("base64")


def test_default_embed_inlines_bundled_faces(
    force_bundled_fallbacks: None,
) -> None:
    svg = _render(_text_document("Arial", "Times New Roman", "Courier New"))
    assert "data:font/ttf;base64," in svg
    assert svg.count("@font-face") == 3
    assert "font-family: \"Arimo\"" in svg
    assert "font-family: \"Tinos\"" in svg
    assert "font-family: \"Cousine\"" in svg


def test_omit_skips_font_face_rules(force_bundled_fallbacks: None) -> None:
    svg = _render(
        _text_document("Arial"),
        font_output=SchSvgFontOutput.OMIT,
    )
    assert "@font-face" not in svg
    assert "data:font/ttf;base64," not in svg
    assert 'font-family="Arimo"' in svg


def test_files_copies_faces_with_relative_urls(
    force_bundled_fallbacks: None,
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    svg = _render(
        _text_document("Arial", "Times New Roman", "Courier New"),
        font_output="files",
        font_output_dir=font_dir,
        font_url_prefix="fonts/",
    )
    assert "data:font/ttf;base64," not in svg
    assert "C:/" not in svg
    assert "file:" not in svg
    copied = sorted(path.name for path in font_dir.glob("*.ttf"))
    assert copied == [
        "Arimo-Regular.ttf",
        "Cousine-Regular.ttf",
        "Tinos-Regular.ttf",
    ]
    assert 'url("fonts/Arimo-Regular.ttf")' in svg
    assert 'url("fonts/Tinos-Regular.ttf")' in svg
    assert 'url("fonts/Cousine-Regular.ttf")' in svg
    arimo_source = next(
        path / "Arimo-Regular.ttf"
        for path in get_bundled_font_search_dirs()
        if (path / "Arimo-Regular.ttf").is_file()
    )
    assert (font_dir / "Arimo-Regular.ttf").read_bytes() == arimo_source.read_bytes()


def test_files_requires_output_dir(force_bundled_fallbacks: None) -> None:
    with pytest.raises(ValueError, match="font_output_dir"):
        _render(_text_document("Arial"), font_output=SchSvgFontOutput.FILES)


@pytest.mark.parametrize("blank_dir", ["", "   "])
def test_files_rejects_blank_output_dir(
    force_bundled_fallbacks: None,
    blank_dir: str,
) -> None:
    with pytest.raises(ValueError, match="font_output_dir"):
        _render(
            _text_document("Arial"),
            font_output=SchSvgFontOutput.FILES,
            font_output_dir=blank_dir,
        )


def test_files_empty_url_prefix_uses_basename(
    force_bundled_fallbacks: None,
    tmp_path: Path,
) -> None:
    svg = _render(
        _text_document("Arial"),
        font_output=SchSvgFontOutput.FILES,
        font_output_dir=tmp_path,
        font_url_prefix="",
    )
    assert 'url("Arimo-Regular.ttf")' in svg
    assert "C:/" not in svg
    assert "file:" not in svg
    assert (tmp_path / "Arimo-Regular.ttf").is_file()


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        ("", ""),
        ("fonts", "fonts/"),
        ("fonts/", "fonts/"),
    ],
)
def test_normalize_font_url_prefix_accepts_relative(prefix: str, expected: str) -> None:
    assert _normalize_font_url_prefix(prefix) == expected


@pytest.mark.parametrize(
    "prefix",
    ["C:/fonts", "/fonts", "file:fonts", "https://example.com/fonts", "..", "fonts/../x"],
)
def test_normalize_font_url_prefix_rejects_unsafe(prefix: str) -> None:
    with pytest.raises(ValueError, match="relative URL"):
        _normalize_font_url_prefix(prefix)


def test_files_rejects_absolute_url_prefix(
    force_bundled_fallbacks: None,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="relative URL"):
        _render(
            _text_document("Arial"),
            font_output=SchSvgFontOutput.FILES,
            font_output_dir=tmp_path,
            font_url_prefix="C:/fonts",
        )


def test_public_render_options_accept_font_output() -> None:
    options = SchSvgRenderOptions(font_output="omit")
    assert normalize_sch_svg_font_output(options.font_output) is SchSvgFontOutput.OMIT


def test_schdoc_to_svg_forwards_omit(
    force_bundled_fallbacks: None,
) -> None:
    schdoc = AltiumSchDoc()
    schdoc.add_object(
        make_sch_text_string(
            location_mils=SchPointMils.from_mils(1000, 1000),
            text="Arial label",
            font=SchFontSpec(name="Arial", size=10),
        )
    )
    svg = schdoc.to_svg(
        include_border=False,
        options=SchSvgRenderOptions(font_output="omit"),
    )
    assert "@font-face" not in svg
    assert "data:font/ttf;base64," not in svg
    assert 'font-family="Arimo"' in svg
