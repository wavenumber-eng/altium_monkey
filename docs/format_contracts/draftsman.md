# Draftsman Contract

altium-monkey provides an experimental Python API for Draftsman `.PCBDwf`
documents. The current contract is intentionally narrow: supported fields are
typed, unsupported XML subtrees are preserved, and the public API may change as
additional Draftsman object families are modeled.

## Container Contract

- Raw XML Draftsman files are supported for load and save.
- Legacy LZ4-compressed Draftsman files are supported for load.
- Saving currently writes raw XML containers. The `compression="lz4"` option is
  reserved and raises `NotImplementedError`.
- `compression="preserve"` preserves raw XML inputs as raw XML outputs. For LZ4
  inputs, it currently writes raw XML so edited files remain Altium-openable.

## XML And Object Model

- `AltiumDraftsmanDocument` owns the XML tree.
- `AltiumDraftsmanPage`, `AltiumDraftsmanItem`,
  `AltiumDraftsmanNote`, `AltiumDraftsmanText`, and
  `AltiumDraftsmanPicture` are live XML-backed wrappers.
- `document.pages`, `page.items`, `document.notes`, `document.texts`, and
  `document.pictures` return query collections. Structural mutation goes
  through methods such as `page.add_note(...)`, `page.add_text(...)`,
  `page.add_picture(...)`, and note row helpers.
- Unknown page items and unsupported document-level blocks stay in the XML tree
  and are written back unchanged unless callers edit the underlying XML.

## Geometry And Units

- Public geometry value objects use millimeters.
- Draftsman serializes drawing coordinates as 96 drawing points per inch.
- Page coordinates use a lower-left origin. X increases to the right and Y
  increases upward.
- `page.point_from_top_left(...)` converts visual upper-left offsets into the
  serialized lower-left coordinate system.
- `page.rect_centered(...)` returns a centered `DraftsmanRect` for page-level
  placement.

## Styles And Fonts

- Document font styles are stored as family name, size, and decoration flags.
- `.PCBDwf` font-style records reference installed font families; they do not
  contain embedded font bytes.
- Explicit text and note font-style helper calls also clear the relevant
  `UseDocumentFont*` flag so Altium uses the requested style id.
- `DraftsmanFontDecoration` uses the observed bit flags:
  italic `1`, bold `2`, underline `4`, and strikeout `8`.

## Supported Authored Items

- Notes can be created with titles, rows, row text, row border styles, width,
  start point, font styles, and text color.
- Text items can be created with rectangle, text, font style, text color,
  alignment, clipping, rotation, and fill-style id.
- Picture items can be created from embedded image bytes with rectangle,
  maintain-aspect-ratio, and rotation.
- PNG payloads are written to the legacy `Bitmap` field. Non-PNG bitmap
  payloads are written to `Bitmap2` with a small PNG placeholder in `Bitmap`.

## Compatibility Boundaries

- The default blank profile is an AD25-style template because it opens across
  the supported Altium versions tested so far.
- Board fabrication views, assembly views, dimensions, callouts, tables,
  title-block templates, and board-derived cache payloads are preserved but not
  fully typed by this initial API.
- Full Draftsman rendering is outside this contract.

## Test Gates

The Draftsman contract is protected by focused document tests, executable
public examples, documentation synchronization checks, and release validation.
Manual Altium open/save checks are used for generated samples before expanding
the authored object surface.
