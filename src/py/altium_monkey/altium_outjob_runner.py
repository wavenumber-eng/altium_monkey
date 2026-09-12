"""
OutJob execution helpers.

This module provides a small API for running Altium OutJobs through
`AltiumLauncher.run_script()`.

Key capabilities:
- Run one OutJob for a project and wait for completion.
- Run a batch of OutJobs sequentially.
- Stage a temporary OutJob copy per run to keep template files unchanged.
- Normalize GeneratedFiles medium paths in .OutJob files:
  - clear `PublishSettings.OutputFilePathN`
  - enforce `GeneratedFilesSettings.RelativeOutputPathN`
    from `PublishSettings.OutputBasePathN` (or a provided default path).
- Rebind embedded `ConfigurationN_ItemM` `DocumentPath=` values to the
  current project's .PcbDoc before run.
"""

from __future__ import annotations

import configparser
import logging
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Iterable

try:
    from .altium_configparser_helpers import (
        preserve_option_case as _preserve_option_case,
    )
    from .altium_launcher import AltiumLauncher
    from .altium_prjscr import AltiumPrjScr
except ImportError:  # pragma: no cover - supports direct script execution
    _preserve_option_case = import_module(
        "altium_configparser_helpers"
    ).preserve_option_case
    AltiumLauncher = import_module("altium_launcher").AltiumLauncher
    AltiumPrjScr = import_module("altium_prjscr").AltiumPrjScr


log = logging.getLogger(__name__)

