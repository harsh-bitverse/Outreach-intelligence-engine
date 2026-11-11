"""
Workbook Guard & Concurrency Coordination Module.

Enforces crash-safe, deterministic Excel workbook operations:
1. Unified single locking mechanism (.lock canonical file, with legacy .opp_lock mirror).
   Detects and reclaims stale locks (>120s or dead PIDs).
2. Atomic temporary-file replacement with SHA256 checksum and ZIP/XML structural validation.
3. Companion .version metadata tracking.
4. Stable committed read-snapshots (.stable.xlsx) and isolated per-reader read copies
   to completely eliminate openpyxl ElementTree.ParseError concurrency collisions.
"""

import hashlib
import json
import logging
import os
import shutil
import time
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from config.settings import settings

logger = logging.getLogger(__name__)


def is_pid_running(pid: int) -> bool:
    """Check whether a given PID is alive on Windows."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class UnifiedWorkbookLock:
    """
    Unified mutual exclusion coordinator for the master workbook.
    Manages canonical .lock file and legacy .opp_lock mirror.
    """

    def __init__(self, workbook_path: Optional[Path] = None, stale_timeout_seconds: float = 120.0):
        self.workbook_path = Path(workbook_path or settings.MASTER_WORKBOOK_PATH)
        self.lock_path = self.workbook_path.with_suffix(".lock")
        self.opp_lock_path = self.workbook_path.with_suffix(".opp_lock")
        self.stale_timeout_seconds = stale_timeout_seconds
        self._acquired = False

    def _check_and_clear_stale_lock(self, path: Path) -> bool:
        """Inspects lock file for age or dead owner PID. Returns True if cleared."""
        if not path.exists():
            return False
        try:
            mtime = path.stat().st_mtime
            age = time.time() - mtime
            content = path.read_text(encoding="utf-8").strip()
            owner_pid = None
            if content:
                parts = content.split()
                if parts and parts[0].isdigit():
                    owner_pid = int(parts[0])

            # If older than stale timeout or owner PID is demonstrably dead, clear it
            is_dead = (owner_pid is not None and not is_pid_running(owner_pid))
            if age > self.stale_timeout_seconds or is_dead:
                logger.warning(
                    f"Clearing stale lock file {path.name} (Age: {age:.1f}s, Owner PID: {owner_pid}, Running: {not is_dead})"
                )
                path.unlink(missing_ok=True)
                return True
        except Exception as e:
            logger.warning(f"Error checking stale lock {path}: {e}")
        return False

    def acquire(self, timeout_seconds: float = 15.0) -> bool:
        """
        Acquires mutual exclusion lock coordinating both .lock and .opp_lock.
        Raises TimeoutError if not acquired within timeout_seconds.
        """
        start_time = time.time()
        pid = os.getpid()
        payload = f"{pid} {datetime.now(timezone.utc).isoformat()}".encode("utf-8")

        while time.time() - start_time < timeout_seconds:
            # Check for stale locks
            self._check_and_clear_stale_lock(self.lock_path)
            self._check_and_clear_stale_lock(self.opp_lock_path)

            if self.lock_path.exists() or self.opp_lock_path.exists():
                time.sleep(0.15)
                continue

            try:
                # Atomically claim canonical .lock
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, payload)
                os.close(fd)

                # Mirror to legacy .opp_lock
                try:
                    fd_opp = os.open(str(self.opp_lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd_opp, payload)
                    os.close(fd_opp)
                except Exception:
                    pass

                self._acquired = True
                return True
            except FileExistsError:
                time.sleep(0.15)

        raise TimeoutError(
            f"Could not acquire unified workbook lock at {self.lock_path} within {timeout_seconds}s"
        )

    def release(self) -> None:
        """Releases both .lock and .opp_lock files."""
        try:
            if self.lock_path.exists():
                self.lock_path.unlink()
        except OSError:
            pass
        try:
            if self.opp_lock_path.exists():
                self.opp_lock_path.unlink()
        except OSError:
            pass
        self._acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def compute_file_sha256(file_path: Path) -> str:
    """Compute SHA256 hexadecimal hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_workbook_zip_and_xml(file_path: Path) -> None:
    """
    Validates that file_path is a valid, uncorrupted Excel XLSX archive with well-formed XML.
    Raises ValueError on any integrity failure.
    """
    if not file_path.exists():
        raise ValueError(f"Workbook candidate does not exist: {file_path}")
    if file_path.stat().st_size < 1000:
        raise ValueError(f"Workbook candidate unexpectedly truncated: {file_path.stat().st_size} bytes")

    if not zipfile.is_zipfile(file_path):
        raise ValueError(f"File is not a valid ZIP/XLSX archive: {file_path}")

    with zipfile.ZipFile(file_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise ValueError(f"Corrupt ZIP entry detected in workbook: {bad_file}")

        namelist = zf.namelist()
        if "xl/workbook.xml" not in namelist:
            raise ValueError("Missing critical xl/workbook.xml in XLSX archive")

        # Check all internal XML files for well-formedness
        for name in namelist:
            if name.endswith(".xml"):
                raw_xml = zf.read(name)
                try:
                    ET.fromstring(raw_xml)
                except ET.ParseError as e:
                    raise ValueError(f"XML parse failure in XLSX member {name}: {e}")


def atomic_save_and_validate_workbook(
    wb: Any,
    workbook_path: Path,
    create_backup: bool = True
) -> Dict[str, Any]:
    """
    Saves an openpyxl workbook safely:
    1. Saves to a temporary file.
    2. Validates ZIP CRC and XML structure.
    3. Computes SHA256 checksum.
    4. Atomically moves/replaces target workbook.
    5. Writes companion .version metadata file.
    6. Updates committed stable read snapshot (.stable.xlsx).
    """
    workbook_path = Path(workbook_path)
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = workbook_path.with_suffix(f".tmp_{os.getpid()}_{int(time.time()*1000)}.xlsx")

    try:
        wb.save(str(temp_path))

        # Validate temporary file integrity
        validate_workbook_zip_and_xml(temp_path)
        sha256_hash = compute_file_sha256(temp_path)
        size_bytes = temp_path.stat().st_size

        # Create timestamped pre-sync backup of existing file if requested
        if create_backup and workbook_path.exists():
            backup_dir = settings.DATA_DIR / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            backup_path = backup_dir / f"{workbook_path.stem}_guard_{ts}.xlsx.bak"
            shutil.copy2(str(workbook_path), str(backup_path))

        # Atomic replacement with bounded retry for Windows sharing
        committed = False
        for attempt in range(5):
            try:
                os.replace(str(temp_path), str(workbook_path))
                committed = True
                break
            except OSError:
                time.sleep(0.15 * (attempt + 1))

        if not committed:
            shutil.move(str(temp_path), str(workbook_path))

        # Write companion version metadata
        version_data = {
            "sha256": sha256_hash,
            "size_bytes": size_bytes,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "writer_pid": os.getpid(),
            "target": str(workbook_path)
        }
        version_file = workbook_path.with_suffix(".version")
        version_file.write_text(json.dumps(version_data, indent=2), encoding="utf-8")

        # Update stable read-snapshot atomically
        stable_path = workbook_path.with_suffix(".stable.xlsx")
        temp_stable = stable_path.with_suffix(f".tmp_stable_{os.getpid()}.xlsx")
        shutil.copy2(str(workbook_path), str(temp_stable))
        try:
            os.replace(str(temp_stable), str(stable_path))
        except OSError:
            shutil.move(str(temp_stable), str(stable_path))

        return {
            "status": "COMMITTED",
            "sha256": sha256_hash,
            "size_bytes": size_bytes,
            "target": str(workbook_path),
            "stable_snapshot": str(stable_path)
        }
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


@contextmanager
def safe_read_workbook_snapshot(workbook_path: Path):
    """
    Context manager for reading the workbook safely without concurrent openpyxl streaming collisions.
    Copies either .stable.xlsx (if available) or workbook_path to a per-reader temporary snapshot file.
    Yields the Path to the isolated temporary snapshot file, and cleans it up upon exit.
    """
    workbook_path = Path(workbook_path)
    stable_path = workbook_path.with_suffix(".stable.xlsx")

    # Select best read source
    source_path = stable_path if (stable_path.exists() and stable_path.stat().st_size > 10000) else workbook_path
    if not source_path.exists():
        raise FileNotFoundError(f"Workbook source not found: {source_path}")

    snap_file = settings.DATA_DIR / f"_reader_snap_{os.getpid()}_{int(time.time()*1000)}.xlsx"
    snap_file.parent.mkdir(parents=True, exist_ok=True)

    # Copy with bounded retry in case write is committing
    copied = False
    for attempt in range(5):
        try:
            shutil.copy2(str(source_path), str(snap_file))
            copied = True
            break
        except Exception:
            time.sleep(0.1 * (attempt + 1))

    if not copied:
        # Fallback to source directly
        yield source_path
        return

    try:
        yield snap_file
    finally:
        if snap_file.exists():
            snap_file.unlink(missing_ok=True)
