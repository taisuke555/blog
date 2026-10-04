# -*- coding: utf-8 -*-
"""台本 → TTS → 実測タイムライン → BGM(ダッキング) + 効果音 → ミックス。

usage: GEO_WORK=./work python make_audio.py scripts/suez.py
出力:  $GEO_WORK/audio.wav  timeline.json  subs.srt
（video-engine スキルの lecture テンプレートの音声パイプラインを地理解説向けに調整したもの）
"""
import os, sys, json, wave, importlib.util
import numpy as np
from scipy.signal import butter, lfilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tts_adapters import get_tts

WORK = os.environ.get("GEO_WORK", os.path.join(os.getcwd(), "work"))
os.makedirs(WORK, exist_ok=True)
spec = importlib.util.spec_from_file_location("script", sys.argv[1])
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

SR = 48000
SPEED, HALFTONE = 1.08, 0.0
GAP_S, GAP_CH, LEAD, TAIL = 0.30, 0.85, 0.9, 2.6
TTS = get_tts(sr=SR, speed=SPEED, half_tone=HALFTONE)
PAUSE = getattr(S, "PAUSE_AFTER", {})
CACHE = os.path.join(WORK, "tts_cache")
os.makedirs(CACHE, exist_ok=True)


def trim(w, th=0.011, pad=int(0.06 * SR)):
    idx = np.where(np.abs(w) > th)[0]
    if len(idx) == 0:
        return w
    return w[max(0, idx[0] - pad):min(len(w), idx[-1] + pad)]


def synth(key, text):
    """同じ文の再合成はキャッシュから（ElevenLabs 等の課金対策）"""
    p = os.path.join(CACHE, f"{os.environ.get('VE_TTS', 'openjtalk')}_{key}.npy")
    meta = p + ".txt"
    if os.path.exists(p) and open(meta).read() == text:
        return np.load(p)
    w = trim(TTS.synth(text, key=key))
    np.save(p, w)
    open(meta, "w").write(text)
    return w


print("synthesizing...")
voice = np.zeros(0)
t = LEAD
chapters, sents = [], []
for ci, (chip, label, lines) in enumerate(S.CH):
    ch_t0 = t
    for key, tts_text, disp in lines:
        w = synth(key, tts_text)
        pad_n = int(t * SR) - len(voice)
        if pad_n > 0:
            voice = np.concatenate([voice, np.zeros(pad_n)])
        sents.append({"key": key, "ch": ci, "text": disp or tts_text, "t0": round(t, 3),
                      "t1": round(t + len(w) / SR, 3)})
        voice = np.concatenate([voice, w])
        t += len(w) / SR + GAP_S + PAUSE.get(key, 0.0)
    t += GAP_CH - GAP_S
    chapters.append({"i": ci, "chip": chip, "label": label, "t0": round(ch_t0, 3), "t1": 0})
TOTAL = t - GAP_CH + TAIL
for i, c in enumerate(chapters):
    c["t1"] = chapters[i + 1]["t0"] if i + 1 < len(chapters) else round(TOTAL, 3)
N = int(TOTAL * SR)
voice = np.concatenate([voice, np.zeros(max(0, N - len(voice)))])[:N]
voice = voice / np.abs(voice).max() * 0.92
print(f"narration: {TOTAL:.1f}s, {len(sents)} sentences")
SBY = {s["key"]: s for s in sents}

# ---------------------------------------------------------------- BGM（ドキュメンタリー調 84BPM）
BPM = 84.0
BEAT = 60 / BPM
BAR = BEAT * 4
rng = np.random.default_rng(11)


def midi(n):
    return 440 * 2 ** ((n - 69) / 12)


def lp(s, fc, o=2):
    b, a = butter(o, fc / (SR / 2), btype="low")
    return lfilter(b, a, s)


def hp(s, fc, o=2):
    b, a = butter(o, fc / (SR / 2), btype="high")
    return lfilter(b, a, s)


