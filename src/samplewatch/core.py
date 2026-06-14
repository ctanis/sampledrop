from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
import os
from pathlib import Path
import re
import threading
import time
import tomllib

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from .audio import AudioProcessor, AudioResult
from .config import AudioOptions, Config, OrganizationOptions


AUDIO_EXTENSIONS = {".wav", ".aiff", ".aif", ".flac"}
SEQUENCE_METADATA_NAME = ".samplewatch-sequences.toml"
SEQUENCE_LOCK_NAME = ".samplewatch-sequences.lock"
SEQUENCE_LOCK_TIMEOUT_SEC = 10.0
SEQUENCE_LOCK_STALE_SEC = 120.0
PROJECT_RE = re.compile(r"[^a-zA-Z0-9]+")


class ProjectState:
    def __init__(self, project: str) -> None:
        self._project = sanitize_project(project)
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            return self._project

    def set(self, project: str) -> str:
        cleaned = sanitize_project(project)
        with self._lock:
            self._project = cleaned
        return cleaned


class ProcessingState:
    def __init__(self, options: AudioOptions) -> None:
        self._options = options
        self._lock = threading.Lock()

    def snapshot(self) -> AudioOptions:
        with self._lock:
            return self._options

    def set_trim(self, enabled: bool) -> bool:
        with self._lock:
            self._options = replace(self._options, trim=enabled)
            return self._options.trim

    def toggle_trim(self) -> bool:
        with self._lock:
            self._options = replace(self._options, trim=not self._options.trim)
            return self._options.trim

    def set_normalize(self, enabled: bool) -> bool:
        with self._lock:
            self._options = replace(self._options, normalize=enabled)
            return self._options.normalize

    def toggle_normalize(self) -> bool:
        with self._lock:
            self._options = replace(self._options, normalize=not self._options.normalize)
            return self._options.normalize


class SequenceStore:
    def __init__(self, samples_dir: Path) -> None:
        self.samples_dir = samples_dir
        self.metadata_path = samples_dir / SEQUENCE_METADATA_NAME
        self.lock_path = samples_dir / SEQUENCE_LOCK_NAME
        self._lock = threading.Lock()

    def next_destination(self, project: str, folder: str | None) -> Path:
        with self._lock:
            destination_dir = self.samples_dir / folder if folder else self.samples_dir
            destination_dir.mkdir(parents=True, exist_ok=True)
            next_number = self._reserve_number(project, destination_dir)
            return destination_dir / f"{project}_{next_number:03d}.wav"

    def _reserve_number(self, project: str, destination_dir: Path) -> int:
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        with SequenceFileLock(self.lock_path):
            metadata = self._read_metadata()
            projects = metadata.projects
            highest = max(projects.get(project, 0), self._highest_file_number(project))
            next_number = highest + 1
            while (destination_dir / f"{project}_{next_number:03d}.wav").exists():
                next_number += 1
            projects[project] = next_number
            self._write_metadata(metadata)
            return next_number

    def _highest_file_number(self, project: str) -> int:
        pattern = re.compile(rf"^{re.escape(project)}_(\d{{3}})\.wav$")
        highest = 0
        for path in self.samples_dir.rglob(f"{project}_*.wav"):
            if SEQUENCE_LOCK_NAME in path.parts:
                continue
            match = pattern.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest

    def set_last_path(self, path: Path) -> None:
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        with SequenceFileLock(self.lock_path):
            metadata = self._read_metadata()
            metadata.state["last_path"] = relative_sample_path(path, self.samples_dir)
            self._write_metadata(metadata)

    def last_path(self) -> Path | None:
        metadata = self._read_metadata()
        raw_path = metadata.state.get("last_path")
        if not raw_path:
            return None
        path = Path(raw_path)
        if not path.is_absolute():
            path = self.samples_dir / path
        return path

    def _read_metadata(self) -> "SequenceMetadata":
        if not self.metadata_path.exists():
            return SequenceMetadata()

        data = tomllib.loads(self.metadata_path.read_text())
        raw_projects = data.get("projects", {})
        projects: dict[str, int] = {}
        for project, value in raw_projects.items():
            try:
                projects[str(project)] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        raw_state = data.get("state", {})
        state = {str(key): str(value) for key, value in raw_state.items()}
        return SequenceMetadata(projects=projects, state=state)

    def _write_metadata(self, metadata: "SequenceMetadata") -> None:
        lines = [
            "# Tracks the highest sequence id reserved for each project.",
            "# This keeps names unique even if files move out of the samples folder.",
            "[projects]",
        ]
        for project in sorted(metadata.projects):
            lines.append(f'{toml_key(project)} = {metadata.projects[project]}')
        if metadata.state:
            lines.extend(["", "[state]"])
            for key in sorted(metadata.state):
                lines.append(f'{toml_key(key)} = "{toml_string(metadata.state[key])}"')
        temp_path = self.metadata_path.with_suffix(".tmp")
        temp_path.write_text("\n".join(lines) + "\n")
        temp_path.replace(self.metadata_path)


