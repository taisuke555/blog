# -*- coding: utf-8 -*-
"""video-engine TTSアダプタ層（video-engine スキルから同梱コピー）。

共通インターフェース:
    tts = get_tts(sr=48000, speed=1.0, half_tone=0.0)
    wav = tts.synth(text, key=None)   # -> np.ndarray float64 mono, -1..1, tts.sr

切替(環境変数 VE_TTS):
    openjtalk  (既定) ローカル合成。無料・オフライン。音質は機械音声レベル。
    elevenlabs        ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID 必須。
    wavdir            VE_WAV_DIR/{key}.wav を読込(n8n等で事前バッチ生成した音声)。

make_audio.py 側は get_tts() を呼ぶだけ。台本・尺合わせ・BGM・字幕は声に依存しない。
"""
import os, json, subprocess, tempfile
import numpy as np


def _resample(w, sr_from, sr_to):
    if sr_from == sr_to:
        return w
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(int(sr_from), int(sr_to))
    return resample_poly(w, sr_to // g, sr_from // g)


def _ffmpeg_decode(path, sr):
    """任意の音声ファイル -> float64 mono @sr (ffmpeg経由)"""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "f64le", "-ac", "1",
         "-ar", str(sr), "pipe:1"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float64).copy()


class OpenJTalkTTS:
    """pyopenjtalk-plus(辞書同梱)。pip install pyopenjtalk-plus"""
    def __init__(self, sr=48000, speed=1.0, half_tone=0.0, **kw):
        import pyopenjtalk
        self._p = pyopenjtalk
        self.sr, self.speed, self.half_tone = sr, speed, half_tone

    def synth(self, text, key=None):
        lab = self._p.extract_fullcontext(text)
        w, sr = self._p.synthesize(lab, speed=self.speed, half_tone=self.half_tone)
        return _resample(np.asarray(w, np.float64) / 32768.0, sr, self.sr)


class ElevenLabsTTS:
    """ElevenLabs API。mp3で受けてffmpegでデコード(プラン差に依存しない経路)。

    ※注意: API遮断サンドボックスでは未検証。初回実行時にレスポンス形式を確認し、
    エラー時は r.read() の中身(JSONエラー)を表示して原因を特定すること。
    speed/half_toneは無視される(声質はVoice側の設定で調整)。
    """
    def __init__(self, sr=48000, model_id="eleven_multilingual_v2", **kw):
        self.sr = sr
        self.key = os.environ["ELEVENLABS_API_KEY"]
        self.voice = os.environ["ELEVENLABS_VOICE_ID"]
        self.model = os.environ.get("ELEVENLABS_MODEL", model_id)

    def synth(self, text, key=None):
        import urllib.request
        url = (f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}"
               f"?output_format=mp3_44100_128")
        body = json.dumps({"text": text, "model_id": self.model}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "xi-api-key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            mp3 = r.read()
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3)
            tmp = f.name
        try:
            return _ffmpeg_decode(tmp, self.sr)
        finally:
            os.unlink(tmp)


class WavDirTTS:
    """事前生成音声の読込: {VE_WAV_DIR}/{key}.wav (keyは台本の各文キー)。
    n8n+ElevenLabsで先にバッチ生成 → このアダプタで尺実測、が現行運用と相性最良。"""
    def __init__(self, sr=48000, **kw):
        self.sr = sr
        self.dir = os.environ["VE_WAV_DIR"]

    def synth(self, text, key=None):
        path = os.path.join(self.dir, f"{key}.wav")
        if not os.path.exists(path):
            raise FileNotFoundError(f"音声ファイルなし: {path}(台本キー '{key}')")
        return _ffmpeg_decode(path, self.sr)


ADAPTERS = {"openjtalk": OpenJTalkTTS, "elevenlabs": ElevenLabsTTS, "wavdir": WavDirTTS}


def get_tts(sr=48000, **kw):
    name = os.environ.get("VE_TTS", "openjtalk").lower()
    if name not in ADAPTERS:
        raise ValueError(f"VE_TTS={name} は未対応。選択肢: {list(ADAPTERS)}")
    return ADAPTERS[name](sr=sr, **kw)


if __name__ == "__main__":
    t = get_tts()
    w = t.synth("アダプタのセルフテストです。", key="selftest")
    print(f"{type(t).__name__}: {len(w)/t.sr:.2f}s, peak={np.abs(w).max():.3f}")
