from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = Path("~/.samplewatch.toml").expanduser()
DEFAULT_SOCKET_PATH = Path("~/.samplewatch.sock").expanduser()
DEFAULT_PID_PATH = Path("~/.samplewatch.pid").expanduser()


@dataclass(frozen=True)
class AudioOptions:
    trim: bool = True
    normalize: bool = True
    normalize_target_dbfs: float = -1.0
    fallback_output_subtype: str = "PCM_24"
    fallback_sample_rate: int = 44100
    silence_threshold_dbfs: float = -50.0
    stable_checks: int = 3
    stable_interval_sec: float = 0.5
    write_timeout_sec: float = 60.0


@dataclass(frozen=True)
class LaunchOptions:
    open_finder: bool = True
    finder_left: int = 80
    finder_top: int = 80
    finder_width: int = 520
    finder_height: int = 360
    finder_hide_toolbar: bool = True
    finder_background_image: Path | None = None


@dataclass(frozen=True)
class NotificationOptions:
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    drop_dir: Path
    samples_dir: Path
    project: str
    log_file: Path
    audio: AudioOptions
    launch: LaunchOptions
    notifications: NotificationOptions

    @classmethod
    def load(cls, path: Path) -> "Config":
        data = tomllib.loads(path.read_text()) if path.exists() else {}
        general = data.get("general", {})
        audio = data.get("audio", {})
        launch = data.get("launch", {})
        notifications = data.get("notifications", {})

        drop_dir = _expand_path(general.get("drop_dir", "~/SampleDrop"))
        samples_dir = _expand_path(general.get("samples_dir", "~/Samples"))
        log_file = _expand_path(general.get("log_file", "~/.samplewatch.log"))

        return cls(
            drop_dir=drop_dir,
            samples_dir=samples_dir,
            project=str(general.get("project", "samples")),
            log_file=log_file,
            audio=AudioOptions(
                trim=bool(audio.get("trim", True)),
                normalize=bool(audio.get("normalize", True)),
                normalize_target_dbfs=float(audio.get("normalize_target_dbfs", -1.0)),
                fallback_output_subtype=str(audio.get("fallback_output_subtype", "PCM_24")),
                fallback_sample_rate=int(audio.get("fallback_sample_rate", 44100)),
                silence_threshold_dbfs=float(audio.get("silence_threshold_dbfs", -50.0)),
                stable_checks=int(audio.get("stable_checks", 3)),
                stable_interval_sec=float(audio.get("stable_interval_sec", 0.5)),
                write_timeout_sec=float(audio.get("write_timeout_sec", 60.0)),
            ),
            launch=LaunchOptions(
                open_finder=bool(launch.get("open_finder", True)),
                finder_left=int(launch.get("finder_left", 80)),
                finder_top=int(launch.get("finder_top", 80)),
                finder_width=int(launch.get("finder_width", 520)),
                finder_height=int(launch.get("finder_height", 360)),
                finder_hide_toolbar=bool(launch.get("finder_hide_toolbar", True)),
                finder_background_image=_expand_optional_path(launch.get("finder_background_image")),
            ),
            notifications=NotificationOptions(
                enabled=bool(notifications.get("enabled", True)),
            ),
        )

    def save(self, path: Path, project: str, audio: AudioOptions) -> None:
        path = path.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "[general]",
                    f'drop_dir = "{_toml_string(str(self.drop_dir))}"',
                    f'samples_dir = "{_toml_string(str(self.samples_dir))}"',
                    f'project = "{_toml_string(project)}"',
                    f'log_file = "{_toml_string(str(self.log_file))}"',
                    "",
                    "[audio]",
                    f"trim = {_toml_bool(audio.trim)}",
                    f"normalize = {_toml_bool(audio.normalize)}",
                    f"normalize_target_dbfs = {audio.normalize_target_dbfs:.1f}",
                    f'fallback_output_subtype = "{_toml_string(audio.fallback_output_subtype)}"',
                    f"fallback_sample_rate = {audio.fallback_sample_rate}",
                    f"silence_threshold_dbfs = {audio.silence_threshold_dbfs:.1f}",
                    f"stable_checks = {audio.stable_checks}",
                    f"stable_interval_sec = {audio.stable_interval_sec:g}",
                    f"write_timeout_sec = {audio.write_timeout_sec:g}",
                    "",
                    "[launch]",
                    f"open_finder = {_toml_bool(self.launch.open_finder)}",
                    f"finder_left = {self.launch.finder_left}",
                    f"finder_top = {self.launch.finder_top}",
                    f"finder_width = {self.launch.finder_width}",
                    f"finder_height = {self.launch.finder_height}",
                    f"finder_hide_toolbar = {_toml_bool(self.launch.finder_hide_toolbar)}",
                    *(
                        [f'finder_background_image = "{_toml_string(str(self.launch.finder_background_image))}"']
                        if self.launch.finder_background_image
                        else []
                    ),
                    "",
                    "[notifications]",
                    f"enabled = {_toml_bool(self.notifications.enabled)}",
                    "",
                ]
            )
        )


def _expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _expand_optional_path(value: object) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _expand_path(text)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
