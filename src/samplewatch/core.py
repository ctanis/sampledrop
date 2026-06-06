from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
import logging
from pathlib import Path
import re
import threading
import time

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from .audio import AudioProcessor, AudioResult
from .config import AudioOptions, Config


AUDIO_EXTENSIONS = {".wav", ".aiff", ".aif", ".flac"}
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
        self._lock = threading.Lock()

    def next_destination(self, project: str, day: str) -> Path:
        with self._lock:
            day_dir = self.samples_dir / day
            day_dir.mkdir(parents=True, exist_ok=True)
            next_number = self._next_number(day_dir, project)
            return day_dir / f"{project}_{next_number:03d}.wav"

    def _next_number(self, day_dir: Path, project: str) -> int:
        pattern = re.compile(rf"^{re.escape(project)}_(\d{{3}})\.wav$")
        highest = 0
        for path in day_dir.glob(f"{project}_*.wav"):
            match = pattern.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1


class SampleProcessor:
    def __init__(
        self,
        config: Config,
        project_state: ProjectState,
        processing_state: ProcessingState,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.project_state = project_state
        self.processing_state = processing_state
        self.logger = logger
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
                day = datetime.now().strftime("%Y-%m-%d")
                destination = self.sequences.next_destination(project, day)
                temp_destination = destination.with_name(f".{destination.stem}.tmp.wav")
                result = self.audio.process(source, temp_destination, self.processing_state.snapshot())
                temp_destination.replace(destination)
                try:
                    source.unlink()
                except Exception:
                    destination.unlink(missing_ok=True)
                    raise
                self._last_path = destination
                self._print_saved(day, destination, result)
                self.logger.info(
                    "saved source=%s destination=%s trimmed=%s normalized=%s duration=%.3f",
                    source,
                    destination,
                    result.trimmed,
                    result.normalized,
                    result.duration_sec,
                )
        except Exception:
            if "temp_destination" in locals():
                temp_destination.unlink(missing_ok=True)
            with self._seen_lock:
                self._seen.discard(source)
            self.logger.exception("failed source=%s", source)
            print(f"Error processing {source.name}; original left in place.")

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
                return source

            destination = self.sequences.next_destination(target_project, source.parent.name)
            source.replace(destination)
            self._last_path = destination
            self.logger.info("renamed-last source=%s destination=%s", source, destination)
            print("Renamed last:")
            print(f"{destination.parent.name}/{destination.name}")
            return destination

    def _reprocess_last(self, label: str, options: AudioOptions) -> AudioResult | None:
        with self._process_lock:
            source = self._require_last_path()
            temp_destination = source.with_name(f".{source.stem}.tmp.wav")
            try:
                result = self.audio.process(source, temp_destination, options)
                temp_destination.replace(source)
                self._last_path = source
                self.logger.info(
                    "%s path=%s trimmed=%s normalized=%s duration=%.3f",
                    label.lower().replace(" ", "-"),
                    source,
                    result.trimmed,
                    result.normalized,
                    result.duration_sec,
                )
                self._print_last_action(label, source, result)
                return result
            except Exception:
                temp_destination.unlink(missing_ok=True)
                self.logger.exception("failed-last-action path=%s", source)
                print(f"Error updating last file: {source.name}")
                return None

    def _require_last_path(self) -> Path:
        if self._last_path is None:
            raise ValueError("no file has been processed yet")
        if not self._last_path.exists():
            raise ValueError(f"last file no longer exists: {self._last_path}")
        return self._last_path

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

    def _print_saved(self, day: str, destination: Path, result: AudioResult) -> None:
        rel = f"{day}/{destination.name}"
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
        print(f"{path.parent.name}/{path.name}")
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


def sanitize_project(project: str) -> str:
    cleaned = PROJECT_RE.sub("-", project.strip().lower()).strip("-")
    if not cleaned:
        raise ValueError("project name cannot be empty")
    return cleaned
