#!/usr/bin/env python3
from __future__ import annotations

import json
import sys


ACTIONS = [
    ("status", "Status", "Show current samplewatch state"),
    ("start", "Start watcher", "Start samplewatch in detached mode"),
    ("stop", "Stop watcher", "Stop the detached samplewatch watcher"),
    ("d", "Open drop folder", "Reopen the Finder drop window"),
    ("x", "Reveal last sample", "Show the last saved sample in Finder"),
    ("t", "Toggle trim", "Toggle trimming for future samples"),
    ("t on", "Trim on", "Enable trimming"),
    ("t off", "Trim off", "Disable trimming"),
    ("n", "Toggle normalize", "Toggle normalizing for future samples"),
    ("n on", "Normalize on", "Enable normalizing"),
    ("n off", "Normalize off", "Disable normalizing"),
    ("lt", "Trim last sample", "Trim the last saved sample"),
    ("ln", "Normalize last sample", "Normalize the last saved sample"),
    ("lp", "Rename last to current project", "Rename the last sample using the active project"),
    ("notify", "Test notification", "Send a Samplewatch Notification Center test"),
]


def main() -> int:
    query = " ".join(sys.argv[1:]).strip()
    items = []

    if query.startswith("p "):
        project = query.removeprefix("p ").strip()
        items.append(item(f"p {project}", f"Set project: {project}", "Set active samplewatch project"))
    elif query.startswith("project "):
        project = query.removeprefix("project ").strip()
        items.append(item(f"p {project}", f"Set project: {project}", "Set active samplewatch project"))
    elif query.startswith("lp "):
        project = query.removeprefix("lp ").strip()
        items.append(item(f"lp {project}", f"Rename last sample: {project}", "Rename last sample and set project"))
    else:
        needle = query.lower()
        exact_matches = [
            item(command, title, subtitle)
            for command, title, subtitle in ACTIONS
            if needle and needle == command
        ]
        if exact_matches:
            items.extend(exact_matches)
        else:
            for command, title, subtitle in ACTIONS:
                haystack = f"{command} {title} {subtitle}".lower()
                if not needle or needle in haystack:
                    items.append(item(command, title, subtitle))

        if query and not items:
            items.append(item(f"p {query}", f"Set project: {query}", "Set active samplewatch project"))
            items.append(item(f"lp {query}", f"Rename last sample: {query}", "Rename last sample and set project"))

    print(json.dumps({"items": items}))
    return 0


def item(command: str, title: str, subtitle: str) -> dict[str, object]:
    return {
        "title": title,
        "subtitle": subtitle,
        "arg": command,
        "valid": True,
        "text": {"copy": f"samplewatch {command}", "largetype": title},
    }


if __name__ == "__main__":
    raise SystemExit(main())