def place(buf, sig, t0, g=1.0):
    s = int(t0 * SR)
    e = min(s + len(sig), len(buf))
    if 0 <= s < len(buf) and e > s:
        buf[s:e] += sig[:e - s] * g


def pad(notes, dur):
    tt = np.arange(int(SR * dur)) / SR
    s = np.zeros_like(tt)
    for n in notes:
        f = midi(n)
        s += np.sin(2 * np.pi * f * tt) + 0.45 * np.sin(2 * np.pi * f * 1.004 * tt) + 0.2 * np.sin(2 * np.pi * f * 2 * tt)
    env = np.minimum(tt / 0.9, 1) * np.clip((dur - tt) / 0.7, 0, 1)
    return lp(s * env / len(notes), 1100)


def pulse(note, dur):
    tt = np.arange(int(SR * dur)) / SR
    f = midi(note)
    s = np.sign(np.sin(2 * np.pi * f * tt)) * 0.5 + np.sin(2 * np.pi * f * tt)
    return lp(s * np.exp(-tt * 7), 1400)


def kick():
    tt = np.arange(int(SR * 0.45)) / SR
    f = 38 + 60 * np.exp(-tt * 24)
    return np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 7) * 1.5) * 0.8


def shaker():
    tt = np.arange(int(SR * 0.07)) / SR
    return hp(rng.standard_normal(len(tt)), 7000, 3) * np.exp(-tt * 70) * 0.4


PROG = [[50, 57, 62, 65], [46, 53, 58, 62], [43, 50, 55, 58], [45, 52, 57, 61]]   # Dm Bb Gm A
ROOT = [38, 34, 31, 33]
bgm = np.zeros(N)
K, SH = kick(), shaker()
for bar in range(int(TOTAL / BAR) + 1):
    t0 = bar * BAR
    ch = PROG[bar % 4]
    place(bgm, pad(ch, BAR * 1.08), t0, 0.6)
    tt = np.arange(int(SR * BAR)) / SR
    place(bgm, lp(np.sin(2 * np.pi * midi(ROOT[bar % 4]) * tt) * (0.6 + 0.4 * np.exp(-tt * 1.5)), 260), t0, 0.55)
    if bar >= 2:
        for b in range(4):
            place(bgm, K, t0 + b * BEAT, 0.45 if b in (0, 2) else 0.2)
        for i in range(8):
            place(bgm, SH, t0 + i * BEAT / 2, 0.25 if i % 2 else 0.12)
    arp = [ch[0] + 12, ch[1] + 12, ch[2] + 12, ch[3] + 12, ch[2] + 12, ch[1] + 12, ch[3] + 12, ch[2] + 24]
    for i in range(8):
        place(bgm, pulse(arp[i], 0.5), t0 + i * BEAT / 2, 0.10)

# ダッキング（ナレーション中はBGMを下げる）
blk = 480
env = np.abs(voice)
env = np.concatenate([env, np.zeros((-len(env)) % blk)]).reshape(-1, blk).max(1)
sm = np.zeros_like(env)
e = 0.0
a_atk, a_rel = np.exp(-1 / (SR / blk * 0.03)), np.exp(-1 / (SR / blk * 0.40))
for i, v in enumerate(env):
    coef = a_atk if v > e else a_rel
    e = coef * e + (1 - coef) * v
    sm[i] = e
duck = np.repeat(np.clip(1.0 / (1.0 + 7.0 * np.clip(sm / 0.5, 0, 2)), 0.28, 1.0), blk)[:N]
bgm = bgm / (np.abs(bgm).max() + 1e-9)
bed = bgm * 0.19 * duck

# ---------------------------------------------------------------- SFX
def whoosh(dur=0.7):
    tt = np.arange(int(SR * dur)) / SR
    n = rng.standard_normal(len(tt))
    out = np.zeros_like(n)
    # 帯域をスイープさせた風切り音
    for i, f in enumerate(np.linspace(400, 3200, 8)):
        b, a = butter(2, [f / (SR / 2), min(0.99, f * 1.6 / (SR / 2))], btype="band")
        seg = lfilter(b, a, n)
        w = np.exp(-((tt / dur - (i + 0.5) / 8) ** 2) / 0.02)
        out += seg * w
    return out / np.abs(out).max() * np.sin(np.pi * tt / dur) * 0.55


