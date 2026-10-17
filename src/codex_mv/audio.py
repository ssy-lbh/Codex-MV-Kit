"""Whole-stream PCM evidence and strict, declared-window audio comparisons.

FFmpeg performs mono decoding/resampling. PCM is spooled to temporary files,
not kept as an unbounded in-memory waveform. A one-hour default decoding cap
raises an error instead of silently analyzing a prefix; callers may explicitly
change or disable it. All reported durations/counts describe decoded PCM.

Tempo/phase estimates describe repeated onsets, not musical downbeats or words.
STFT bin spacing, window bandwidth, temporal resolution, and BPM scan spacing
are reported separately so a fine scan cannot masquerade as physical precision.
"""

from __future__ import annotations

from contextlib import contextmanager
from fractions import Fraction
import math
from pathlib import Path
import subprocess
import tempfile
from typing import BinaryIO, Iterator

import numpy as np

from .media import Toolchain, probe


class AudioError(RuntimeError):
    """Audio could not be decoded or analyzed within the requested limits."""


_CHUNK_SAMPLES = 65536
_DEFAULT_CAP_SECONDS = 3600.0


def _number(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive and ' if positive else ''}finite")
    return result


def _rate(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
        raise ValueError("sample_rate must be a positive integer")
    return int(value)


def _metadata(path: Path, toolchain: Toolchain, sample_rate: int) -> dict:
    """Container duration is evidence, not proof of codec padding."""
    info = probe(path, toolchain=toolchain)
    stream = next((s for s in info.get("streams", [])
                   if s.get("codec_type") == "audio"), None)
    if stream is None:
        raise AudioError(f"No audio stream: {path}")
    duration = None
    basis = None
    try:
        if stream.get("duration_ts") is not None and stream.get("time_base"):
            duration = float(int(stream["duration_ts"]) * Fraction(stream["time_base"]))
            basis = "audio stream duration_ts * time_base"
        elif stream.get("duration") is not None:
            duration = float(stream["duration"])
            basis = "audio stream duration"
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        duration = None
    if duration is not None and (not math.isfinite(duration) or duration < 0):
        duration = None
    return {
        "codec": stream.get("codec_name"),
        "metadata_duration_seconds": duration,
        "metadata_duration_basis": basis if duration is not None else None,
        "metadata_samples": round(duration * sample_rate) if duration is not None else None,
    }


@contextmanager
def _decoded_pcm(path: str | Path, sample_rate: int, toolchain: Toolchain,
                 max_duration_seconds: float | None) -> Iterator[tuple[BinaryIO, int, dict]]:
    source = Path(path)
    if not source.is_file():
        raise AudioError(f"Audio file does not exist: {source}")
    cap_samples = None
    if max_duration_seconds is not None:
        cap_samples = round(_number(max_duration_seconds, "max_duration_seconds", positive=True)
                            * sample_rate)
    metadata = _metadata(source, toolchain, sample_rate)
    command = [str(toolchain.ffmpeg), "-v", "error", "-nostdin", "-i", str(source),
               "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(sample_rate),
               "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"]
    with tempfile.TemporaryFile() as pcm, tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
        except OSError as exc:
            raise AudioError(f"Could not run FFmpeg: {exc}") from exc
        byte_count = 0
        try:
            assert process.stdout is not None
            while block := process.stdout.read(_CHUNK_SAMPLES * 4):
                byte_count += len(block)
                if cap_samples is not None and byte_count > cap_samples * 4:
                    raise AudioError(
                        f"Decoded audio exceeds max_duration_seconds={max_duration_seconds}; "
                        "no partial analysis was returned. Raise the cap explicitly if intended."
                    )
                pcm.write(block)
            code = process.wait()
            if code:
                errors.seek(0)
                message = errors.read(8192).decode("utf-8", "replace").strip()
                raise AudioError(f"FFmpeg audio decode failed ({code}): {message}")
            if byte_count % 4:
                raise AudioError("FFmpeg returned an incomplete float32 PCM sample")
            count = byte_count // 4
            if not count:
                raise AudioError("Audio stream contains no decoded samples")
            metadata["raw_sample_count"] = count
            declared = metadata["metadata_samples"]
            metadata["raw_samples_beyond_metadata_duration"] = (
                max(0, count - declared) if declared is not None else None)
            metadata["padding_note"] = (
                "Excess over stream metadata may be decoder/codec padding or duration metadata "
                "rounding; samples beyond a requested window may also be ordinary source content."
            )
            pcm.seek(0)
            yield pcm, count, metadata
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _chunks(pcm: BinaryIO, start: int, count: int) -> Iterator[np.ndarray]:
    pcm.seek(start * 4)
    remaining = count
    while remaining:
        data = pcm.read(min(remaining, _CHUNK_SAMPLES) * 4)
        if not data:
            raise AudioError("Temporary PCM ended before its measured sample count")
        block = np.frombuffer(data, dtype="<f4")
        if not np.isfinite(block).all():
            raise AudioError("Decoded PCM contains non-finite samples")
        remaining -= len(block)
        yield block


def _pcm_features(pcm: BinaryIO, count: int, sample_rate: int,
                  n_fft: int = 2048) -> dict:
    """Accumulate full-stream energy and STFT evidence with bounded FFT batches."""
    hop = max(1, round(sample_rate / 100))
    energy_parts: list[np.ndarray] = []
    energy_pending = np.empty(0, dtype=np.float32)
    fft_pending = np.empty(0, dtype=np.float32)
    window = np.hanning(n_fft)
    power_sum = np.zeros(n_fft // 2 + 1, dtype=np.float64)
    previous_magnitude = None
    flux_sum = 0.0
    frame_count = 0
    sum_squares = 0.0
    peak = 0.0
    clipped = 0
    for block in _chunks(pcm, 0, count):
        values = block.astype(np.float64)
        sum_squares += float(np.dot(values, values))
        peak = max(peak, float(np.max(np.abs(values))))
        clipped += int(np.count_nonzero(np.abs(values) >= 0.999))
        energy_buffer = np.concatenate((energy_pending, block))
        bins = len(energy_buffer) // hop
        if bins:
            grouped = energy_buffer[:bins * hop].reshape(bins, hop).astype(np.float64)
            energy_parts.append(np.sqrt(np.mean(grouped * grouped, axis=1)))
        energy_pending = energy_buffer[bins * hop:]
        fft_buffer = np.concatenate((fft_pending, block))
        if len(fft_buffer) >= n_fft:
            frames = np.lib.stride_tricks.sliding_window_view(fft_buffer, n_fft)[::hop]
            magnitude = np.abs(np.fft.rfft(frames * window, axis=1))
            power_sum += np.sum(magnitude * magnitude, axis=0)
            if previous_magnitude is None:
                differences = np.diff(magnitude, axis=0)
            else:
                differences = np.diff(np.vstack((previous_magnitude, magnitude)), axis=0)
            flux_sum += float(np.maximum(differences, 0).sum())
            previous_magnitude = magnitude[-1:]
            frame_count += len(frames)
            fft_pending = fft_buffer[len(frames) * hop:]
        else:
            fft_pending = fft_buffer
    if len(energy_pending):
        values = energy_pending.astype(np.float64)
        energy_parts.append(np.array([math.sqrt(float(np.mean(values * values)))]))
    energy = np.concatenate(energy_parts) if energy_parts else np.empty(0)
    frequencies = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    spectral_total = float(power_sum.sum())
    dominant = float(frequencies[int(np.argmax(power_sum))]) if spectral_total > 0 else None
    centroid = float(np.dot(frequencies, power_sum) / spectral_total) if spectral_total > 0 else None
    enbw = sample_rate * float(np.sum(window * window)) / float(np.sum(window) ** 2)
    return {
        "energy": energy,
        "hop": hop,
        "rms": math.sqrt(sum_squares / count),
        "peak": peak,
        "clipped_sample_fraction": clipped / count,
        "stft": {
            "window": "Hann (symmetric)",
            "n_fft": n_fft,
            "window_samples": n_fft,
            "window_duration_seconds": n_fft / sample_rate,
            "hop_samples": hop,
            "hop_duration_seconds": hop / sample_rate,
            "frequency_bin_spacing_hz": sample_rate / n_fft,
            "hann_equivalent_noise_bandwidth_hz": enbw,
            "frame_count": frame_count,
            "dominant_frequency_bin_hz": dominant,
            "power_weighted_centroid_hz": centroid,
            "mean_positive_spectral_flux": flux_sum / max(1, frame_count - 1),
            "resolution_note": (
                "Bin spacing is the sampled frequency grid, not a promise to resolve tones "
                "that close. Hann bandwidth and window duration bound interpretation; "
                "neither STFT nor BPM scan spacing establishes measurement precision."
            ),
        },
    }


def _onset_events(energy: np.ndarray, hop_seconds: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not len(energy) or float(energy.max()) <= 1e-9:
        return np.empty(0), np.empty(0), np.zeros_like(energy)
    # Ignore tiny RMS variation from carrier phase in a stationary tone.
    floor = 0.02 * max(float(np.percentile(energy, 90)), 0.1 * float(energy.max()))
    onset = np.maximum(np.diff(energy, prepend=0.0) - floor, 0.0)
    threshold = 0.025 * float(onset.max())
    before = np.r_[0.0, onset[:-1]]
    after = np.r_[onset[1:], 0.0]
    indices = np.flatnonzero((onset > before) & (onset >= after) & (onset > threshold))
    # One transient can occupy several bins; retain its strongest nearby peak.
    separation = max(1, round(0.08 / hop_seconds))
    kept: list[int] = []
    for index in indices:
        if kept and index - kept[-1] < separation:
            if onset[index] > onset[kept[-1]]:
                kept[-1] = int(index)
        else:
            kept.append(int(index))
    chosen = np.asarray(kept, dtype=np.int64)
    return ((chosen.astype(np.float64) + 0.5) * hop_seconds,
            onset[chosen], onset)


def _comb_scores(bpms: np.ndarray, autocorrelation: np.ndarray, hop_seconds: float) -> np.ndarray:
    result = np.zeros(len(bpms))
    weight_sum = np.zeros(len(bpms))
    positions = np.arange(len(autocorrelation))
    for multiple in range(1, 9):
        lags = multiple * 60.0 / (bpms * hop_seconds)
        valid = lags < len(autocorrelation) - 1
        weight = 1.0 / math.sqrt(multiple)
        result[valid] += weight * np.interp(lags[valid], positions, autocorrelation)
        weight_sum[valid] += weight
    return np.divide(result, weight_sum, out=np.zeros_like(result), where=weight_sum > 0)


def _coherences(bpms: np.ndarray, times: np.ndarray, weights: np.ndarray) -> np.ndarray:
    result = np.empty(len(bpms))
    normalizer = float(weights.sum())
    for begin in range(0, len(bpms), 16):
        trial = bpms[begin:begin + 16]
        vectors = np.exp((2j * math.pi / 60) * trial[:, None] * times[None, :])
        result[begin:begin + len(trial)] = np.abs(vectors @ weights) / normalizer
    return result


def _tempo(energy: np.ndarray, hop_seconds: float, duration: float,
           bpm_min: float, bpm_max: float) -> dict:
    times, weights, onset = _onset_events(energy, hop_seconds)
    result = {
        "estimated_bpm": None,
        "phase_seconds": None,
        "beat_period_seconds": None,
        "onset_count": len(times),
        "tempo_confidence": 0.0,
        "phase_candidates": [],
        "tempo_alternatives": [],
        "uncertainty": {
            "kind": "heuristic uncertainty; not a statistical confidence interval",
            "bpm_half_width": None,
            "phase_seconds": None,
            "finite_window_fourier_spacing_bpm": 60.0 / duration,
            "scan_step_bpm": None,
        },
        "method": (
            "Whole-stream 10 ms RMS attacks, FFT autocorrelation with an eight-lag comb, "
            "then weighted onset phase-coherence refinement inside the requested BPM range."
        ),
        "interpretation": (
            "Onset-aligned pulse estimate only. Half/double tempo and half-beat phase "
            "branches remain possible; a pulse grid does not identify a musical downbeat "
            "or the timing/content of sung lyrics."
        ),
    }
    if len(times) < 4 or times[-1] - times[0] < 0.75:
        result["reason"] = "Too few separated onsets for a tempo estimate"
        return result
    centered = onset - float(onset.mean())
    fft_size = 1 << (2 * len(centered) - 1).bit_length()
    spectrum = np.fft.rfft(centered, n=fft_size)
    acf = np.fft.irfft(spectrum * spectrum.conj(), n=fft_size)[:len(centered)]
    acf /= np.arange(len(centered), 0, -1)
    if acf[0] <= 0:
        result["reason"] = "Onset envelope has no usable variation"
        return result
    acf /= acf[0]
    coarse = np.arange(bpm_min, bpm_max, 0.05)
    coarse = np.unique(np.r_[coarse, bpm_max])
    scores = _comb_scores(coarse, acf, hop_seconds)
    peaks = np.flatnonzero((scores >= np.r_[-np.inf, scores[:-1]])
                           & (scores >= np.r_[scores[1:], -np.inf]))
    top = peaks[np.argsort(scores[peaks])[-12:]]
    span = float(times[-1] - times[0])
    step = min(0.05, 15.0 / span)
    trials = []
    for index in top:
        center = coarse[index]
        halfwidth = max(1.5, center * 0.015)
        trials.append(np.arange(max(bpm_min, center - halfwidth),
                                min(bpm_max, center + halfwidth), step))
    fine = np.unique(np.concatenate((*trials, np.array([bpm_min, bpm_max]))))
    coherence = _coherences(fine, times, weights)
    comb = np.clip(_comb_scores(fine, acf, hop_seconds), 0.0, 1.0)
    combined = 0.75 * coherence + 0.25 * comb
    best = int(np.argmax(combined))
    bpm = float(fine[best])
    confidence = float(np.clip(combined[best], 0.0, 1.0))
    if confidence < 0.25:
        result["reason"] = "Weak periodic evidence; no reliable pulse estimate"
        return result
    period = 60.0 / bpm
    vector = np.dot(np.exp(2j * math.pi * times / period), weights)
    phase = float((np.angle(vector) * period / (2 * math.pi)) % period)
    residual = (times - phase + period / 2) % period - period / 2
    good = np.abs(residual) <= 0.15 * period
    jitter = float(np.median(np.abs(residual[good]))) if np.any(good) else period / 2
    phase_uncertainty = max(hop_seconds / 2, 1.4826 * jitter)
    bpm_uncertainty = max(step, 60.0 / (8 * span), 2 * bpm * phase_uncertainty / span)
    tolerance = max(2 * hop_seconds, 0.08 * period)
    phase_candidates = []
    for branch, candidate in (("onset_aligned", phase), ("half_beat_shift", (phase + period / 2) % period)):
        errors = (times - candidate + period / 2) % period - period / 2
        support = float(weights[np.abs(errors) <= tolerance].sum() / weights.sum())
        phase_candidates.append({"branch": branch, "phase_seconds": float(candidate),
                                 "weighted_onset_support": support})
    alternatives = []
    for factor in (0.5, 2.0):
        candidate = bpm * factor
        # arange/refinement roundoff must not drop an exact range-edge branch.
        if math.isclose(candidate, bpm_min, rel_tol=1e-10, abs_tol=1e-9):
            candidate = bpm_min
        elif math.isclose(candidate, bpm_max, rel_tol=1e-10, abs_tol=1e-9):
            candidate = bpm_max
        if bpm_min <= candidate <= bpm_max:
            alternatives.append({"bpm": candidate, "relation": "half_tempo" if factor < 1 else "double_tempo",
                                 "onset_coherence": float(_coherences(np.array([candidate]), times, weights)[0])})
    result.update({
        "estimated_bpm": bpm,
        "phase_seconds": phase,
        "beat_period_seconds": period,
        "tempo_confidence": confidence,
        "phase_candidates": phase_candidates,
        "tempo_alternatives": alternatives,
    })
    result["uncertainty"].update({"bpm_half_width": bpm_uncertainty,
                                   "phase_seconds": phase_uncertainty,
                                   "scan_step_bpm": step})
    return result


def analyze_audio(path: str | Path, *, toolchain: Toolchain | None = None,
                  sample_rate: int = 22050, bpm_min: float = 60,
                  bpm_max: float = 200,
                  max_duration_seconds: float | None = _DEFAULT_CAP_SECONDS) -> dict:
    """Analyze the entire first audio stream; never silently truncate at the cap."""
    rate = _rate(sample_rate)
    low = _number(bpm_min, "bpm_min", positive=True)
    high = _number(bpm_max, "bpm_max", positive=True)
    if low >= high or high > 1000:
        raise ValueError("Require 0 < bpm_min < bpm_max <= 1000")
    if low < 1:
        raise ValueError("bpm_min must be at least 1")
    tools = toolchain if toolchain is not None else Toolchain()
    with _decoded_pcm(path, rate, tools, max_duration_seconds) as (pcm, count, metadata):
        features = _pcm_features(pcm, count, rate)
    duration = count / rate
    tempo = _tempo(features.pop("energy"), features.pop("hop") / rate, duration, low, high)
    return {
        "sample_rate": rate,
        "sample_count": count,
        "duration_seconds": duration,
        "channels_analyzed": 1,
        "coverage": "entire decoded first audio stream",
        "bpm_search_range": [low, high],
        "downbeat_inferred": False,
        "lyrics_inferred": False,
        "source": metadata,
        **features,
        **tempo,
    }


def _window_stats(pcm: BinaryIO, start: int, count: int) -> dict:
    peak = total = squares = 0.0
    for block in _chunks(pcm, start, count):
        values = block.astype(np.float64)
        total += float(values.sum())
        squares += float(np.dot(values, values))
        peak = max(peak, float(np.max(np.abs(values))))
    return {"peak": peak, "rms": math.sqrt(squares / count) if count else 0.0,
            "sum": total, "sum_squares": squares}


def _lag_from_reduced(reference: np.ndarray, candidate: np.ndarray,
                      block_size: int, sample_rate: int, max_lag_seconds: float) -> dict:
    length = len(reference)
    if length < 2:
        return {"lag_samples": None, "lag_seconds": None}
    reference = reference - float(reference.mean())
    candidate = candidate - float(candidate.mean())
    if not np.any(reference) or not np.any(candidate):
        return {"lag_samples": None, "lag_seconds": None}
    fft_size = 1 << (2 * length - 1).bit_length()
    cross = np.fft.irfft(np.fft.rfft(candidate, n=fft_size)
                         * np.fft.rfft(reference, n=fft_size).conj(), n=fft_size)
    radius = min(length - 1, int(max_lag_seconds * sample_rate / block_size))
    if radius:
        scores = np.r_[cross[-radius:], cross[:radius + 1]]
        lag = (int(np.argmax(scores)) - radius) * block_size
    else:
        lag = 0
    return {"lag_samples": lag, "lag_seconds": lag / sample_rate}


def _full_window_pair(reference: BinaryIO, candidate: BinaryIO,
                      reference_start: int, candidate_start: int,
                      count: int, rate: int, max_lag_seconds: float) -> dict:
    """Pearson correlation uses every sample; only the optional lag is reduced."""
    cross_sum = 0.0
    block_size = max(1, math.ceil(count / 262144))
    reduced_reference: list[np.ndarray] = []
    reduced_candidate: list[np.ndarray] = []
    pending_r = np.empty(0, dtype=np.float64)
    pending_c = np.empty(0, dtype=np.float64)
    for raw_r, raw_c in zip(_chunks(reference, reference_start, count),
                            _chunks(candidate, candidate_start, count), strict=True):
        values_r = raw_r.astype(np.float64)
        values_c = raw_c.astype(np.float64)
        cross_sum += float(np.dot(values_r, values_c))
        buffer_r = np.r_[pending_r, values_r]
        buffer_c = np.r_[pending_c, values_c]
        groups = len(buffer_r) // block_size
        if groups:
            reduced_reference.append(buffer_r[:groups * block_size].reshape(groups, block_size).mean(axis=1))
            reduced_candidate.append(buffer_c[:groups * block_size].reshape(groups, block_size).mean(axis=1))
        pending_r = buffer_r[groups * block_size:]
        pending_c = buffer_c[groups * block_size:]
    if len(pending_r):
        reduced_reference.append(np.array([pending_r.mean()]))
        reduced_candidate.append(np.array([pending_c.mean()]))
    lag = _lag_from_reduced(np.concatenate(reduced_reference), np.concatenate(reduced_candidate),
                            block_size, rate, max_lag_seconds)
    return {"cross_sum": cross_sum, **lag,
            "lag_resolution_samples": block_size,
            "lag_resolution_seconds": block_size / rate,
            "lag_method": "full-window waveform cross-correlation after bounded block averaging",
            "lag_sign": "positive means candidate is delayed relative to reference"}


def compare_audio(reference: str | Path, candidate: str | Path, *, start: float = 0,
                  reference_start: float | None = None, candidate_start: float | None = None,
                  duration: float | None = None, sample_rate: int = 22050,
                  toolchain: Toolchain | None = None, silence_threshold: float = 1e-7,
                  max_lag_seconds: float = 0.25,
                  max_duration_seconds: float | None = _DEFAULT_CAP_SECONDS) -> dict:
    """Compare complete independently positioned windows without min-length trims.

    ``start`` is a compatibility default for BOTH inputs. An explicit
    ``reference_start`` or ``candidate_start`` overrides it for that input.
    For preview QA use source offset + preview start for the reference and 0
    for the encoded candidate. ``round(duration * sample_rate)`` samples are
    required from each input. Without duration, the entire reference remainder
    declares the window and the candidate's entire remainder must match it.

    Raw decoder excess is reported separately. With an explicit duration,
    excess after a complete window does not change its correlation or count;
    a short input fails. Correlation is Pearson at zero lag, computed using all
    samples. Silence is based on peak amplitude at the stated threshold.
    """
    rate = _rate(sample_rate)
    common_start = _number(start, "start")
    left_start = common_start if reference_start is None else _number(reference_start, "reference_start")
    right_start = common_start if candidate_start is None else _number(candidate_start, "candidate_start")
    if common_start < 0 or left_start < 0 or right_start < 0:
        raise ValueError("Audio window starts must be non-negative")
    threshold = _number(silence_threshold, "silence_threshold")
    lag_limit = _number(max_lag_seconds, "max_lag_seconds")
    if threshold < 0 or lag_limit < 0:
        raise ValueError("silence_threshold and max_lag_seconds must be non-negative")
    requested_duration = None if duration is None else _number(duration, "duration", positive=True)
    tools = toolchain if toolchain is not None else Toolchain()
    with _decoded_pcm(reference, rate, tools, max_duration_seconds) as (left, left_total, left_meta):
        with _decoded_pcm(candidate, rate, tools, max_duration_seconds) as (right, right_total, right_meta):
            left_index, right_index = round(left_start * rate), round(right_start * rate)
            left_available = max(0, left_total - left_index)
            right_available = max(0, right_total - right_index)
            expected = round(requested_duration * rate) if requested_duration is not None else left_available
            left_count = min(left_available, expected)
            right_count = min(right_available, expected) if requested_duration is not None else right_available
            count_match = left_count == right_count == expected and expected > 0
            left_stats = _window_stats(left, left_index, left_count)
            right_stats = _window_stats(right, right_index, right_count)
            left_silent = left_stats["peak"] <= threshold
            right_silent = right_stats["peak"] <= threshold
            result = {
                "sample_rate": rate,
                "channels_compared": 1,
                "duration_seconds": expected / rate,
                "requested_duration_seconds": requested_duration,
                "expected_samples": expected,
                "reference_samples": left_count,
                "candidate_samples": right_count,
                "sample_count_match": count_match,
                "reference_peak": left_stats["peak"],
                "candidate_peak": right_stats["peak"],
                "reference_rms": left_stats["rms"],
                "candidate_rms": right_stats["rms"],
                "reference_is_silent": left_silent,
                "candidate_is_silent": right_silent,
                "silence_threshold": threshold,
                "correlation": None,
                "lag_samples": None,
                "lag_seconds": None,
                "status": "count_mismatch",
            }
            for name, meta, index, available, window_count, stats in (
                    ("reference", left_meta, left_index, left_available, left_count, left_stats),
                    ("candidate", right_meta, right_index, right_available, right_count, right_stats)):
                result[name] = {**meta, "start_sample": index, "available_samples": available,
                                "window_sample_count": window_count, "expected_sample_count": expected,
                                "raw_samples_after_window": max(0, available - window_count),
                                "peak": stats["peak"], "rms": stats["rms"]}
            if not count_match:
                return result
            if left_silent or right_silent:
                result["status"] = "silent_match" if left_silent and right_silent else "silent_mismatch"
                return result
            left_variance = max(0.0, left_stats["sum_squares"] - left_stats["sum"] ** 2 / expected)
            right_variance = max(0.0, right_stats["sum_squares"] - right_stats["sum"] ** 2 / expected)
            if left_variance <= 1e-24 or right_variance <= 1e-24:
                result["status"] = "undefined_correlation"
                return result
            paired = _full_window_pair(left, right, left_index, right_index, expected, rate, lag_limit)
            covariance = paired.pop("cross_sum") - left_stats["sum"] * right_stats["sum"] / expected
            result["correlation"] = float(np.clip(covariance / math.sqrt(left_variance * right_variance), -1.0, 1.0))
            result["status"] = "ok"
            result.update(paired)
            return result
