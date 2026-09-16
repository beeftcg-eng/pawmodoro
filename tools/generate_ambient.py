#!/usr/bin/env python3
"""
generate_ambient.py - Synthesizes three more looping ambient tracks
(ocean waves, white noise, wind) to sit alongside rain.wav, all from noise
(no sampled/copyrighted audio), each a seamless equal-power-crossfaded
loop. Run once at build time; the resources/*.wav files are what ship.
Requires numpy + scipy (only needed here, not at app runtime).
"""
import os
import wave
import numpy as np
from scipy.signal import butter, filtfilt

SR = 44100
RESOURCES = os.path.join(os.path.dirname(__file__), "..", "resources")
os.makedirs(RESOURCES, exist_ok=True)


def bandpass(sig, low, high, order=4):
    nyq = SR / 2
    low = max(low, 1)
    high = min(high, nyq - 1)
    b, a = butter(order, [low / nyq, high / nyq], btype="band")
    return filtfilt(b, a, sig)


def lowpass(sig, cutoff, order=4):
    nyq = SR / 2
    b, a = butter(order, cutoff / nyq, btype="low")
    return filtfilt(b, a, sig)


def smoothed_random_walk(duration, n, low=0.7, high=1.0, smooth=6, seed=None):
    rng = np.random.default_rng(seed)
    coarse_n = int(duration) + 2
    coarse = rng.normal(0, 1, coarse_n)
    kernel = np.ones(smooth) / smooth
    coarse = np.convolve(coarse, kernel, mode="same")
    coarse = (coarse - coarse.min()) / (coarse.max() - coarse.min())
    coarse = low + (high - low) * coarse
    return np.interp(np.linspace(0, duration, n), np.linspace(0, duration, coarse_n), coarse)


def crossfade_loop(signal, sr, crossfade_seconds):
    fade_len = int(sr * crossfade_seconds)
    t_fade = np.linspace(0, np.pi / 2, fade_len)
    fade_out, fade_in = np.cos(t_fade), np.sin(t_fade)
    signal[:fade_len] = signal[:fade_len] * fade_in + signal[-fade_len:] * fade_out
    return signal[:-fade_len]


