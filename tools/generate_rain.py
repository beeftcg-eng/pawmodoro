#!/usr/bin/env python3
"""
generate_rain.py - Synthesizes a longer, more naturally-textured looping
rain ambience entirely from noise (no sampled/copyrighted audio). Layers
a low rumble, mid hiss, and high shimmer band (each proper band-passed
noise, not just differencing), organic Poisson-distributed droplet
transients with varied decay, a smoothed random-walk "gust" envelope
instead of a mechanical sine, and a long equal-power crossfade so the
3-minute loop point is inaudible and restarts are rare.
Run once at build time; resources/rain.wav is what actually ships.
Requires numpy + scipy (only needed here, not at app runtime).
"""
import os
import wave
import numpy as np
from scipy.signal import butter, filtfilt

SR = 44100
DURATION = 180.0   # 3 minutes — long enough that loop restarts are rare
CROSSFADE = 4.0     # seconds, equal-power crossfade at the loop point
OUT = os.path.join(os.path.dirname(__file__), "..", "resources", "rain.wav")

rng = np.random.default_rng(7)
n = int(SR * DURATION)


def bandpass(sig, low, high, order=4):
    nyq = SR / 2
    b, a = butter(order, [low / nyq, high / nyq], btype="band")
    return filtfilt(b, a, sig)


# --- Three decorrelated noise bands, layered like real rain has body,
# texture, and shimmer at different frequencies ---
low_band = bandpass(rng.normal(0, 1, n), 80, 500)
low_band /= np.max(np.abs(low_band))

mid_band = bandpass(rng.normal(0, 1, n), 500, 3000)
mid_band /= np.max(np.abs(mid_band))

high_band = bandpass(rng.normal(0, 1, n), 3000, 9000)
high_band /= np.max(np.abs(high_band))

bed = 0.50 * low_band + 0.35 * mid_band + 0.15 * high_band
bed /= np.max(np.abs(bed))

# --- Organic "gust" envelope: a smoothed random walk, not a mechanical
# sine wave, so intensity drifts naturally rather than pulsing on a beat ---
coarse_n = int(DURATION) + 2
coarse = rng.normal(0, 1, coarse_n)
kernel = np.ones(6) / 6.0
coarse = np.convolve(coarse, kernel, mode="same")
coarse = (coarse - coarse.min()) / (coarse.max() - coarse.min())
coarse = 0.72 + 0.28 * coarse  # keep it within a believable dynamic range
gust = np.interp(np.linspace(0, DURATION, n), np.linspace(0, DURATION, coarse_n), coarse)
bed *= gust

# --- Droplet transients: Poisson-distributed arrival times (not evenly
# spaced, like real rain), each with randomized duration/decay/amplitude ---
droplets = np.zeros(n)
rate_per_sec = 42
t = 0.0
positions = []
while t < DURATION:
    t += rng.exponential(1.0 / rate_per_sec)
    if t < DURATION:
        positions.append(t)

for t_pos in positions:
    pos = int(t_pos * SR)
    dur = int(rng.uniform(0.015, 0.05) * SR)
    decay_rate = rng.uniform(14, 28)
    env = np.exp(-np.linspace(0, 1, dur) * decay_rate)
    amp = rng.uniform(0.04, 0.20)
    click = rng.normal(0, 1, dur) * env * amp
    end = min(pos + dur, n)
    droplets[pos:end] += click[: end - pos]

signal = bed * 0.55 + droplets

# --- Seamless loop: equal-power crossfade (sin/cos) sounds smoother to the
# ear than a linear fade for continuous noise textures like this ---
fade_len = int(SR * CROSSFADE)
t_fade = np.linspace(0, np.pi / 2, fade_len)
fade_out, fade_in = np.cos(t_fade), np.sin(t_fade)
signal[:fade_len] = signal[:fade_len] * fade_in + signal[-fade_len:] * fade_out
signal = signal[:-fade_len]

signal = signal / np.max(np.abs(signal)) * 0.85
pcm = (signal * 32767).astype(np.int16)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with wave.open(OUT, "w") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(SR)
    wf.writeframes(pcm.tobytes())

print(f"Wrote {OUT} ({len(pcm) / SR:.1f}s, {os.path.getsize(OUT) / 1024 / 1024:.1f} MB, {len(positions)} droplets)")