class SequenceMetadata:
    def __init__(self, projects: dict[str, int] | None = None, state: dict[str, str] | None = None) -> None:
        self.projects = projects or {}
        self.state = state or {}


class SequenceFileLock:
    def __init__(
        self,
        path: Path,
        timeout_sec: float = SEQUENCE_LOCK_TIMEOUT_SEC,
        stale_sec: float = SEQUENCE_LOCK_STALE_SEC,
    ) -> None:
        self.path = path
        self.timeout_sec = timeout_sec
        self.stale_sec = stale_sec

    def __enter__(self) -> "SequenceFileLock":
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                self.path.mkdir()
                (self.path / "owner").write_text(f"pid={os.getpid()}\ntime={time.time():.3f}\n")
                return self
            except FileExistsError:
                if self._is_stale():
                    self._remove_stale_lock()
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for sequence metadata lock: {self.path}")
                time.sleep(0.1)

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        try:
            (self.path / "owner").unlink(missing_ok=True)
            self.path.rmdir()
        except FileNotFoundError:
            return
        except OSError:
            return

    def _is_stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self.stale_sec
        except FileNotFoundError:
            return False

    def _remove_stale_lock(self) -> None:
        try:
            (self.path / "owner").unlink(missing_ok=True)
            self.path.rmdir()
        except FileNotFoundError:
            return
        except OSError:
            return


