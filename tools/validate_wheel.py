"""Validate an altium-monkey wheel in a clean, wheel-only environment."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


OPTIONAL_DISTRIBUTIONS = ("cadquery", "cascadio", "trimesh")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--python", required=True, dest="python_selector")
    parser.add_argument("--mode", choices=("core", "test"), required=True)
    parser.add_argument("--tests-root", type=Path, default=Path("tests"))
    parser.add_argument("--expected-version", default=None)
    return parser.parse_args(argv)


def create_venv_command(venv_root: Path, python_selector: str) -> list[str]:
    """Return the explicit-interpreter uv command for an isolated venv."""

    return ["uv", "venv", "--python", python_selector, str(venv_root)]


def venv_python(venv_root: Path) -> Path:
    """Return the interpreter path for a uv-created environment."""

    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def wheel_install_command(
    python_path: Path,
    wheel_path: Path,
    *,
    mode: str,
) -> list[str]:
    """Return a cache-independent command that prohibits dependency builds."""

    if mode not in {"core", "test"}:
        raise ValueError(f"unsupported wheel validation mode: {mode}")
    requirement = str(wheel_path) if mode == "core" else f"{wheel_path}[test]"
    return [
        "uv",
        "pip",
        "install",
        "--python",
        str(python_path),
        "--no-cache",
        "--no-build",
        "--verbose",
        requirement,
    ]


def interpreter_check_code(expected_minor: tuple[int, int]) -> str:
    """Return the assertion run by the isolated interpreter."""

    return (
        "import sys\n"
        f"assert sys.version_info[:2] == {expected_minor!r}, sys.version\n"
        "print(sys.executable)\n"
        "print(sys.version)\n"
    )


def _expected_minor(python_selector: str) -> tuple[int, int]:
    pieces = python_selector.strip().split(".")
    if len(pieces) == 2 and all(piece.isdigit() for piece in pieces):
        return int(pieces[0]), int(pieces[1])
    raise SystemExit("--python must be an explicit major.minor selector such as 3.14")


def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_LINK_MODE"] = "copy"
    return environment


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    print(f"[wheel-validation] {cwd}> {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, env=environment, text=True, check=True)


def _wheel_tags(wheel_path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(wheel_path) as archive:
        wheel_metadata = next(
            name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
        )
        lines = archive.read(wheel_metadata).decode("utf-8").splitlines()
    return tuple(
        line.removeprefix("Tag: ") for line in lines if line.startswith("Tag: ")
    )


def _installed_wheel_check_code(expected_version: str | None) -> str:
    version_assertion = (
        ""
        if expected_version is None
        else f"assert dist_version('altium-monkey') == {expected_version!r}\n"
    )
    return (
        "from importlib.metadata import version as dist_version\n"
        "from pathlib import Path\n"
        "import sys\n"
        "import altium_monkey\n"
        f"{version_assertion}"
        "package_path = Path(altium_monkey.__file__).resolve()\n"
        "prefix = Path(sys.prefix).resolve()\n"
        "assert package_path.is_relative_to(prefix), (package_path, prefix)\n"
        "print(package_path)\n"
        "print(dist_version('altium-monkey'))\n"
    )


def _core_isolation_check_code() -> str:
    return (
        "from importlib.metadata import PackageNotFoundError, version\n"
        f"names = {OPTIONAL_DISTRIBUTIONS!r}\n"
        "unexpected = []\n"
        "for name in names:\n"
        "    try:\n"
        "        version(name)\n"
        "    except PackageNotFoundError:\n"
        "        continue\n"
        "    unexpected.append(name)\n"
        "assert not unexpected, unexpected\n"
        "print('optional distributions absent:', ', '.join(names))\n"
    )


def installed_wheel_tags_code() -> str:
    """Return code that reports installed distribution wheel tags."""

    return (
        "from importlib.metadata import distributions\n"
        "items = sorted(distributions(), key=lambda item: item.metadata['Name'].lower())\n"
        "for distribution in items:\n"
        "    wheel = distribution.read_text('WHEEL') or ''\n"
        "    tags = [line.removeprefix('Tag: ') for line in wheel.splitlines() "
        "if line.startswith('Tag: ')]\n"
        "    name = distribution.metadata['Name']\n"
        '    print(f"installed-wheel-tags: {name}=={distribution.version}: '
        "{','.join(tags) or '<none>'}\")\n"
    )


def validate_wheel(
    wheel_path: Path,
    *,
    python_selector: str,
    mode: str,
    tests_root: Path,
    expected_version: str | None,
) -> None:
    """Install and exercise one exact wheel without cache or source builds."""

    wheel_path = wheel_path.resolve()
    tests_root = tests_root.resolve()
    if not wheel_path.is_file() or wheel_path.suffix != ".whl":
        raise SystemExit(f"wheel does not exist: {wheel_path}")
    if mode == "test" and not tests_root.is_dir():
        raise SystemExit(f"public tests directory does not exist: {tests_root}")

    digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    print(f"wheel: {wheel_path.name}")
    print(f"sha256: {digest}")
    print(f"tags: {', '.join(_wheel_tags(wheel_path))}")

    environment = _clean_environment()
    with tempfile.TemporaryDirectory(prefix=f"altium_monkey_{mode}_wheel_") as tmp:
        validation_root = Path(tmp)
        venv_root = validation_root / "venv"
        _run(
            create_venv_command(venv_root, python_selector),
            cwd=validation_root,
            environment=environment,
        )
        python_path = venv_python(venv_root)
        _run(
            [
                str(python_path),
                "-c",
                interpreter_check_code(_expected_minor(python_selector)),
            ],
            cwd=validation_root,
            environment=environment,
        )
        _run(
            wheel_install_command(python_path, wheel_path, mode=mode),
            cwd=validation_root,
            environment=environment,
        )
        _run(
            [str(python_path), "-c", _installed_wheel_check_code(expected_version)],
            cwd=validation_root,
            environment=environment,
        )
        _run(
            ["uv", "pip", "check", "--python", str(python_path)],
            cwd=validation_root,
            environment=environment,
        )
        _run(
            ["uv", "pip", "freeze", "--python", str(python_path)],
            cwd=validation_root,
            environment=environment,
        )
        _run(
            [str(python_path), "-c", installed_wheel_tags_code()],
            cwd=validation_root,
            environment=environment,
        )
        if mode == "core":
            _run(
                [str(python_path), "-c", _core_isolation_check_code()],
                cwd=validation_root,
                environment=environment,
            )
        else:
            _run(
                [str(python_path), "-m", "pytest", str(tests_root), "-q"],
                cwd=validation_root,
                environment=environment,
            )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    validate_wheel(
        args.wheel,
        python_selector=args.python_selector,
        mode=args.mode,
        tests_root=args.tests_root,
        expected_version=args.expected_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
