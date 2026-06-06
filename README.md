# samplewatch

`samplewatch` is a small Python command-line utility for a single-user macOS sampling workflow. It watches a drop folder for incoming audio, waits until each file is fully written, then saves a cleaned WAV into dated sample folders.

## Features

- Watches a configured drop folder, such as `~/SampleDrop`
- Supports `.wav`, `.aiff`, `.aif`, and `.flac` inputs
- Writes organized WAV files to `Samples/YYYY-MM-DD/`
- Maintains an in-memory project name while running
- Treats the drop folder as a simple spool directory
- Uses per-project, per-day numbering like `phaseplant_001.wav`
- Optionally trims leading/trailing silence, with runtime toggles
- Optionally normalizes peak level, with runtime toggles, defaulting to `-1.0 dBFS`
- Deletes the original only after the processed file is safely written
- Saves project, trim, and normalize state back to the config on exit
- Optionally opens the drop folder in Finder at launch
- Shows macOS Notification Center popups for detached watcher activity
- Logs concise processing results to a simple log file

## Install

Python 3.11 or newer is required.

On macOS, install libsndfile first if needed:

```sh
brew install libsndfile
```

Install as a personal utility:

```sh
scripts/install.sh
```

This creates:

```text
~/.local/share/samplewatch/venv/
~/bin/samplewatch -> ~/.local/share/samplewatch/venv/bin/samplewatch
```

The installer copies `samplewatch.example.toml` to `~/.samplewatch.toml` only if no config exists yet. If `~/bin` is not on your `PATH`, the installer prints the shell line to add.

Upgrade from this repo:

```sh
scripts/upgrade.sh
```

Uninstall the venv and symlink, while preserving config and logs:

```sh
scripts/uninstall.sh
```

For development, you can still use a repo-local venv and `pip install -e .`.

## Configure

Copy the example config:

```sh
cp samplewatch.example.toml ~/.samplewatch.toml
```

Example:

```toml
[general]
drop_dir = "~/SampleDrop"
samples_dir = "~/Samples"
project = "phaseplant"
log_file = "~/.samplewatch.log"

[audio]
trim = true
normalize = true
normalize_target_dbfs = -1.0
silence_threshold_dbfs = -50.0
stable_checks = 3
stable_interval_sec = 0.5
write_timeout_sec = 60.0

[launch]
open_finder = true
finder_left = 80
finder_top = 80
finder_width = 520
finder_height = 360

[notifications]
enabled = true
```

If no config file exists, `samplewatch` uses these same defaults.

## Run

Foreground interactive mode:

```sh
samplewatch
```

Detached watcher mode:

```sh
samplewatch --detach
```

One-shot client commands, suitable for Alfred:

```sh
samplewatch status
samplewatch p modular
samplewatch t off
samplewatch d
samplewatch x
samplewatch notify
samplewatch stop
```

Use a custom config:

```sh
samplewatch --config ./samplewatch.example.toml
```

While running, type commands into the same terminal:

```text
project phaseplant
p modular
project
t
trim
trim off
n
normalize
normalize off
s
status
notify
lt
ln
lp
lp phaseplant
d
x
q
quit
stop
```

`project <name>` and `p <name>` set the current project. `project` or `p` prints it. `trim` or `t` toggles trimming. `normalize`, `norm`, or `n` toggles normalizing. `trim on|off`, `t on|off`, `normalize on|off`, and `n on|off` set those options explicitly. `status` or `s` prints the active configuration. `notify` sends a test Notification Center popup. `quit` or `q` exits foreground interactive mode. `stop` terminates a detached watcher. State-changing commands print the current project, trim, and normalize settings immediately.

Last-file and Finder commands:

- `lt` trims the last saved file.
- `ln` normalizes the last saved file.
- `lp` renames the last saved file to the current project sequence.
- `lp <name>` renames the last saved file to that project sequence and makes it the current project for future files.
- `d` reopens the drop folder Finder window.
- `x` reveals the last saved file in Finder.

The older `!t`, `!n`, `!p`, `!d`, and `!x` forms also work inside samplewatch's interactive prompt. At a shell prompt, prefer the aliases above because bash treats `!` as history expansion.

## Output

Dropping `take.aiff` into `~/SampleDrop` with project `phaseplant` might create:

```text
~/Samples/
└── 2026-06-06/
    └── phaseplant_001.wav
```

Terminal output looks like:

```text
Saved:
2026-06-06/phaseplant_004.wav
Project: phaseplant
Trimmed: yes
Normalized: yes
Duration: 12.4 sec
Current: project=phaseplant trim=on normalize=on
```

To open the spool folder in a small Finder window at startup:

```toml
[launch]
open_finder = true
finder_left = 80
finder_top = 80
finder_width = 520
finder_height = 360
```

Finder window sizing is best-effort and macOS-only. If the Finder helper fails, `samplewatch` keeps running and writes the failure to the log.

Notifications use macOS Notification Center and can be disabled:

```toml
[notifications]
enabled = false
```

Detached mode uses a local Unix socket and PID file:

```text
~/.samplewatch.sock
~/.samplewatch.pid
```

Alfred workflows can call the same one-shot commands, for example `samplewatch p phaseplant` or `samplewatch x`.

## Alfred

Build the Alfred workflow:

```sh
scripts/build_alfred_workflow.py
```

Then open:

```text
dist/Samplewatch.alfredworkflow
```

The workflow keyword is `sd`. Examples:

```text
sd status
sd start
sd stop
sd phaseplant
sd p modular
sd lp phaseplant
sd x
sd d
```

Install `samplewatch` first with `scripts/install.sh`, because the Alfred workflow calls the installed `samplewatch` command.
`status` includes whether the detached backend is running. When the detached watcher is running, `sd status` also shows a silent Notification Center summary of the backend, current project, trim, normalize, and notification settings.

## Notes

- Sequence numbers are calculated from existing files in the destination day folder.
- Project names are normalized to lowercase slug names, so `Digitakt Kit` becomes `digitakt-kit`.
- Audio is written as WAV regardless of input format.
- If processing fails, the original file is left in the drop folder and the failure is written to the log.
- Project, trim, and normalize exit state is written to the active config file as the next run's defaults.
- Bang commands edit only the last successfully saved file and do not change trim/normalize defaults.
- Launch helper settings are preserved when exit state is saved.
- Notification settings are preserved when exit state is saved.

## Future Extensions

- Optional subfolders by source app, BPM, key, or input format
- A `skip normalize once` command for special recordings
- Configurable output bit depth or sample rate conversion
- Sidecar metadata files for notes, tags, or hardware chain
- A tiny menu bar companion that sends project-name commands to the watcher
