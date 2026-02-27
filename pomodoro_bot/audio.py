"""
Audio generator for Pomodoro bot.
Produces WAV bytes for sand-flowing, bell-chime, and tick sounds
using only numpy + the standard-library wave module.
"""

import io
import math
import struct
import wave

import numpy as np

SAMPLE_RATE = 44100


def _to_wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Convert float32 array [-1, 1] to 16-bit mono WAV bytes."""
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _fade_envelope(n: int, fade_in: int, fade_out: int) -> np.ndarray:
    env = np.ones(n)
    if fade_in > 0:
        env[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
    if fade_out > 0:
        env[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
    return env


# ──────────────────────────────────────────────────────────────────────────────
# 1. Sand-flowing sound  (session start)
# ──────────────────────────────────────────────────────────────────────────────

def create_sand_sound(duration: float = 4.0) -> bytes:
    """
    Simulate the soft hiss of sand grains falling through an hourglass neck.

    Technique:
    - White noise shaped with a moving-average lowpass (mimics granular diffusion)
    - High-frequency shimmer layer (grain-friction sparkle)
    - Slow amplitude swell so it feels like the stream settles
    - Gentle fade-in / fade-out
    """
    sr = SAMPLE_RATE
    n = int(duration * sr)
    rng = np.random.default_rng(0)

    # Base white noise
    noise = rng.standard_normal(n).astype(np.float32)

    # Lowpass via cumulative-sum trick (first-order IIR, α ≈ 0.05)
    alpha = 0.05
    lp = np.zeros(n, dtype=np.float32)
    lp[0] = noise[0]
    for i in range(1, n):
        lp[i] = alpha * noise[i] + (1.0 - alpha) * lp[i - 1]

    # High shimmer (less aggressive lowpass)
    alpha2 = 0.3
    hp = np.zeros(n, dtype=np.float32)
    hp[0] = noise[0]
    for i in range(1, n):
        hp[i] = alpha2 * noise[i] + (1.0 - alpha2) * hp[i - 1]

    # Slow amplitude modulation — grain stream dynamics
    t = np.linspace(0, duration, n)
    swell = 0.6 + 0.4 * np.sin(2 * math.pi * 0.25 * t + 0.5)

    # Mix layers
    sand = (lp * 0.65 + hp * 0.35) * swell

    # Normalise & envelope
    peak = np.max(np.abs(sand)) + 1e-9
    sand /= peak
    sand *= _fade_envelope(n, int(0.25 * sr), int(0.4 * sr))
    sand *= 0.75

    return _to_wav_bytes(sand)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Bell chime  (session / break end)
# ──────────────────────────────────────────────────────────────────────────────

def _bell_partial(n: int, freq: float, amp: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    env = np.exp(-decay * t)
    return amp * env * np.sin(2 * math.pi * freq * t)


def create_bell_sound(duration: float = 3.0) -> bytes:
    """
    Warm bell chime: fundamental + inharmonic overtones with exponential decay.
    Two strikes offset in time give a 'ding-dong' feel.
    """
    sr = SAMPLE_RATE
    n = int(duration * sr)
    out = np.zeros(n, dtype=np.float32)

    def add_strike(offset_sec: float, fund: float, amp_scale: float):
        offset = int(offset_sec * sr)
        length = n - offset
        if length <= 0:
            return
        # Bell partials (frequency ratios from physical bell acoustics)
        partials = [
            (fund * 1.000, 1.00, 4.0),
            (fund * 2.756, 0.60, 6.0),
            (fund * 5.404, 0.25, 9.0),
            (fund * 8.933, 0.12, 14.0),
            (fund * 13.34, 0.06, 20.0),
        ]
        wave_sum = np.zeros(length, dtype=np.float32)
        for freq, amp, decay in partials:
            wave_sum += _bell_partial(length, freq, amp * amp_scale, decay)
        out[offset: offset + length] += wave_sum

    add_strike(0.00, 523.25, 0.45)   # C5 first strike
    add_strike(0.55, 659.25, 0.35)   # E5 second strike (harmony)

    peak = np.max(np.abs(out)) + 1e-9
    out /= peak
    out *= _fade_envelope(n, int(0.01 * sr), int(0.4 * sr))
    out *= 0.80

    return _to_wav_bytes(out)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Soft tick  (minute-update or warning)
# ──────────────────────────────────────────────────────────────────────────────

def create_tick_sound(duration: float = 0.25) -> bytes:
    """
    A soft, muted mechanical click — like a sand grain hitting glass.
    """
    sr = SAMPLE_RATE
    n = int(duration * sr)
    t = np.arange(n) / sr

    # Short impulse: decaying sine burst
    freq = 1800.0
    decay = 40.0
    click = np.sin(2 * math.pi * freq * t) * np.exp(-decay * t)

    # Add a tiny noise transient at the very start
    rng = np.random.default_rng(1)
    transient_len = int(0.003 * sr)
    noise_burst = rng.standard_normal(transient_len) * np.linspace(1, 0, transient_len) * 0.4
    click[:transient_len] += noise_burst.astype(np.float32)

    peak = np.max(np.abs(click)) + 1e-9
    click /= peak
    click *= 0.45

    return _to_wav_bytes(click.astype(np.float32))


# ──────────────────────────────────────────────────────────────────────────────
# 4. Ascending chime  (break-end / back-to-focus reminder)
# ──────────────────────────────────────────────────────────────────────────────

def create_ascending_chime(duration: float = 2.5) -> bytes:
    """
    Three rising tones (C-E-G) — gentle 'time to focus again' signal.
    """
    sr = SAMPLE_RATE
    n = int(duration * sr)
    out = np.zeros(n, dtype=np.float32)

    notes = [261.63, 329.63, 392.00]  # C4, E4, G4
    note_dur = duration / len(notes)
    note_n = int(note_dur * sr)

    for idx, freq in enumerate(notes):
        offset = idx * note_n
        t = np.arange(note_n) / sr
        env = np.exp(-5.0 * t)
        tone = 0.5 * env * np.sin(2 * math.pi * freq * t)
        # slight harmonic for richness
        tone += 0.15 * env * np.sin(2 * math.pi * freq * 2 * t) * np.exp(-3 * t)
        out[offset: offset + note_n] += tone.astype(np.float32)

    peak = np.max(np.abs(out)) + 1e-9
    out /= peak
    out *= _fade_envelope(n, int(0.01 * sr), int(0.3 * sr))
    out *= 0.70

    return _to_wav_bytes(out)


# ──────────────────────────────────────────────────────────────────────────────
# Cache helpers  (avoid re-generating on every send)
# ──────────────────────────────────────────────────────────────────────────────

_cache: dict[str, bytes] = {}


def get_sound(name: str) -> bytes:
    """
    Return cached sound bytes.
    name: "sand" | "bell" | "tick" | "ascending"
    """
    if name not in _cache:
        if name == "sand":
            _cache[name] = create_sand_sound()
        elif name == "bell":
            _cache[name] = create_bell_sound()
        elif name == "tick":
            _cache[name] = create_tick_sound()
        elif name == "ascending":
            _cache[name] = create_ascending_chime()
        else:
            raise ValueError(f"Unknown sound: {name}")
    return _cache[name]
