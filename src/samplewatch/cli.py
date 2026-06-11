from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import json
import logging
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time

from watchdog.observers import Observer

from .config import DEFAULT_CONFIG_PATH, DEFAULT_PID_PATH, DEFAULT_SOCKET_PATH, Config
from .core import DropEventHandler, ProcessingState, ProjectState, SampleProcessor, is_audio_file


@dataclass(frozen=True)
class CommandResult:
    output: str
    stop: bool = False


@dataclass
class Runtime:
    config_path: Path
    config: Config
    project_state: ProjectState
    processing_state: ProcessingState
    processor: SampleProcessor
    logger: logging.Logger
    notifier: Notifier


class Notifier:
    def __init__(self, enabled: bool, logger: logging.Logger) -> None:
        self.enabled = enabled
        self.logger = logger

    def send(self, title: str, message: str) -> bool:
        if not self.enabled:
            return False
        script = (
            f'display notification "{_applescript_string(message)}" '
            f'with title "{_applescript_string(title)}"'
        )
        try:
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            return True
        except Exception:
            self.logger.exception("failed notification title=%s message=%s", title, message)
            return False


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = args.config.expanduser()

    if args.detach:
        return detach(args)

    command = " ".join(args.command).strip()
    if command:
        return send_client_command(command, args.socket.expanduser(), config_path)

    return run_watcher(config_path, args.socket.expanduser(), args.pid.expanduser(), interactive=not args.daemon)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch a drop folder and organize incoming samples.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"TOML config path. Defaults to {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--socket",
        type=Path,
        default=DEFAULT_SOCKET_PATH,
        help=f"Unix socket path for detached command mode. Defaults to {DEFAULT_SOCKET_PATH}",
    )
    parser.add_argument(
        "--pid",
        type=Path,
        default=DEFAULT_PID_PATH,
        help=f"PID file path for detached mode. Defaults to {DEFAULT_PID_PATH}",
    )
    parser.add_argument("--detach", action="store_true", help="Start samplewatch in the background.")
    parser.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to send to a detached watcher.")
    return parser.parse_args(argv)


def detach(args: argparse.Namespace) -> int:
    socket_path = args.socket.expanduser()
    if socket_path.exists() and is_server_running(socket_path):
        print(f"samplewatch already running on {socket_path}")
        return 0

    config = Config.load(args.config.expanduser())
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    log_output = config.log_file.open("a")
    cmd = [
        sys.executable,
        "-m",
        "samplewatch.cli",
        "--daemon",
        "--config",
        str(args.config.expanduser()),
        "--socket",
        str(socket_path),
        "--pid",
        str(args.pid.expanduser()),
    ]
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_output,
        stderr=log_output,
        start_new_session=True,
    )
    log_output.close()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if socket_path.exists() and is_server_running(socket_path):
            print(f"samplewatch detached: {socket_path}")
            return 0
        time.sleep(0.1)

    print("samplewatch detach requested, but the command socket did not appear yet")
    return 1


def run_watcher(config_path: Path, socket_path: Path, pid_path: Path, interactive: bool) -> int:
    config = Config.load(config_path)
    config.drop_dir.mkdir(parents=True, exist_ok=True)
    config.samples_dir.mkdir(parents=True, exist_ok=True)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(config.log_file, log_started=False)
    notifier = Notifier(config.notifications.enabled, logger)
    runtime = Runtime(
        config_path=config_path,
        config=config,
        project_state=ProjectState(config.project),
        processing_state=ProcessingState(config.audio),
        processor=None,  # type: ignore[arg-type]
        logger=logger,
        notifier=notifier,
    )
    runtime.processor = SampleProcessor(
        config,
        runtime.project_state,
        runtime.processing_state,
        logger,
        notify=notifier.send,
    )

    maybe_open_finder(config, logger)

    stop_event = threading.Event()
    work_queue: queue.Queue[Path | None] = queue.Queue()
    worker = threading.Thread(target=process_queue, args=(work_queue, runtime.processor), daemon=True)
    worker.start()

    observer = Observer()
    observer.schedule(DropEventHandler(work_queue.put), str(config.drop_dir), recursive=False)
    observer.start()

    if socket_path.exists() and is_server_running(socket_path):
        print(f"samplewatch already running on {socket_path}")
        return 1

    server = CommandServer(socket_path, runtime, stop_event)
    server.start()
    write_pid(pid_path)

    for path in sorted(config.drop_dir.iterdir()):
        if is_audio_file(path):
            work_queue.put(path)

    if interactive:
        print_startup(config, runtime.project_state, runtime.processing_state)
    else:
        logger.info("daemon started socket=%s pid=%s", socket_path, pid_path)
        notifier.send("Samplewatch running", f"Watching {config.drop_dir}")

    try:
        if interactive:
            command_loop(runtime, stop_event)
        else:
            stop_event.wait()
        return 0
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        stop_event.set()
        observer.stop()
        observer.join(timeout=5)
        work_queue.put(None)
        worker.join(timeout=5)
        server.stop()
        save_exit_state(config_path, config, runtime.project_state, runtime.processing_state, logger)
        cleanup_runtime_files(socket_path, pid_path)
        logger.info("stopped")
        if not interactive:
            notifier.send("Samplewatch stopped", "Watcher shut down cleanly")
        if interactive:
            print("samplewatch stopped")