def save_wav(signal, path, peak=0.85):
    signal = signal / np.max(np.abs(signal)) * peak
    pcm = (signal * 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    print(f"Wrote {path} ({len(pcm) / SR:.1f}s, {os.path.getsize(path) / 1024 / 1024:.1f} MB)")


# ---------------- Ocean waves ----------------
def make_ocean(duration=110.0, crossfade=3.0):
    n = int(SR * duration)
    rng = np.random.default_rng(11)
    wash = bandpass(rng.normal(0, 1, n), 60, 900)
    wash /= np.max(np.abs(wash))
    foam = bandpass(rng.normal(0, 1, n), 900, 4000)
    foam /= np.max(np.abs(foam))

    # wave cycles: irregular period around 7s, fast-ish attack, slow decay
    t = np.linspace(0, duration, n)
    cycle_len = 7.0
    phase = (t % cycle_len) / cycle_len
    # asymmetric envelope: rises then falls, slightly randomized per-sample smoothness
    env = np.where(phase < 0.35, phase / 0.35, np.exp(-(phase - 0.35) * 3.2))
    env = env / env.max()
    # jitter the envelope timing subtly so it's not a metronome
    jitter = smoothed_random_walk(duration, n, low=0.85, high=1.15, smooth=8, seed=12)
    env = np.clip(env * jitter, 0, None)

    signal = wash * (0.55 + 0.45 * env) + foam * env * 0.35
    signal = crossfade_loop(signal, SR, crossfade)
    save_wav(signal, os.path.join(RESOURCES, "ocean.wav"))


# ---------------- White noise ----------------
def make_white_noise(duration=90.0, crossfade=2.5):
    n = int(SR * duration)
    rng = np.random.default_rng(21)
    signal = rng.normal(0, 1, n)
    # gently tame harsh top end so it's pleasant, not hissy static
    signal = lowpass(signal, 11000)
    signal = crossfade_loop(signal, SR, crossfade)
    save_wav(signal, os.path.join(RESOURCES, "white_noise.wav"), peak=0.6)


# ---------------- Wind ----------------
def make_wind(duration=110.0, crossfade=3.0):
    n = int(SR * duration)
    rng = np.random.default_rng(31)
    body = bandpass(rng.normal(0, 1, n), 150, 1800)
    body /= np.max(np.abs(body))
    whistle = bandpass(rng.normal(0, 1, n), 1800, 3500)
    whistle /= np.max(np.abs(whistle))

    # broad, slow gusts — much slower drift than rain's
    gust = smoothed_random_walk(duration, n, low=0.5, high=1.0, smooth=20, seed=32)

    signal = body * gust + whistle * (gust ** 2) * 0.25
    signal = crossfade_loop(signal, SR, crossfade)
    save_wav(signal, os.path.join(RESOURCES, "wind.wav"))


# ---------------- Café ambience ----------------
def make_cafe(duration=110.0, crossfade=3.0):
    n = int(SR * duration)
    rng = np.random.default_rng(41)
    murmur = bandpass(rng.normal(0, 1, n), 250, 2800)
    murmur /= np.max(np.abs(murmur))
    room = bandpass(rng.normal(0, 1, n), 60, 250)
    room /= np.max(np.abs(room))

    # slow swell in murmur intensity, like conversation ebbing and flowing
    swell = smoothed_random_walk(duration, n, low=0.6, high=1.0, smooth=25, seed=42)
    signal = murmur * swell * 0.6 + room * 0.25

    # occasional cup/spoon clinks — sparse, brighter tone than rain droplets
    clinks = np.zeros(n)
    rate_per_sec = 0.6
    t = 0.0
    positions = []
    while t < duration:
        t += rng.exponential(1.0 / rate_per_sec)
        if t < duration:
            positions.append(t)
    for t_pos in positions:
        pos = int(t_pos * SR)
        dur = int(rng.uniform(0.03, 0.09) * SR)
        decay_rate = rng.uniform(10, 18)
        env = np.exp(-np.linspace(0, 1, dur) * decay_rate)
        burst = bandpass(rng.normal(0, 1, dur + 200), 2500, 7000)[:dur]
        amp = rng.uniform(0.08, 0.22)
        click = burst * env * amp
        end = min(pos + dur, n)
        clinks[pos:end] += click[: end - pos]

    signal = signal + clinks
    signal = crossfade_loop(signal, SR, crossfade)
    save_wav(signal, os.path.join(RESOURCES, "cafe.wav"))


# ---------------- Fireplace ----------------
def make_fireplace(duration=110.0, crossfade=3.0):
    n = int(SR * duration)
    rng = np.random.default_rng(51)
    roar = bandpass(rng.normal(0, 1, n), 40, 400)
    roar /= np.max(np.abs(roar))
    flicker = smoothed_random_walk(duration, n, low=0.7, high=1.0, smooth=10, seed=52)
    bed = roar * flicker

    # crackle/pop transients — sharp, broadband, irregular timing
    crackle = np.zeros(n)
    rate_per_sec = 3.2
    t = 0.0
    positions = []
    while t < duration:
        t += rng.exponential(1.0 / rate_per_sec)
        if t < duration:
            positions.append(t)
    for t_pos in positions:
        pos = int(t_pos * SR)
        dur = int(rng.uniform(0.006, 0.02) * SR)
        decay_rate = rng.uniform(25, 45)
        env = np.exp(-np.linspace(0, 1, dur) * decay_rate)
        amp = rng.uniform(0.15, 0.45)
        pop = rng.normal(0, 1, dur) * env * amp
        end = min(pos + dur, n)
        crackle[pos:end] += pop[: end - pos]

    signal = bed * 0.55 + crackle
    signal = crossfade_loop(signal, SR, crossfade)
    save_wav(signal, os.path.join(RESOURCES, "fireplace.wav"))


if __name__ == "__main__":
    make_ocean()
    make_white_noise()
    make_wind()
    make_cafe()
    make_fireplace()
