# Contributing to Altium Monkey

Thanks for your interest in improving Altium Monkey.

This repository is a published mirror for a project developed in a separate
upstream workspace. The project uses additional private validation
infrastructure and a large file corpus that is not included in this repository.

Because of that, pull requests are welcome as proposals, but they may not be
merged directly as submitted. A PR can still be very useful when it shows the
intended behavior, expected API shape, a failing case, or a possible
implementation strategy. Accepted changes may be adapted or rewritten in the
upstream source tree, validated there, and mirrored back to this repository in a
later package version.

## What Helps

The most useful contributions are:

1. clear bug reports with the smallest reproducible case
2. public test files that can be committed or referenced safely
3. focused API feedback with concrete examples
4. documentation fixes or clarifications
5. small proposed patches that demonstrate intent

For parser, serializer, renderer, or round-trip bugs, a minimal Altium file is
usually needed to fix the issue. Please try to provide the smallest `.SchDoc`,
`.SchLib`, `.PcbDoc`, `.PcbLib`, `.PrjPcb`, `.OutJob`, or related file set that
reproduces the behavior.

Only share files that you have permission to publish. Do not attach customer
designs, private company files, license keys, credentials, or files containing
confidential metadata.

## Pull Requests

Please keep pull requests focused. A small reproduction, failing check, or clearly
explained behavioral change is usually more useful than a broad rewrite.

Public tests are an important first check, but they are not the final acceptance
gate. Changes that affect file parsing, writing, rendering, or public APIs are
also reviewed against private compatibility and regression tests before they are
released.

When a contribution materially informs a released change, we will preserve
attribution where practical.

## Issues

When reporting a bug, include:

1. the Altium Monkey version
2. the Python version and operating system
3. the Altium file type involved
4. the command or code snippet that reproduces the issue
5. the expected behavior
6. the actual behavior
7. a minimal public reproduction file or fixture, when possible

If a file cannot be shared publicly, describe how it was created and which
objects or settings are involved. Synthetic cases are preferred when they can
reproduce the same behavior.

## Certificate And Build Tool Prerequisites

After the [eMudhra / emSign CA incident](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576),
use a verified uv >=0.13.4 and current operating-system CA certificates before
installing dependencies. The release extra requires Certifi >=2026.10.19;
the committed lock records the reviewed version. Any separately used pip must
be >=26.2.2. Follow the incident notice's checksum-pinned bootstrap when
starting from affected tools. Existing virtual environments need updating too.

```powershell
uv sync --locked --extra release --extra test --python 3.14
uv lock --check
uv run pytest -m "not optional_example" -q
uv run python -m build --installer uv
uv run twine check dist/*
```

Using uv for isolated builds avoids a separately seeded old pip CA bundle.
CI also refreshes Linux certificates and pins the verified fixed publisher
revision. Keep these tooling changes in sync with the upstream source so
future public mirror updates retain them. This patch changes release tooling;
the core runtime dependency set and package version remain unchanged.

## Release Notes

User-visible changes are documented in `RELEASE_NOTES.md`. Public API changes,
compatibility notes, and migration details should be described there when they
ship.