def print_startup(config: Config, project_state: ProjectState, processing_state: ProcessingState) -> None:
    print("samplewatch running")
    print(f"Drop folder: {config.drop_dir}")
    print(f"Samples: {config.samples_dir}")
    print_settings(project_state, processing_state)
    print(
        "Commands: project/p <name>, trim/t [on|off], normalize/n [on|off], "
        "status/s, notify, quit/q, stop, lt, ln, lp [name], d, x"
    )


def process_queue(work_queue: queue.Queue[Path | None], processor: SampleProcessor) -> None:
    while True:
        path = work_queue.get()
        try:
            if path is None:
                return
            processor.maybe_process(path)
        finally:
            work_queue.task_done()


def command_loop(runtime: Runtime, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        line = sys.stdin.readline()
        if line == "":
            time.sleep(0.1)
            continue

        result = handle_command(line.strip(), runtime, allow_quit=True)
        if result.output:
            print(result.output, end="" if result.output.endswith("\n") else "\n")
        if result.stop:
            stop_event.set()


def handle_command(command: str, runtime: Runtime, allow_quit: bool) -> CommandResult:
    if not command:
        return CommandResult("")

    buffer = io.StringIO()
    stop = False
    with redirect_stdout(buffer):
        try:
            stop = execute_command(command, runtime, allow_quit)
        except ValueError as exc:
            print(f"Error: {exc}")
    return CommandResult(buffer.getvalue(), stop)


def execute_command(command: str, runtime: Runtime, allow_quit: bool) -> bool:
    if command in {"stop"} or (allow_quit and command in {"quit", "q", "exit"}):
        print("Stopping samplewatch")
        return True
    if command in {"quit", "q", "exit"}:
        print("Use 'stop' to terminate the detached watcher.")
        return False
    if command in {"project", "p"}:
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Samplewatch project")
        return False
    if command.startswith("project "):
        project = set_project(runtime.project_state, runtime.processing_state, command.removeprefix("project "))
        notify_settings(runtime, "Project set", f"project={project}")
        return False
    if command.startswith("p "):
        project = set_project(runtime.project_state, runtime.processing_state, command.removeprefix("p "))
        notify_settings(runtime, "Project set", f"project={project}")
        return False
    if command in {"trim", "t"}:
        enabled = runtime.processing_state.toggle_trim()
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Trim toggled", f"trim={'on' if enabled else 'off'}")
        return False
    if command.startswith("trim "):
        enabled = runtime.processing_state.set_trim(parse_enabled(command.removeprefix("trim ")))
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Trim set", f"trim={'on' if enabled else 'off'}")
        return False
    if command.startswith("t "):
        enabled = runtime.processing_state.set_trim(parse_enabled(command.removeprefix("t ")))
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Trim set", f"trim={'on' if enabled else 'off'}")
        return False
    if command in {"normalize", "norm", "n"}:
        enabled = runtime.processing_state.toggle_normalize()
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Normalize toggled", f"normalize={'on' if enabled else 'off'}")
        return False
    if command.startswith("normalize "):
        enabled = runtime.processing_state.set_normalize(parse_enabled(command.removeprefix("normalize ")))
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Normalize set", f"normalize={'on' if enabled else 'off'}")
        return False
    if command.startswith("norm "):
        enabled = runtime.processing_state.set_normalize(parse_enabled(command.removeprefix("norm ")))
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Normalize set", f"normalize={'on' if enabled else 'off'}")
        return False
    if command.startswith("n "):
        enabled = runtime.processing_state.set_normalize(parse_enabled(command.removeprefix("n ")))
        print_settings(runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Normalize set", f"normalize={'on' if enabled else 'off'}")
        return False
    if command in {"!t", "lt"}:
        runtime.processor.trim_last()
        print_settings(runtime.project_state, runtime.processing_state)
        return False
    if command in {"!n", "ln"}:
        runtime.processor.normalize_last()
        print_settings(runtime.project_state, runtime.processing_state)
        return False
    if command in {"!p", "lp"}:
        runtime.processor.rename_last()
        print_settings(runtime.project_state, runtime.processing_state)
        return False
    if command.startswith("!p "):
        runtime.processor.rename_last(command.removeprefix("!p "))
        print_settings(runtime.project_state, runtime.processing_state)
        return False
    if command.startswith("lp "):
        runtime.processor.rename_last(command.removeprefix("lp "))
        print_settings(runtime.project_state, runtime.processing_state)
        return False
    if command in {"!d", "d"}:
        open_finder_drop(runtime.config, runtime.logger)
        print_settings(runtime.project_state, runtime.processing_state)
        runtime.notifier.send("Drop folder opened", str(runtime.config.drop_dir))
        return False
    if command in {"!x", "x"}:
        path = reveal_last_product(runtime.processor, runtime.logger)
        print_settings(runtime.project_state, runtime.processing_state)
        runtime.notifier.send("Last sample revealed", str(path))
        return False
    if command in {"notify", "notification"}:
        sent = runtime.notifier.send("Samplewatch test", format_settings(runtime.project_state, runtime.processing_state))
        print("Notification sent" if sent else "Notification not sent")
        print_settings(runtime.project_state, runtime.processing_state)
        return False
    if command in {"status", "s"}:
        print_status(runtime.config, runtime.project_state, runtime.processing_state)
        notify_settings(runtime, "Samplewatch status")
        return False

    print(
        "Unknown command. Try: project/p <name>, trim/t [on|off], "
        "normalize/n [on|off], status/s, notify, stop, lt, ln, lp [name], d, x"
    )
    return False


class CommandServer:
    def __init__(self, socket_path: Path, runtime: Runtime, stop_event: threading.Event) -> None:
        self.socket_path = socket_path
        self.runtime = runtime
        self.stop_event = stop_event
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None

    def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.socket_path))
        self._sock.listen(5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _serve(self) -> None:
        assert self._sock is not None
        while not self.stop_event.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                self._handle_client(conn)

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            payload = conn.recv(8192).decode().strip()
            request = json.loads(payload)
            command = str(request.get("command", ""))
            result = handle_command(command, self.runtime, allow_quit=False)
            if result.stop:
                self.stop_event.set()
            response = {"ok": True, "output": result.output, "stop": result.stop}
        except Exception as exc:
            response = {"ok": False, "output": f"Error: {exc}\n", "stop": False}
        conn.sendall(json.dumps(response).encode() + b"\n")


def send_client_command(command: str, socket_path: Path, config_path: Path) -> int:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall(json.dumps({"command": command}).encode() + b"\n")
            raw = client.recv(65536).decode()
    except FileNotFoundError:
        if command in {"notify", "notification"}:
            return send_standalone_notification(config_path)
        if command in {"status", "s"}:
            print_stopped_status(socket_path)
            send_standalone_status_notification(config_path, "Backend not running", str(socket_path))
            return 0
        print(f"samplewatch is not running; no socket at {socket_path}")
        return 1
    except ConnectionRefusedError:
        if command in {"status", "s"}:
            print_unreachable_status(socket_path)
            send_standalone_status_notification(config_path, "Backend unreachable", str(socket_path))
            return 1
        print(f"samplewatch socket is not accepting commands: {socket_path}")
        return 1
    except OSError as exc:
        if command in {"status", "s"}:
            print_unreachable_status(socket_path, str(exc))
            send_standalone_status_notification(config_path, "Backend unreachable", str(exc))
            return 1
        print(f"Could not contact samplewatch: {exc}")
        return 1

    response = json.loads(raw)
    output = str(response.get("output", ""))
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    return 0 if response.get("ok") else 1


def send_standalone_notification(config_path: Path) -> int:
    config = Config.load(config_path)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(config.log_file, log_started=False)
    notifier = Notifier(config.notifications.enabled, logger)
    sent = notifier.send("Samplewatch test", "Notification Center test")
    print("Notification sent" if sent else "Notification not sent")
    return 0 if sent else 1


def send_standalone_status_notification(config_path: Path, title: str, message: str) -> None:
    config = Config.load(config_path)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = setup_logging(config.log_file)
    Notifier(config.notifications.enabled, logger).send(f"Samplewatch status: {title}", message)


def is_server_running(socket_path: Path) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(str(socket_path))
            client.sendall(json.dumps({"command": "status"}).encode() + b"\n")
            client.recv(1024)
        return True
    except OSError:
        return False


def set_project(project_state: ProjectState, processing_state: ProcessingState, raw_project: str) -> str:
    project = project_state.set(raw_project)
    print(f"Project set: {project}")
    print_settings(project_state, processing_state)
    return project


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


def notify_settings(runtime: Runtime, title: str, message: str | None = None) -> None:
    runtime.notifier.send(title, message or format_settings(runtime.project_state, runtime.processing_state))


def print_status(config: Config, project_state: ProjectState, processing_state: ProcessingState) -> None:
    audio = processing_state.snapshot()
    print("Status:")
    print("Backend: running")
    print(f"Project: {project_state.get()}")
    print(f"Drop folder: {config.drop_dir}")
    print(f"Samples: {config.samples_dir}")
    print(f"Trim: {'on' if audio.trim else 'off'}")
    print(f"Normalize: {'on' if audio.normalize else 'off'}")
    print(f"Normalize target: {audio.normalize_target_dbfs:.1f} dBFS")
    print(f"Fallback output subtype: {audio.fallback_output_subtype}")
    print(f"Fallback sample rate: {audio.fallback_sample_rate} Hz")
    print(f"Open Finder on launch: {'yes' if config.launch.open_finder else 'no'}")
    print(f"Finder hide toolbar: {'yes' if config.launch.finder_hide_toolbar else 'no'}")
    if config.launch.finder_background_image:
        print(f"Finder background: {config.launch.finder_background_image}")
    print(f"Notifications: {'on' if config.notifications.enabled else 'off'}")
    print(f"Log: {config.log_file}")


def print_stopped_status(socket_path: Path) -> None:
    print("Status:")
    print("Backend: not running")
    print(f"Socket: {socket_path}")


def print_unreachable_status(socket_path: Path, detail: str | None = None) -> None:
    print("Status:")
    print("Backend: unreachable")
    print(f"Socket: {socket_path}")
    if detail:
        print(f"Error: {detail}")


def maybe_open_finder(config: Config, logger: logging.Logger) -> None:
    if not config.launch.open_finder:
        return
    open_finder_drop(config, logger)


def open_finder_drop(config: Config, logger: logging.Logger) -> None:
    left = config.launch.finder_left
    top = config.launch.finder_top
    right = left + config.launch.finder_width
    bottom = top + config.launch.finder_height
    toolbar_line = (
        "  set toolbar visible of front window to false"
        if config.launch.finder_hide_toolbar
        else "  set toolbar visible of front window to true"
    )
    background_line = ""
    if config.launch.finder_background_image:
        if config.launch.finder_background_image.exists():
            background_line = (
                "  set background picture of icon view options of front window "
                f'to (POSIX file "{_applescript_string(str(config.launch.finder_background_image))}")'
            )
        else:
            logger.warning("Finder background image not found path=%s", config.launch.finder_background_image)
    script = f"""
tell application "Finder"
  activate
  open POSIX file "{_applescript_string(str(config.drop_dir))}"
  set bounds of front window to {{{left}, {top}, {right}, {bottom}}}
  set current view of front window to icon view
{toolbar_line}
{background_line}
end tell
"""
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
        print(f"Finder: opened drop folder {config.drop_dir}")
    except Exception:
        logger.exception("failed to open Finder drop folder=%s", config.drop_dir)
        print(f"Warning: could not open Finder for {config.drop_dir}")


def reveal_last_product(processor: SampleProcessor, logger: logging.Logger) -> Path:
    path = processor.last_path()
    script = f"""
tell application "Finder"
  activate
  reveal POSIX file "{_applescript_string(str(path))}"
end tell
"""
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
        print(f"Finder: revealed last file {path}")
    except Exception:
        logger.exception("failed to reveal last file=%s", path)
        print(f"Warning: could not reveal last file {path}")
    return path


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


def setup_logging(log_file: Path, log_started: bool = True) -> logging.Logger:
    logger = logging.getLogger("samplewatch")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    if log_started:
        logger.info("started")
    return logger


def write_pid(pid_path: Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os_getpid()}\n")


def cleanup_runtime_files(socket_path: Path, pid_path: Path) -> None:
    socket_path.unlink(missing_ok=True)
    pid_path.unlink(missing_ok=True)


def os_getpid() -> int:
    import os

    return os.getpid()


if __name__ == "__main__":
    raise SystemExit(main())
