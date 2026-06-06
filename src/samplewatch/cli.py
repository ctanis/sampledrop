from __future__ import annotations

import argparse
import logging
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from watchdog.observers import Observer

from .config import DEFAULT_CONFIG_PATH, Config
from .core import DropEventHandler, ProcessingState, ProjectState, SampleProcessor, is_audio_file


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = args.config.expanduser()
    config = Config.load(config_path)

    config.drop_dir.mkdir(parents=True, exist_ok=True)
    config.samples_dir.mkdir(parents=True, exist_ok=True)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(config.log_file)
    project_state = ProjectState(config.project)
    processing_state = ProcessingState(config.audio)
    processor = SampleProcessor(config, project_state, processing_state, logger)
    maybe_open_finder(config, logger)

    work_queue: queue.Queue[Path | None] = queue.Queue()
    worker = threading.Thread(target=process_queue, args=(work_queue, processor), daemon=True)
    worker.start()

    observer = Observer()
    observer.schedule(DropEventHandler(work_queue.put), str(config.drop_dir), recursive=False)
    observer.start()

    for path in sorted(config.drop_dir.iterdir()):
        if is_audio_file(path):
            work_queue.put(path)

    print("samplewatch running")
    print(f"Drop folder: {config.drop_dir}")
    print(f"Samples: {config.samples_dir}")
    print_settings(project_state, processing_state)
    print(
        "Commands: project/p <name>, trim/t [on|off], normalize/n [on|off], "
        "status/s, quit/q, !t, !n, !p [name]"
    )

    try:
        return command_loop(config, project_state, processing_state, processor)
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        observer.stop()
        observer.join(timeout=5)
        work_queue.put(None)
        worker.join(timeout=5)
        save_exit_state(config_path, config, project_state, processing_state, logger)
        logger.info("stopped")
        print("samplewatch stopped")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a drop folder and organize incoming samples.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"TOML config path. Defaults to {DEFAULT_CONFIG_PATH}",
    )
    return parser.parse_args(argv)


def process_queue(work_queue: queue.Queue[Path | None], processor: SampleProcessor) -> None:
    while True:
        path = work_queue.get()
        try:
            if path is None:
                return
            processor.maybe_process(path)
        finally:
            work_queue.task_done()


def command_loop(
    config: Config,
    project_state: ProjectState,
    processing_state: ProcessingState,
    processor: SampleProcessor,
) -> int:
    while True:
        line = sys.stdin.readline()
        if line == "":
            time.sleep(0.1)
            continue

        command = line.strip()
        if not command:
            continue

        try:
            if command in {"quit", "q", "exit"}:
                return 0
            if command in {"project", "p"}:
                print_settings(project_state, processing_state)
                continue
            if command.startswith("project "):
                set_project(project_state, processing_state, command.removeprefix("project "))
                continue
            if command.startswith("p "):
                set_project(project_state, processing_state, command.removeprefix("p "))
                continue
            if command in {"trim", "t"}:
                processing_state.toggle_trim()
                print_settings(project_state, processing_state)
                continue
            if command.startswith("trim "):
                processing_state.set_trim(parse_enabled(command.removeprefix("trim ")))
                print_settings(project_state, processing_state)
                continue
            if command.startswith("t "):
                processing_state.set_trim(parse_enabled(command.removeprefix("t ")))
                print_settings(project_state, processing_state)
                continue
            if command in {"normalize", "norm", "n"}:
                processing_state.toggle_normalize()
                print_settings(project_state, processing_state)
                continue
            if command.startswith("normalize "):
                processing_state.set_normalize(parse_enabled(command.removeprefix("normalize ")))
                print_settings(project_state, processing_state)
                continue
            if command.startswith("norm "):
                processing_state.set_normalize(parse_enabled(command.removeprefix("norm ")))
                print_settings(project_state, processing_state)
                continue
            if command.startswith("n "):
                processing_state.set_normalize(parse_enabled(command.removeprefix("n ")))
                print_settings(project_state, processing_state)
                continue
            if command == "!t":
                processor.trim_last()
                print_settings(project_state, processing_state)
                continue
            if command == "!n":
                processor.normalize_last()
                print_settings(project_state, processing_state)
                continue
            if command == "!p":
                processor.rename_last()
                print_settings(project_state, processing_state)
                continue
            if command.startswith("!p "):
                processor.rename_last(command.removeprefix("!p "))
                print_settings(project_state, processing_state)
                continue
            if command in {"status", "s"}:
                print_status(config, project_state, processing_state)
                continue
            print(
                "Unknown command. Try: project/p <name>, trim/t [on|off], "
                "normalize/n [on|off], status/s, quit/q, !t, !n, !p [name]"
            )
        except ValueError as exc:
            print(f"Error: {exc}")


def set_project(project_state: ProjectState, processing_state: ProcessingState, raw_project: str) -> None:
    project = project_state.set(raw_project)
    print(f"Project set: {project}")
    print_settings(project_state, processing_state)


def parse_enabled(raw_value: str) -> bool:
    value = raw_value.strip().lower()
    if value in {"on", "yes", "true", "1", "enable", "enabled"}:
        return True
    if value in {"off", "no", "false", "0", "disable", "disabled"}:
        return False
    raise ValueError("expected on or off")


def format_settings(project_state: ProjectState, processing_state: ProcessingState) -> str:
    audio = processing_state.snapshot()
    trim = "on" if audio.trim else "off"
    normalize = "on" if audio.normalize else "off"
    return f"Current: project={project_state.get()} trim={trim} normalize={normalize}"


def print_settings(project_state: ProjectState, processing_state: ProcessingState) -> None:
    print(format_settings(project_state, processing_state))


def print_status(config: Config, project_state: ProjectState, processing_state: ProcessingState) -> None:
    audio = processing_state.snapshot()
    print("Status:")
    print(f"Project: {project_state.get()}")
    print(f"Drop folder: {config.drop_dir}")
    print(f"Samples: {config.samples_dir}")
    print(f"Trim: {'on' if audio.trim else 'off'}")
    print(f"Normalize: {'on' if audio.normalize else 'off'}")
    print(f"Normalize target: {audio.normalize_target_dbfs:.1f} dBFS")
    print(f"Open Finder on launch: {'yes' if config.launch.open_finder else 'no'}")
    print(f"Log: {config.log_file}")


def maybe_open_finder(config: Config, logger: logging.Logger) -> None:
    if not config.launch.open_finder:
        return

    left = config.launch.finder_left
    top = config.launch.finder_top
    right = left + config.launch.finder_width
    bottom = top + config.launch.finder_height
    script = f"""
tell application "Finder"
  activate
  open POSIX file "{_applescript_string(str(config.drop_dir))}"
  set bounds of front window to {{{left}, {top}, {right}, {bottom}}}
end tell
"""
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
        print(f"Finder: opened drop folder {config.drop_dir}")
    except Exception:
        logger.exception("failed to open Finder drop folder=%s", config.drop_dir)
        print(f"Warning: could not open Finder for {config.drop_dir}")


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def save_exit_state(
    config_path: Path,
    config: Config,
    project_state: ProjectState,
    processing_state: ProcessingState,
    logger: logging.Logger,
) -> None:
    try:
        config.save(config_path, project_state.get(), processing_state.snapshot())
        logger.info("saved config=%s", config_path)
    except Exception:
        logger.exception("failed to save config=%s", config_path)
        print(f"Warning: could not save exit state to {config_path}")


def setup_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("samplewatch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("started")
    return logger


if __name__ == "__main__":
    raise SystemExit(main())
