# Original synthesized sound design for the Mayday Reel (no third-party audio).
import numpy as np
SR = 48000
rng = np.random.default_rng(3)

def t_(d): return np.arange(int(d * SR)) / SR
def env_ad(n, a, d):
    e = np.ones(n); na = max(1, int(a * SR)); nd = max(1, int(d * SR))
    e[:na] = np.linspace(0, 1, na)
    if nd < n: e[-nd:] *= np.linspace(1, 0, nd)
    return e
def lp(x, fc):  # one-pole lowpass
    a = np.exp(-2 * np.pi * fc / SR); y = np.empty_like(x); s = 0.0
    for i in range(len(x)): s = (1 - a) * x[i] + a * s; y[i] = s
    return y
def lp_fast(x, fc, passes=2):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(passes * 2, fc / (SR / 2), output='sos'), x)
def hp_fast(x, fc):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, fc / (SR / 2), 'high', output='sos'), x)
def bp_fast(x, lo, hi):
    from scipy.signal import butter, sosfilt
    return sosfilt(butter(4, [lo / (SR / 2), hi / (SR / 2)], 'band', output='sos'), x)

def warning_beeps(d=2.9, rate=4.0):
    """Two-tone cockpit-style warning: alternating 1000/1250 Hz square-ish beeps."""
    t = t_(d); out = np.zeros_like(t); per = 1 / rate
    for k in range(int(d * rate)):
        s = int(k * per * SR); n = int(per * 0.55 * SR); f = 1000 if k % 2 == 0 else 1250
        tt = np.arange(n) / SR
        w = np.tanh(3 * np.sin(2 * np.pi * f * tt)) * env_ad(n, 0.004, 0.02)
        out[s:s + n] += w[:max(0, min(n, len(out) - s))]
    return 0.22 * out

def radio_static(d, level=0.08):
    n = rng.standard_normal(int(d * SR))
    return level * bp_fast(n, 900, 5000) * (0.7 + 0.3 * np.sin(2 * np.pi * 7 * t_(d)))

def squelch(d=0.18):
    n = rng.standard_normal(int(d * SR)); return 0.35 * bp_fast(n, 1200, 6000) * env_ad(len(n), 0.002, 0.12)

def whoosh(d=0.55, up=True, level=0.5):
    n = rng.standard_normal(int(d * SR)); t = t_(d)
    from scipy.signal import butter, sosfilt
    out = np.zeros_like(n); seg = 512
    for i in range(0, len(n), seg):
        p = i / len(n); p = p if up else 1 - p
        fc = 300 + 5500 * p ** 2
        sos = butter(2, [max(80, fc * 0.5) / (SR / 2), min(SR / 2 - 100, fc * 1.6) / (SR / 2)], 'band', output='sos')
        out[i:i + seg] = sosfilt(sos, n[i:i + seg])
    e = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 1.5
    return level * out * e

def impact(d=1.4, level=0.9):
    t = t_(d); f = 55 * np.exp(-t * 2.2) + 32
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3.0)
    click = rng.standard_normal(len(t)) * np.exp(-t * 60)
    noise = lp_fast(rng.standard_normal(len(t)), 900) * np.exp(-t * 6)
    return level * (0.9 * body + 0.15 * click + 0.35 * noise)

def tick(d=0.05, f=2400, level=0.18):
    t = t_(d); return level * np.sin(2 * np.pi * f * t) * np.exp(-t * 90)

def ui_pop(level=0.22):
    t = t_(0.12); f = 600 + 1400 * (t / 0.12)
    return level * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 35)

def radar_ping(level=0.25):
    t = t_(1.2); return level * (np.sin(2 * np.pi * 1850 * t) + 0.3 * np.sin(2 * np.pi * 3700 * t)) * np.exp(-t * 4.5)

def riser(d=1.5, level=0.35):
    t = t_(d); n = rng.standard_normal(len(t))
    hp = hp_fast(n, 1500) * (t / d) ** 2
    tone = np.sin(2 * np.pi * np.cumsum(200 + 900 * (t / d) ** 2) / SR) * (t / d) ** 2 * 0.3
    return level * (hp * 0.6 + tone)

def drone(d, base=55.0, level=0.18):
    t = t_(d); x = np.zeros_like(t)
    for k, det in enumerate([1.0, 1.003, 0.997, 2.0, 1.5]):
        ph = 2 * np.pi * base * det * t
        x += (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)) / (1 + k * 0.6)
    x = lp_fast(x, 700)
    return level * x / np.max(np.abs(x))

def pulse_bed(d, bpm=100, level=0.5):
    out = np.zeros(int(d * SR)); beat = 60 / bpm
    k = impact(0.45, 1.0) * 0.6
    for i in range(int(d / beat) + 1):
        s = int(i * beat * SR); e = min(len(out), s + len(k))
        if s < len(out): out[s:e] += k[:e - s] * (1.0 if i % 2 == 0 else 0.55)
    return level * out

def pad(d, freqs=(220, 277.18, 329.63, 440), level=0.12):
    t = t_(d); x = sum(np.sin(2 * np.pi * f * t + np.sin(2 * np.pi * 0.2 * t)) for f in freqs)
    x = lp_fast(x, 1800); e = env_ad(len(t), min(1.0, d / 3), min(1.0, d / 3))
    return level * x / np.max(np.abs(x)) * e

def radio_voice(x, sr=SR):
    """Band-limit + mild saturation for the hook's radio transmission."""
    y = bp_fast(x, 320, 3300); y = np.tanh(2.2 * y) / np.tanh(2.2)
    return y
