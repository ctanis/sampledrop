#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import plistlib
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ALFRED_DIR = ROOT / "alfred"
DIST_DIR = ROOT / "dist"
WORKFLOW_PATH = DIST_DIR / "Samplewatch.alfredworkflow"
ALFRED_PATH = (
    'PATH="$HOME/bin:$HOME/.local/share/samplewatch/venv/bin:'
    '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"; export PATH'
)


def main() -> int:
    DIST_DIR.mkdir(exist_ok=True)
    write_info_plist()
    make_executable(ALFRED_DIR / "alfred_filter.py")
    make_executable(ALFRED_DIR / "alfred_run.sh")

    with zipfile.ZipFile(WORKFLOW_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ALFRED_DIR.iterdir()):
            if path.name in {".DS_Store", "__pycache__"}:
                continue
            archive.write(path, path.name)

    print(WORKFLOW_PATH)
    return 0


def write_info_plist() -> None:
    script_filter_uid = "samplewatch.scriptfilter"
    run_script_uid = "samplewatch.runscript"
    plist = {
        "bundleid": "com.samplewatch.alfred",
        "category": "Productivity",
        "connections": {
            script_filter_uid: [
                {
                    "destinationuid": run_script_uid,
                    "modifiers": 0,
                    "modifiersubtext": "",
                    "vitoclose": False,
                }
            ]
        },
        "createdby": "samplewatch",
        "description": "Control samplewatch from Alfred.",
        "disabled": False,
        "name": "Samplewatch",
        "objects": [
            {
                "config": {
                    "argumenttype": 1,
                    "escaping": 102,
                    "keyword": "sd",
                    "queuedelaycustom": 3,
                    "queuedelayimmediatelyinitially": True,
                    "queuedelaymode": 0,
                    "runningsubtext": "Samplewatch",
                    "script": f'{ALFRED_PATH}\npython3 ./alfred_filter.py "$1"',
                    "scriptargtype": 1,
                    "subtext": "Control the detached samplewatch watcher",
                    "title": "Samplewatch",
                    "type": 0,
                    "withspace": True,
                },
                "type": "alfred.workflow.input.scriptfilter",
                "uid": script_filter_uid,
                "version": 3,
            },
            {
                "config": {
                    "concurrently": False,
                    "escaping": 102,
                    "script": 'sh ./alfred_run.sh "$1"',
                    "scriptargtype": 1,
                    "type": 0,
                },
                "type": "alfred.workflow.action.script",
                "uid": run_script_uid,
                "version": 2,
            },
        ],
        "uidata": {
            script_filter_uid: {"xpos": 80, "ypos": 80},
            run_script_uid: {"xpos": 360, "ypos": 80},
        },
        "variables": {},
        "version": "0.1.0",
        "webaddress": "https://github.com/ctanis/sampledrop",
    }
    with (ALFRED_DIR / "info.plist").open("wb") as file:
        plistlib.dump(plist, file)


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


if __name__ == "__main__":
    raise SystemExit(main())
