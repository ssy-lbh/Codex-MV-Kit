"""One global beat clock, shared by full renders and cropped previews."""

from __future__ import annotations

from dataclasses import dataclass
import math


def _finite(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


@dataclass(frozen=True)
class BeatClock:
    """Beat zero occurs at ``phase`` global seconds; negative beats are valid.

    ``phase`` is a time offset, not a fraction of a beat. A crop beginning at
    global time 12 must still call ``time_to_beat(12 + local_time)`` on this
    same clock. Neither beat numbering nor phase is reset by a crop.
    """

    bpm: float
    phase: float = 0.0

    def __post_init__(self) -> None:
        bpm = _finite(self.bpm, "bpm")
        if bpm <= 0:
            raise ValueError("bpm must be positive")
        object.__setattr__(self, "bpm", bpm)
        object.__setattr__(self, "phase", _finite(self.phase, "phase"))

    @property
    def period(self) -> float:
        """Seconds per beat."""
        return 60.0 / self.bpm

    def beat_to_time(self, beat: float) -> float:
        return self.phase + _finite(beat, "beat") * self.period

    def time_to_beat(self, time: float) -> float:
        return (_finite(time, "time") - self.phase) / self.period

    def residual(self, time: float) -> float:
        """Signed seconds from the nearest beat, in [-period/2, period/2).

        Exact half-beat ties choose the later beat. This definition works for
        negative global times as well as positive times.
        """
        beats = self.time_to_beat(time)
        return (beats - math.floor(beats + 0.5)) * self.period

    def loop_phase(self, global_time: float, *, beats: float = 1.0,
                   shift: float = 0.0) -> float:
        """Seconds into a beat-length loop, preserving the global epoch.

        ``shift`` is in seconds, following (time - epoch + shift) % period.
        """
        length = _finite(beats, "beats")
        if length <= 0:
            raise ValueError("beats must be positive")
        return ((_finite(global_time, "global_time") - self.phase
                 + _finite(shift, "shift")) % (length * self.period))
