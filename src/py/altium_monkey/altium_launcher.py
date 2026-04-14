"""Windows launcher helper for opening files and scripts in Altium Designer."""

import logging
import os
import platform
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

class AltiumLauncher:
    """
    Launches Altium Designer on Windows and opens files programmatically.
    """

    def __init__(self, altium_path: Path | None = None, preferred_version: int | None = None) -> None:
        """
        Initialize Altium launcher.
        
        Args:
            altium_path: Path to X2.exe. If None, auto-detects from Program Files.
            preferred_version: Preferred Altium version (e.g., 24, 25). If None, prefers
                `ALTIUM_PREFERRED_VERSION` and otherwise defaults to AD25 for interop lanes.
        """
        if platform.system() != "Windows":
            raise OSError("AltiumLauncher is supported on Windows only.")

        preferred_version = self._resolve_preferred_version(preferred_version)
        if altium_path is None:
            altium_path = self._find_altium_installation(preferred_version)

        if altium_path is None or not Path(altium_path).exists():
            raise FileNotFoundError(
                "Altium Designer (X2.exe) not found. Please install Altium Designer or specify path manually."
            )

        self.altium_path = Path(altium_path)
        log.info(f"Using Altium: {self.altium_path}")

    @staticmethod
    def _resolve_preferred_version(preferred_version: int | None) -> int | None:
        """
        Resolve the preferred Altium major version for auto-detection.
        """
        if preferred_version is not None:
            return preferred_version

        env_value = os.environ.get("ALTIUM_PREFERRED_VERSION", "").strip()
        if env_value:
            try:
                return int(env_value.removeprefix("AD").strip())
            except ValueError:
                log.warning("Ignoring invalid ALTIUM_PREFERRED_VERSION=%r", env_value)

        # Default to AD25 for the current interop/oracle lanes.
        return 25

    def _find_altium_installation(self, preferred_version: int | None = None) -> Path | None:
        """
        Find Altium Designer installation by searching Program Files.
        If multiple versions found, returns the preferred version or latest.
        
        Args:
            preferred_version: Preferred Altium version (e.g., 24, 25). If None, uses
                `_resolve_preferred_version()` and falls back to latest only if needed.
        
        Returns:
            Path to X2.exe or None if not found
        """
        if platform.system() != "Windows":
            return None

        # Search locations
        search_paths = [
            Path(r"C:\Program Files\Altium"),
            Path(r"C:\Program Files (x86)\Altium"),
        ]

        found_installations: list[tuple[int, Path]] = []

        for base_path in search_paths:
            if not base_path.exists():
                continue

            # Find all ADxx folders
            for item in base_path.iterdir():
                if not item.is_dir():
                    continue

                # Check if folder name matches ADxx pattern
                match = re.match(r'^AD(\d+)$', item.name, re.IGNORECASE)
                if match:
                    version = int(match.group(1))
                    exe_path = item / "X2.EXE"

                    if exe_path.exists():
                        found_installations.append((version, exe_path))
                        log.info(f"Found Altium Designer {version}: {exe_path}")

        if not found_installations:
            log.warning("No Altium Designer installations found in Program Files")
            return None

        # Sort by version number (descending)
        found_installations.sort(key=lambda x: x[0], reverse=True)

        # Try to find preferred version if specified
        if preferred_version is not None:
            for version, path in found_installations:
                if version == preferred_version:
                    log.info(f"Using preferred Altium version: AD{version}")
                    return path

            # Preferred version not found, warn and fall back to latest
            log.warning(f"Preferred Altium version AD{preferred_version} not found")
            log.info(f"Available versions: {', '.join(f'AD{v}' for v, _ in found_installations)}")

        # Use latest version
        latest_version, latest_path = found_installations[0]

        if len(found_installations) > 1:
            log.info(f"Multiple Altium versions found, using latest: AD{latest_version}")
        else:
            log.info(f"Found Altium Designer {latest_version}")

        return latest_path

    def _open_file_impl(self, file_path: Path) -> bool:
        """
        Internal implementation for opening files in Altium Designer.
        
        Directly launches X2.EXE with the file path as an argument.
        This is more reliable than os.startfile() which depends on Windows
        file associations being properly configured.
        
        Args:
            file_path: Path to file to open
        
        Returns:
            True if launch succeeded
        """
        file_path = Path(file_path).resolve()

        if not file_path.exists():
            log.error(f"File not found: {file_path}")
            return False

        log.info(f"Opening file: {file_path}")

        try:
            # Launch X2.EXE directly with the file path
            # Use 'start' to run asynchronously (non-blocking)
            cmd = f'start "" "{self.altium_path}" "{file_path}"'
            result = os.system(cmd)
            if result != 0:
                log.error(f"Failed to launch Altium (exit code {result})")
                return False
            return True
        except OSError as e:
            log.error(f"Failed to open file: {e}")
            return False

    def run_script(
        self,
        script_project: str | Path,
        unit_name: str,
        procedure_name: str,
    ) -> int:
        """
        Launch Altium and run a script procedure on startup.
        
        Execute a startup script through Altium.
        
        Uses os.system() with cmd.exe to reliably handle the pipe character
        in Altium's -R argument syntax. Python subprocess has escaping issues
        with this complex argument format.
        
        Args:
            script_project: Path to .PrjScr script project file
            unit_name: Name of the script unit (e.g., "MyScriptUnit")
            procedure_name: Name of the procedure to run (e.g., "Run")
        
        Returns:
            Exit code from os.system() (0 = success)
        """
        script_project = Path(script_project).resolve()

        if not script_project.exists():
            raise FileNotFoundError(f"Script project not found: {script_project}")

        # Build the cmd.exe invocation that matches the working startup path.
        # The ^| escapes the pipe character in cmd.exe
        # The start "" runs it asynchronously (doesn't block)
        cmd = f'start "" "{self.altium_path}" -RScriptingSystem:RunScript(ProjectName="{script_project}"^|ProcName="{unit_name}>{procedure_name}")'

        log.info(f"Running script: {unit_name}>{procedure_name} from {script_project.name}")
        log.info(f"Command: {cmd}")

        return os.system(cmd)

    def open(self, file_path: str | Path) -> bool:
        """
        Open a file in Altium Designer.
        
        Open a file or project in Altium.
        
        Directly launches X2.EXE with the file path as an argument.
        This is more reliable than os.startfile() which depends on Windows
        file associations being properly configured.
        Supports all Altium file types: SchDoc, SchLib, PcbDoc, PcbLib, PrjPcb.
        
        Args:
            file_path: Path to the file to open
        
        Returns:
            True if file was opened successfully
        """
        return self._open_file_impl(file_path)

    def kill(self) -> bool:
        """
        Kill all running Altium Designer processes.
        
        Terminate running Altium processes.
        
        Uses Windows taskkill to forcefully terminate X2.EXE processes.
        
        Returns:
            True if Altium was killed or wasn't running
        """
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "X2.EXE"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                log.info("Altium Designer processes killed")
                return True
            else:
                log.warning(f"Could not kill Altium: {result.stderr}")
                return False
        except Exception as e:
            log.error(f"Error killing Altium: {e}")
            return False