def pop():
    tt = np.arange(int(SR * 0.18)) / SR
    f = 900 + 900 * np.exp(-tt * 40)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 28) * 0.35


def draw(dur=1.2):
    tt = np.arange(int(SR * dur)) / SR
    f = 300 + 700 * (tt / dur) ** 1.4
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.5 + lp(rng.standard_normal(len(tt)), 2500) * 0.3
    return s * np.sin(np.pi * tt / dur) ** 1.5 * 0.22


def ding():
    tt = np.arange(int(SR * 0.9)) / SR
    return (np.sin(2 * np.pi * 1318.5 * tt) + 0.5 * np.sin(2 * np.pi * 1975.5 * tt)) * np.exp(-tt * 5) * 0.22


def impact():
    tt = np.arange(int(SR * 1.6)) / SR
    f = 32 + 90 * np.exp(-tt * 9)
    boom = np.tanh(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 2.6) * 2.2)
    return boom * 0.7 + whoosh(1.6) * 0.4


def shutter():
    tt = np.arange(int(SR * 0.25)) / SR
    n = hp(rng.standard_normal(len(tt)), 2500, 2)
    return n * (np.exp(-tt * 60) + 0.6 * np.exp(-np.maximum(tt - 0.09, 0) * 60) * (tt > 0.09)) * 0.4


def rumble(dur=2.5):
    tt = np.arange(int(SR * dur)) / SR
    return lp(rng.standard_normal(len(tt)), 140, 3) * np.sin(np.pi * tt / dur) ** 2 * 1.6


SFX = {"whoosh": whoosh, "pop": pop, "draw": draw, "ding": ding, "impact": impact, "shutter": shutter,
       "rumble": rumble}
sfx = np.zeros(N)
for c in chapters[1:]:
    place(sfx, whoosh(0.6), max(0, c["t0"] - 0.45), 0.7)
for name, (key, off, kind) in getattr(S, "CUES", {}).items():
    place(sfx, SFX[kind](), SBY[key]["t0"] + off, 0.9)

mix = voice + bed + sfx * 0.85
mix = np.tanh(mix * 1.05)
mix = mix / np.abs(mix).max() * 0.95
fade = int(1.2 * SR)
mix[-fade:] *= np.linspace(1, 0, fade) ** 1.3
st = np.stack([mix, mix], 1)
with wave.open(os.path.join(WORK, "audio.wav"), "wb") as f:
    f.setnchannels(2)
    f.setsampwidth(2)
    f.setframerate(SR)
    f.writeframes((st * 32767).astype(np.int16).tobytes())

FPS = 30
cues = {n: round(SBY[k]["t0"] + off, 3) for n, (k, off, _) in getattr(S, "CUES", {}).items()}
json.dump({"fps": FPS, "frames": int(np.ceil(TOTAL * FPS)), "total": round(TOTAL, 3), "chapters": chapters,
           "sents": sents, "cues": cues}, open(os.path.join(WORK, "timeline.json"), "w"), ensure_ascii=False, indent=1)


def srt_ts(x):
    return f"{int(x // 3600):02d}:{int(x % 3600 // 60):02d}:{int(x % 60):02d},{int(x * 1000 % 1000):03d}"


with open(os.path.join(WORK, "subs.srt"), "w") as f:
    for i, s in enumerate(sents, 1):
        f.write(f"{i}\n{srt_ts(s['t0'])} --> {srt_ts(min(s['t1'] + 0.25, TOTAL))}\n{s['text']}\n\n")
print(f"OK total={TOTAL:.2f}s frames={int(np.ceil(TOTAL * FPS))}")
for c in chapters:
    print(f"  ch{c['i']} {c['chip']:10s} {c['t0']:6.2f} - {c['t1']:6.2f}")