class SampleProcessor:
    def __init__(
        self,
        config: Config,
        project_state: ProjectState,
        processing_state: ProcessingState,
        logger: logging.Logger,
        notify: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.project_state = project_state
        self.processing_state = processing_state
        self.logger = logger
        self.notify = notify or (lambda _title, _message: None)
        self.audio = AudioProcessor()
        self.sequences = SequenceStore(config.samples_dir)
        self._seen: set[Path] = set()
        self._seen_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._last_path: Path | None = None

    def maybe_process(self, source: Path) -> None:
        source = source.resolve()
        if not is_audio_file(source):
            return

        with self._seen_lock:
            if source in self._seen:
                return
            self._seen.add(source)

        try:
            with self._process_lock:
                self._wait_until_stable(source)
                project = self.project_state.get()
                folder = folder_name(datetime.now(), self.config.organization)
                destination = self.sequences.next_destination(project, folder)
                temp_destination = destination.with_name(f".{destination.stem}.tmp.wav")
                result = self.audio.process(source, temp_destination, self.processing_state.snapshot())
                temp_destination.replace(destination)
                try:
                    source.unlink()
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                self._set_last_path(destination)
                self._print_saved(destination, result)
                self.logger.info(
                    "saved source=%s destination=%s trimmed=%s normalized=%s duration=%.3f",
                    source,
                    destination,
                    result.trimmed,
                    result.normalized,
                    result.duration_sec,
                )
                self.notify("Sample saved", relative_sample_path(destination, self.config.samples_dir))
        except Exception:
            if "temp_destination" in locals():
                temp_destination.unlink(missing_ok=True)
            self.logger.exception("failed source=%s", source)
            print(f"Error processing {source.name}; original left in place.")
            self.notify("Samplewatch error", f"Could not process {source.name}")
        finally:
            with self._seen_lock:
                self._seen.discard(source)

    def trim_last(self) -> None:
        options = replace(self.config.audio, trim=True, normalize=False)
        self._reprocess_last("Trimmed last", options)

    def normalize_last(self) -> None:
        options = replace(self.config.audio, trim=False, normalize=True)
        self._reprocess_last("Normalized last", options)

    def rename_last(self, project: str | None = None) -> Path:
        with self._process_lock:
            source = self._require_last_path()
            target_project = sanitize_project(project) if project else self.project_state.get()
            if project:
                self.project_state.set(project)
            if source.name.startswith(f"{target_project}_"):
                print(f"Last file already uses project: {target_project}")
                self.notify("Sample rename unchanged", relative_sample_path(source, self.config.samples_dir))
                return source

            destination = self.sequences.next_destination(
                target_project,
                existing_sample_folder(source, self.config.samples_dir),
            )
            source.replace(destination)
            self._set_last_path(destination)
            self.logger.info("renamed-last source=%s destination=%s", source, destination)
            print("Renamed last:")
            print(relative_sample_path(destination, self.config.samples_dir))
            self.notify("Sample renamed", relative_sample_path(destination, self.config.samples_dir))
            return destination

    def last_path(self) -> Path:
        with self._process_lock:
            return self._require_last_path()

    def _reprocess_last(self, label: str, options: AudioOptions) -> AudioResult | None:
        with self._process_lock:
            source = self._require_last_path()
            temp_destination = source.with_name(f".{source.stem}.tmp.wav")
            try:
                result = self.audio.process(source, temp_destination, options)
                temp_destination.replace(source)
                self._set_last_path(source)
                self.logger.info(
                    "%s path=%s trimmed=%s normalized=%s duration=%.3f",
                    label.lower().replace(" ", "-"),
                    source,
                    result.trimmed,
                    result.normalized,
                    result.duration_sec,
                )
                self._print_last_action(label, source, result)
                self.notify(label, relative_sample_path(source, self.config.samples_dir))
                return result
            except Exception:
                temp_destination.unlink(missing_ok=True)
                self.logger.exception("failed-last-action path=%s", source)
                print(f"Error updating last file: {source.name}")
                self.notify("Samplewatch error", f"Could not update {source.name}")
                return None

    def _require_last_path(self) -> Path:
        path = self._last_path or self.sequences.last_path()
        if path is None:
            raise ValueError("no file has been processed yet")
        if not path.exists():
            raise ValueError(f"last file no longer exists: {path}")
        self._last_path = path
        return path

    def _set_last_path(self, path: Path) -> None:
        self._last_path = path
        self.sequences.set_last_path(path)

    def _wait_until_stable(self, source: Path) -> None:
        deadline = time.monotonic() + self.config.audio.write_timeout_sec
        stable_count = 0
        previous_size = -1

        while time.monotonic() < deadline:
            if not source.exists():
                raise FileNotFoundError(source)
            current_size = source.stat().st_size
            if current_size > 0 and current_size == previous_size:
                stable_count += 1
                if stable_count >= self.config.audio.stable_checks:
                    return
            else:
                stable_count = 0
                previous_size = current_size
            time.sleep(self.config.audio.stable_interval_sec)

        raise TimeoutError(f"file did not become stable: {source}")

    def _print_saved(self, destination: Path, result: AudioResult) -> None:
        rel = relative_sample_path(destination, self.config.samples_dir)
        print()
        print("Saved:")
        print(rel)
        print(f"Project: {self.project_state.get()}")
        print(f"Trimmed: {'yes' if result.trimmed else 'no'}")
        print(f"Normalized: {'yes' if result.normalized else 'no'}")
        print(f"Duration: {result.duration_sec:.1f} sec")
        self._print_current_settings()
        print()

    def _print_last_action(self, label: str, path: Path, result: AudioResult) -> None:
        print()
        print(f"{label}:")
        print(relative_sample_path(path, self.config.samples_dir))
        print(f"Trimmed: {'yes' if result.trimmed else 'no'}")
        print(f"Normalized: {'yes' if result.normalized else 'no'}")
        print(f"Duration: {result.duration_sec:.1f} sec")
        self._print_current_settings()
        print()

    def _print_current_settings(self) -> None:
        audio = self.processing_state.snapshot()
        trim = "on" if audio.trim else "off"
        normalize = "on" if audio.normalize else "off"
        print(f"Current: project={self.project_state.get()} trim={trim} normalize={normalize}")


class DropEventHandler(FileSystemEventHandler):
    def __init__(self, enqueue: Callable[[Path], None]) -> None:
        self.enqueue = enqueue

    def on_created(self, event: FileSystemEvent) -> None:
        self._handle(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        destination = getattr(event, "dest_path", None)
        if destination:
            self.enqueue(Path(destination))

    def _handle(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self.enqueue(Path(event.src_path))


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def folder_name(now: datetime, organization: OrganizationOptions) -> str | None:
    if organization.folder_granularity == "none":
        return None
    if organization.folder_granularity == "day":
        return now.strftime("%Y-%m-%d")
    if organization.folder_granularity == "week":
        year, week, _weekday = now.isocalendar()
        return f"{year}-W{week:02d}"
    if organization.folder_granularity == "month":
        return now.strftime("%Y-%m")
    raise ValueError(f"unsupported folder granularity: {organization.folder_granularity}")


def relative_sample_path(path: Path, samples_dir: Path) -> str:
    try:
        return str(path.relative_to(samples_dir))
    except ValueError:
        return str(path)


def existing_sample_folder(path: Path, samples_dir: Path) -> str | None:
    try:
        parent = path.parent.relative_to(samples_dir)
    except ValueError:
        return path.parent.name
    if str(parent) == ".":
        return None
    return str(parent)


def toml_key(value: str) -> str:
    if re.match(r"^[A-Za-z0-9_-]+$", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def sanitize_project(project: str) -> str:
    cleaned = PROJECT_RE.sub("-", project.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("project name cannot be empty")
    return cleaned
