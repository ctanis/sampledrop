from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import AudioOptions


@dataclass(frozen=True)
class AudioResult:
    trimmed: bool
    normalized: bool
    duration_sec: float


class AudioProcessor:
    def process(self, source: Path, destination: Path, options: AudioOptions) -> AudioResult:
        data, sample_rate = sf.read(source, always_2d=True)
        if data.size == 0:
            raise ValueError("audio file contains no samples")

        processed = data
        trimmed = False
        normalized = False

        if options.trim:
            processed, trimmed = self._trim_silence(processed, options)

        if options.normalize:
            processed, normalized = self._normalize(processed, options)

        destination.parent.mkdir(parents=True, exist_ok=True)
        sf.write(destination, processed, sample_rate, format="WAV")
        duration_sec = len(processed) / float(sample_rate)
        return AudioResult(trimmed=trimmed, normalized=normalized, duration_sec=duration_sec)

    def _trim_silence(self, data: np.ndarray, options: AudioOptions) -> tuple[np.ndarray, bool]:
        threshold = 10 ** (options.silence_threshold_dbfs / 20.0)
        amplitude = np.max(np.abs(data), axis=1)
        audible = np.flatnonzero(amplitude > threshold)

        if audible.size == 0:
            return data, False

        start = int(audible[0])
        end = int(audible[-1]) + 1
        if start == 0 and end == len(data):
            return data, False

        return data[start:end], True

    def _normalize(self, data: np.ndarray, options: AudioOptions) -> tuple[np.ndarray, bool]:
        peak = float(np.max(np.abs(data)))
        if peak <= 0.0:
            return data, False

        target_peak = 10 ** (options.normalize_target_dbfs / 20.0)
        gain = target_peak / peak
        if np.isclose(gain, 1.0):
            return data, False

        return np.clip(data * gain, -1.0, 1.0), True