_SECTION_OUTPUT_GROUP = "OutputGroup1"
_SECTION_PUBLISH_SETTINGS = "PublishSettings"
_SECTION_GENERATED_FILES_SETTINGS = "GeneratedFilesSettings"
_GENERATED_FILES_TYPE = "generatedfiles"
_DONE_PREFIX = "DONE:"
_CONFIGURATION_ITEM_RE = re.compile(r"^Configuration\d+_Item\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class OutJobNormalizationResult:
    """
    Summary of normalization work performed on an OutJob file.
    """

    outjob_path: Path
    changed: bool
    generated_medium_indices: tuple[int, ...]
    updated_fields: int
    rebound_document_paths: int


@dataclass(frozen=True)
class OutJobRunRequest:
    """
    Input payload for running one OutJob.
    """

    project_path: Path | str
    outjob_path: Path | str | None = None
    timeout_seconds: float = 300.0
    normalize_generated_paths: bool = True
    default_generated_output_path: str | None = None
    bind_pcbdoc_path: Path | str | None = None
    auto_bind_pcbdoc: bool = False
    script_directory: Path | str | None = None
    keep_script_artifacts: bool = False
    kill_after_run: bool = False


@dataclass(frozen=True)
class OutJobRunResult:
    """
    Result of one OutJob run.
    """

    project_path: Path
    outjob_path: Path
    success: bool
    launch_code: int
    timed_out: bool
    error_count: int
    marker_text: str
    normalized_changed: bool
    rebound_document_paths: int
    marker_path: Path
    log_path: Path
    log_text: str


def _is_windows_absolute_path(path_value: str) -> bool:
    value = path_value.strip()
    if re.match(r"^[A-Za-z]:\\", value):
        return True
    if value.startswith("\\\\"):
        return True
    return False


def _normalize_windows_dir_path(path_value: str) -> str:
    value = path_value.strip().strip('"').strip("'").replace("/", "\\")
    while "\\\\" in value:
        value = value.replace("\\\\", "\\")
    if value and not value.endswith("\\"):
        value += "\\"
    return value


def _choose_generated_output_path(
    publish_base_path: str,
    generated_relative_path: str,
    default_generated_output_path: str | None,
) -> str:
    base = _normalize_windows_dir_path(publish_base_path)
    rel = _normalize_windows_dir_path(generated_relative_path)
    default = _normalize_windows_dir_path(default_generated_output_path or "")

    # Prefer relative paths to keep repo/project portable.
    for candidate in (base, rel, default):
        if candidate and not _is_windows_absolute_path(candidate):
            return candidate

    # If no relative path is available, keep an existing value or fallback.
    if default:
        return default
    if base:
        return base
    return rel


def _detect_newline(raw: bytes) -> str:
    if b"\r\n" in raw:
        return "\r\n"
    if b"\n" in raw:
        return "\n"
    return "\r\n"


def _replace_embedded_document_path(value: str, pcbdoc_path: str) -> tuple[str, int]:
    """
    Replace `DocumentPath=` tokens in an OutJob configuration value.

    Returns:
        Tuple of (updated_value, changed_count).
    """
    parts = value.split("|")
    changed_count = 0
    for idx, part in enumerate(parts):
        stripped = part.lstrip()
        if not stripped.lower().startswith("documentpath="):
            continue
        replacement = f"DocumentPath={pcbdoc_path}"
        if stripped != replacement:
            parts[idx] = replacement
            changed_count += 1
    if changed_count == 0:
        return value, 0
    return "|".join(parts), changed_count


def _find_project_pcbdoc(project_path: Path) -> Path:
    """
    Resolve the primary PcbDoc for a project.

    Preference order:
    1. Existing `.PcbDoc` entries recorded in the .PrjPcb.
    2. Exactly one `.PcbDoc` file next to the .PrjPcb.
    """
    input_dir = project_path.parent
    project_text = project_path.read_text(encoding="latin-1", errors="replace")

    doc_path_re = re.compile(r"^\s*DocumentPath\s*=\s*(.*?)\s*$", re.IGNORECASE)
    pcbdoc_candidates: list[Path] = []
    for line in project_text.splitlines():
        match = doc_path_re.match(line)
        if not match:
            continue
        document_path = match.group(1).strip().strip('"').strip("'")
        if not document_path.lower().endswith(".pcbdoc"):
            continue
        candidate = (input_dir / document_path).resolve()
        if candidate.is_file():
            pcbdoc_candidates.append(candidate)

    unique_candidates = sorted(set(pcbdoc_candidates))
    if len(unique_candidates) == 1:
        return unique_candidates[0]
    if len(unique_candidates) > 1:
        raise ValueError(
            f"Project contains multiple .PcbDoc document entries; specify bind_pcbdoc_path explicitly: {project_path}"
        )

    sibling_pcbdocs = sorted(p for p in input_dir.glob("*.PcbDoc") if p.is_file())
    if len(sibling_pcbdocs) == 1:
        return sibling_pcbdocs[0].resolve()
    if len(sibling_pcbdocs) == 0:
        raise FileNotFoundError(f"No .PcbDoc found next to project: {project_path}")
    raise ValueError(
        f"Multiple .PcbDoc files found next to project; specify bind_pcbdoc_path explicitly: {project_path}"
    )


def normalize_outjob_generated_paths(
    outjob_path: Path | str,
    *,
    default_generated_output_path: str | None = None,
    bind_pcbdoc_path: Path | str | None = None,
    normalize_generated_fields: bool = True,
    dry_run: bool = False,
) -> OutJobNormalizationResult:
    """
    Normalize GeneratedFiles medium paths in an OutJob.

    For every `OutputMediumN_Type=GeneratedFiles`:
    - `PublishSettings.OutputFilePathN` is cleared.
    - `PublishSettings.OutputBasePathN` is normalized to a directory path.
    - `GeneratedFilesSettings.RelativeOutputPathN` is set from the normalized base path.

    Args:
        outjob_path: Path to the .OutJob file.
        default_generated_output_path: Relative fallback path used when existing
            GeneratedFiles paths are absolute or empty.
        bind_pcbdoc_path: Optional .PcbDoc path to inject into embedded
            `ConfigurationN_ItemM` `DocumentPath=` tokens.
        normalize_generated_fields: If True, normalize GeneratedFiles output
            path fields; if False, keep those fields unchanged.
        dry_run: If True, report required changes without writing.
    """
    outjob_path = Path(outjob_path).resolve()
    if not outjob_path.is_file():
        raise FileNotFoundError(f"OutJob not found: {outjob_path}")

    raw = outjob_path.read_bytes()
    newline = _detect_newline(raw)

    cfg = configparser.ConfigParser(interpolation=None)
    cfg.optionxform = _preserve_option_case
    cfg.read_string(raw.decode("latin-1"))

    if not cfg.has_section(_SECTION_PUBLISH_SETTINGS):
        cfg.add_section(_SECTION_PUBLISH_SETTINGS)
    if not cfg.has_section(_SECTION_GENERATED_FILES_SETTINGS):
        cfg.add_section(_SECTION_GENERATED_FILES_SETTINGS)

    generated_indices: list[int] = []
    if cfg.has_section(_SECTION_OUTPUT_GROUP):
        medium_index = 1
        while cfg.has_option(_SECTION_OUTPUT_GROUP, f"OutputMedium{medium_index}_Type"):
            medium_type = (
                cfg.get(
                    _SECTION_OUTPUT_GROUP,
                    f"OutputMedium{medium_index}_Type",
                    fallback="",
                )
                .strip()
                .lower()
            )
            if medium_type == _GENERATED_FILES_TYPE:
                generated_indices.append(medium_index)
            medium_index += 1

    changed = False
    updated_fields = 0
    rebound_document_paths = 0
    if normalize_generated_fields:
        for idx in generated_indices:
            key_output_file_path = f"OutputFilePath{idx}"
            key_output_base_path = f"OutputBasePath{idx}"
            key_relative_output_path = f"RelativeOutputPath{idx}"

            current_base = cfg.get(
                _SECTION_PUBLISH_SETTINGS, key_output_base_path, fallback=""
            )
            current_relative = cfg.get(
                _SECTION_GENERATED_FILES_SETTINGS,
                key_relative_output_path,
                fallback="",
            )
            chosen = _choose_generated_output_path(
                current_base,
                current_relative,
                default_generated_output_path,
            )

            # Clear absolute per-run path residue.
            current_output_file_path = cfg.get(
                _SECTION_PUBLISH_SETTINGS,
                key_output_file_path,
                fallback="",
            )
            if current_output_file_path != "":
                cfg.set(_SECTION_PUBLISH_SETTINGS, key_output_file_path, "")
                changed = True
                updated_fields += 1

            if chosen and current_base != chosen:
                cfg.set(_SECTION_PUBLISH_SETTINGS, key_output_base_path, chosen)
                changed = True
                updated_fields += 1

            if chosen and current_relative != chosen:
                cfg.set(
                    _SECTION_GENERATED_FILES_SETTINGS, key_relative_output_path, chosen
                )
                changed = True
                updated_fields += 1

    if bind_pcbdoc_path is not None:
        pcbdoc_path = Path(bind_pcbdoc_path).resolve()
        if not pcbdoc_path.is_file():
            raise FileNotFoundError(f"Bind .PcbDoc not found: {pcbdoc_path}")
        pcbdoc_value = str(pcbdoc_path).replace("/", "\\")
        for section_name in cfg.sections():
            for option_name, option_value in list(cfg.items(section_name)):
                if not _CONFIGURATION_ITEM_RE.match(option_name):
                    continue
                updated_value, changed_count = _replace_embedded_document_path(
                    option_value,
                    pcbdoc_value,
                )
                if changed_count == 0:
                    continue
                cfg.set(section_name, option_name, updated_value)
                changed = True
                updated_fields += 1
                rebound_document_paths += changed_count

    if changed and not dry_run:
        with open(outjob_path, "w", encoding="latin-1", newline=newline) as f:
            cfg.write(f, space_around_delimiters=False)

    return OutJobNormalizationResult(
        outjob_path=outjob_path,
        changed=changed,
        generated_medium_indices=tuple(generated_indices),
        updated_fields=updated_fields,
        rebound_document_paths=rebound_document_paths,
    )


def _pas_escape(value: str) -> str:
    return value.replace("'", "''")


def _render_outjob_runner_pas(
    *,
    project_path: Path,
    outjob_path: Path,
    script_project_path: Path,
    marker_path: Path,
    log_path: Path,
) -> str:
    project = _pas_escape(str(project_path).replace("/", "\\"))
    outjob = _pas_escape(str(outjob_path).replace("/", "\\"))
    marker = _pas_escape(str(marker_path).replace("/", "\\"))
    log_file = _pas_escape(str(log_path).replace("/", "\\"))

    return f"""//******************************************************************************
//  Auto-generated OutJob runner
//******************************************************************************

var
    LogFile    : TextFile;
    ErrorCount : Integer;
    Workspace  : IWorkspace;
    Project    : IProject;
    OutJobDoc  : Variant;

procedure Log(Msg : String);
begin
    WriteLn(LogFile, FormatDateTime('hh:nn:ss', Now) + ' ' + Msg);
    Flush(LogFile);
end;

procedure WriteMarker(Path : String; Content : String);
var
    F : TextFile;
begin
    AssignFile(F, Path);
    Rewrite(F);
    Write(F, Content);
    CloseFile(F);
end;

procedure RunOutJob(ProjectPath : String; OutJobPath : String);
begin
    if Not FileExists(ProjectPath) then
    begin
        Log('ERROR: Project not found: ' + ProjectPath);
        Inc(ErrorCount);
        Exit;
    end;

    if Not FileExists(OutJobPath) then
    begin
        Log('ERROR: OutJob not found: ' + OutJobPath);
        Inc(ErrorCount);
        Exit;
    end;

    Log('Opening project: ' + ProjectPath);
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', ProjectPath);
    RunProcess('WorkspaceManager:OpenObject');
    Sleep(1000);
    Application.ProcessMessages;

    Workspace := GetWorkspace;
    if Workspace = Nil then
    begin
        Log('ERROR: GetWorkspace returned nil');
        Inc(ErrorCount);
        Exit;
    end;

    Project := Workspace.DM_FocusedProject;
    if Project = Nil then
    begin
        Log('ERROR: No focused project');
        Inc(ErrorCount);
        Exit;
    end;

    Log('Compiling project');
    Project.DM_Compile;
    Sleep(500);
    Application.ProcessMessages;

    Log('Opening OutJob: ' + OutJobPath);
    OutJobDoc := Client.OpenDocument('OUTPUTJOB', OutJobPath);
    if OutJobDoc = Nil then
    begin
        Log('ERROR: Could not open OutJob');
        Inc(ErrorCount);
        Exit;
    end;

    Client.ShowDocument(OutJobDoc);
    Sleep(500);
    Application.ProcessMessages;

    Log('Running GenerateReport');
    ResetParameters;
    AddStringParameter('ObjectKind', 'OutputBatch');
    AddStringParameter('Action', 'Run');
    RunProcess('WorkSpaceManager:GenerateReport');
    Sleep(1000);
    Application.ProcessMessages;

    Log('OutJob run completed');
end;

procedure Run;
begin
    ErrorCount := 0;
    AssignFile(LogFile, '{log_file}');
    Rewrite(LogFile);

    try
        Log('Script started');
        RunOutJob('{project}', '{outjob}');
    except
        Log('ERROR: Unhandled exception in Run');
        Inc(ErrorCount);
    end;

    CloseFile(LogFile);
    WriteMarker('{marker}', 'DONE:' + IntToStr(ErrorCount));
end;

end.
"""


def _find_default_outjob_for_project(project_path: Path) -> Path:
    input_dir = project_path.parent

    # First, honor OutJob references recorded in the project file.
    project_text = project_path.read_text(encoding="latin-1", errors="replace")
    doc_path_re = re.compile(r"^\s*DocumentPath\s*=\s*(.*?)\s*$", re.IGNORECASE)
    for line in project_text.splitlines():
        match = doc_path_re.match(line)
        if not match:
            continue
        document_path = match.group(1).strip().strip('"').strip("'")
        if not document_path.lower().endswith(".outjob"):
            continue
        candidate = (input_dir / document_path).resolve()
        if candidate.is_file():
            return candidate

    preferred = input_dir / "reference_gen.OutJob"
    if preferred.is_file():
        return preferred

    outjobs = sorted(p for p in input_dir.glob("*.OutJob") if p.is_file())
    if len(outjobs) == 1:
        return outjobs[0]

    if len(outjobs) == 0:
        raise FileNotFoundError(
            f"No .OutJob file found next to project: {project_path}"
        )
    raise ValueError(
        f"Multiple .OutJob files found next to project; specify one explicitly: {project_path}"
    )


def _resolve_run_bound_pcbdoc(
    project: Path,
    *,
    bind_pcbdoc_path: Path | str | None,
    auto_bind_pcbdoc: bool,
) -> Path | None:
    if bind_pcbdoc_path is None and auto_bind_pcbdoc:
        return _find_project_pcbdoc(project)
    if bind_pcbdoc_path is None:
        return None
    bound_pcbdoc = Path(bind_pcbdoc_path).resolve()
    if not bound_pcbdoc.is_file():
        raise FileNotFoundError(f"Bind .PcbDoc not found: {bound_pcbdoc}")
    return bound_pcbdoc


def _ensure_no_existing_x2_for_kill_after_run() -> None:
    existing_x2 = AltiumLauncher.running_x2_processes()
    if not existing_x2:
        return
    process_list = ", ".join(
        f"{process.pid}:{process.executable or '<unknown>'}" for process in existing_x2
    )
    raise RuntimeError(
        "kill_after_run requires no pre-existing X2.exe processes because "
        f"cleanup kills all X2.exe instances: {process_list}"
    )


def _outjob_run_script_dir(
    project: Path,
    script_directory: Path | str | None,
) -> Path:
    script_dir = (
        project.parent if script_directory is None else Path(script_directory).resolve()
    )
    script_dir.mkdir(parents=True, exist_ok=True)
    return script_dir


def _stage_working_outjob(
    outjob: Path,
    *,
    stage_outjob_copy: bool,
) -> tuple[Path, Path | None]:
    if not stage_outjob_copy:
        return outjob, None
    with tempfile.NamedTemporaryFile(
        prefix=".__outjob_run_",
        suffix=".OutJob",
        dir=str(outjob.parent),
        delete=False,
    ) as staged_file:
        staged_outjob = Path(staged_file.name)
    shutil.copy2(outjob, staged_outjob)
    return staged_outjob, staged_outjob


def _outjob_runner_artifact_paths(
    tmp_dir: Path,
    unit_name: str,
) -> tuple[Path, Path, Path, Path]:
    return (
        tmp_dir / f"{unit_name}.pas",
        tmp_dir / f"{unit_name}.PrjScr",
        tmp_dir / f"{unit_name}.done",
        tmp_dir / f"{unit_name}.log",
    )


def _write_outjob_runner_artifacts(
    *,
    pas_path: Path,
    prjscr_path: Path,
    project: Path,
    working_outjob: Path,
    marker_path: Path,
    run_log_path: Path,
) -> None:
    pas_path.write_text(
        _render_outjob_runner_pas(
            project_path=project,
            outjob_path=working_outjob,
            script_project_path=prjscr_path,
            marker_path=marker_path,
            log_path=run_log_path,
        ),
        encoding="utf-8",
    )
    AltiumPrjScr.create(pas_path.name).save(prjscr_path)


def _clear_outjob_run_artifacts(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


def _wait_for_outjob_marker(
    marker_path: Path,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[str, int, bool]:
    marker_text = ""
    error_count = -1
    timed_out = True
    start = time.time()
    while (time.time() - start) < timeout_seconds:
        if marker_path.exists():
            time.sleep(0.1)
            marker_text = marker_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).strip()
            if marker_text.startswith(_DONE_PREFIX):
                try:
                    error_count = int(marker_text[len(_DONE_PREFIX) :])
                except ValueError:
                    error_count = -1
            timed_out = False
            break
        time.sleep(poll_interval_seconds)
    return marker_text, error_count, timed_out


def _read_outjob_run_log(run_log_path: Path) -> str:
    if not run_log_path.exists():
        return ""
    return run_log_path.read_text(encoding="utf-8", errors="replace")


class AltiumOutJobRunner:
    """
    Runner for Altium OutJobs.
    """

    def __init__(
        self,
        preferred_version: int | None = 25,
        *,
        altium_path: Path | str | None = None,
    ) -> None:
        self._preferred_version = preferred_version
        self._altium_path = Path(altium_path).resolve() if altium_path else None

    def run(
        self,
        project_path: Path | str,
        *,
        outjob_path: Path | str | None = None,
        timeout_seconds: float = 300.0,
        normalize_generated_paths: bool = True,
        default_generated_output_path: str | None = None,
        bind_pcbdoc_path: Path | str | None = None,
        auto_bind_pcbdoc: bool = False,
        stage_outjob_copy: bool = True,
        script_directory: Path | str | None = None,
        keep_script_artifacts: bool = False,
        poll_interval_seconds: float = 0.5,
        kill_after_run: bool = False,
    ) -> OutJobRunResult:
        """
        Run a single OutJob and wait for completion marker.

        Args:
            project_path: Path to the .PrjPcb file.
            outjob_path: Path to .OutJob. If omitted, uses a default OutJob
                beside the project file.
            timeout_seconds: Maximum wait time for script completion.
            normalize_generated_paths: Normalize GeneratedFiles path fields
                before running.
            default_generated_output_path: Relative fallback for
                GeneratedFiles paths when existing values are absolute/empty.
            bind_pcbdoc_path: Optional .PcbDoc to bind into embedded OutJob
                `ConfigurationN_ItemM` `DocumentPath=` tokens.
                If omitted, existing OutJob document paths are left untouched.
            auto_bind_pcbdoc: If True, auto-discover the primary project
                `.PcbDoc` and bind it into embedded OutJob `DocumentPath=`
                tokens. Use this only when intentionally repairing or
                rebasing stale OutJob document paths.
            stage_outjob_copy: If True, run from a temporary copy so the
                source OutJob file remains unchanged.
            script_directory: Directory where temporary run artifacts are
                generated. Defaults to the project's directory (typically the
                `input/` folder).
            keep_script_artifacts: When `script_directory` is provided, keep
                generated script artifacts after run for inspection.
            poll_interval_seconds: Marker poll interval.
            kill_after_run: If True, require that no X2.exe processes are
                already running, then force-terminate every X2.exe process on
                this machine after the marker/log has been collected. Use this
                for unattended runs where project compile may dirty the
                in-memory project and interactive close prompts must be
                avoided. If False, the runner does not close the opened project
                documents because that close path may prompt to save; callers
                running unattended batches should use `kill_after_run=True`.
        """
        project = Path(project_path).resolve()
        if not project.is_file():
            raise FileNotFoundError(f"Project not found: {project}")

        outjob = (
            Path(outjob_path).resolve()
            if outjob_path
            else _find_default_outjob_for_project(project)
        )
        if not outjob.is_file():
            raise FileNotFoundError(f"OutJob not found: {outjob}")

        bound_pcbdoc = _resolve_run_bound_pcbdoc(
            project,
            bind_pcbdoc_path=bind_pcbdoc_path,
            auto_bind_pcbdoc=auto_bind_pcbdoc,
        )
        staged_outjob: Path | None = None
        pas_path: Path | None = None
        prjscr_path: Path | None = None
        marker_path: Path | None = None
        run_log_path: Path | None = None
        try:
            if kill_after_run:
                _ensure_no_existing_x2_for_kill_after_run()

            script_dir = _outjob_run_script_dir(project, script_directory)

            # Keep all transient execution files in one directory to avoid
            # path-resolution differences in Altium when running scripts.
            tmp_dir = script_dir
            working_outjob, staged_outjob = _stage_working_outjob(
                outjob,
                stage_outjob_copy=stage_outjob_copy,
            )

            normalization = normalize_outjob_generated_paths(
                working_outjob,
                default_generated_output_path=default_generated_output_path,
                bind_pcbdoc_path=bound_pcbdoc,
                normalize_generated_fields=normalize_generated_paths,
                dry_run=False,
            )

            unit_name = "run_outjob"
            pas_path, prjscr_path, marker_path, run_log_path = (
                _outjob_runner_artifact_paths(tmp_dir, unit_name)
            )
            _write_outjob_runner_artifacts(
                pas_path=pas_path,
                prjscr_path=prjscr_path,
                project=project,
                working_outjob=working_outjob,
                marker_path=marker_path,
                run_log_path=run_log_path,
            )

            _clear_outjob_run_artifacts(marker_path, run_log_path)

            launcher = AltiumLauncher(
                altium_path=self._altium_path,
                preferred_version=self._preferred_version,
            )
            try:
                launch_code = launcher.run_script(prjscr_path, unit_name, "Run")

                marker_text, error_count, timed_out = _wait_for_outjob_marker(
                    marker_path,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )
                log_text = _read_outjob_run_log(run_log_path)
            finally:
                if kill_after_run:
                    launcher.kill()

            success = (not timed_out) and (error_count == 0)
            return OutJobRunResult(
                project_path=project,
                outjob_path=outjob,
                success=success,
                launch_code=launch_code,
                timed_out=timed_out,
                error_count=error_count,
                marker_text=marker_text,
                normalized_changed=normalization.changed,
                rebound_document_paths=normalization.rebound_document_paths,
                marker_path=marker_path,
                log_path=run_log_path,
                log_text=log_text,
            )
        finally:
            if staged_outjob is not None and staged_outjob.exists():
                try:
                    staged_outjob.unlink()
                except OSError:
                    pass
            if not keep_script_artifacts:
                for artifact in (pas_path, prjscr_path, marker_path, run_log_path):
                    if artifact is None:
                        continue
                    try:
                        if artifact.exists():
                            artifact.unlink()
                    except OSError:
                        pass

    def run_many(
        self,
        requests: Iterable[OutJobRunRequest],
        *,
        stop_on_error: bool = False,
    ) -> list[OutJobRunResult]:
        """
        Run multiple OutJobs sequentially.
        """
        results: list[OutJobRunResult] = []
        for request in requests:
            result = self.run(
                request.project_path,
                outjob_path=request.outjob_path,
                timeout_seconds=request.timeout_seconds,
                normalize_generated_paths=request.normalize_generated_paths,
                default_generated_output_path=request.default_generated_output_path,
                bind_pcbdoc_path=request.bind_pcbdoc_path,
                auto_bind_pcbdoc=request.auto_bind_pcbdoc,
                script_directory=request.script_directory,
                keep_script_artifacts=request.keep_script_artifacts,
                kill_after_run=request.kill_after_run,
            )
            results.append(result)
            if stop_on_error and not result.success:
                break
        return results
