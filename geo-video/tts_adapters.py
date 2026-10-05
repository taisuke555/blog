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

    def synth(self, text, key=None, **kw):
        lab = self._p.extract_fullcontext(text)
        w, sr = self._p.synthesize(lab, speed=self.speed, half_tone=self.half_tone)
        return _resample(np.asarray(w, np.float64) / 32768.0, sr, self.sr)


class ElevenLabsTTS:
    """ElevenLabs API（既定: Eleven v4）。mp3で受けてffmpegでデコード(プラン差に依存しない経路)。

    環境変数:
        ELEVENLABS_API_KEY   必須
        ELEVENLABS_VOICE_ID  必須（声のID）
        ELEVENLABS_MODEL     既定 eleven_v4（低遅延版は eleven_v4_turbo）
        ELEVENLABS_PRESET    nhk（既定）/ nhk_tag / natural … 下の PRESETS 参照
        ELEVENLABS_STABILITY / ELEVENLABS_SIMILARITY / ELEVENLABS_PREFIX で個別に上書き可
        ELEVENLABS_SEED      既定 1234（毎回の揺れを抑える。完全一致は保証されない）
        ELEVENLABS_DRY_RUN=1 APIを呼ばずにリクエスト内容だけ表示（設定確認用）

    v4 の注意（2026-09 時点の公開情報）:
        - 声の設定は Stability と Similarity のみ。Speed / Style / SSML は無い
        - 間は [pause] / [long pause] などのタグ、話し方は [calm] などの英語タグで指示
        - language_code="ja" を付けないと中国語風の読みが混ざることがある
        - previous_text / next_text で前後の文脈を渡すと、文をまたいだ抑揚がそろう
    """
    PRESETS = {
        # 公共放送のニュース読み：揺れを最小に、演技させない
        "nhk": {"stability": 1.0, "similarity_boost": 0.75, "prefix": ""},
        # 同じ方向をタグで指示する版（Robust だとタグが効きにくいので Natural で）
        "nhk_tag": {"stability": 0.5, "similarity_boost": 0.75,
                    "prefix": "[calm, clear, measured delivery, like a public-broadcast news anchor] "},
        "natural": {"stability": 0.5, "similarity_boost": 0.75, "prefix": ""},
    }

    def __init__(self, sr=48000, model_id="eleven_v4", **kw):
        self.sr = sr
        self.dry = os.environ.get("ELEVENLABS_DRY_RUN") == "1"
        self.key = os.environ.get("ELEVENLABS_API_KEY", "") if self.dry else os.environ["ELEVENLABS_API_KEY"]
        self.voice = os.environ.get("ELEVENLABS_VOICE_ID", "VOICE_ID") if self.dry else os.environ["ELEVENLABS_VOICE_ID"]
        self.model = os.environ.get("ELEVENLABS_MODEL", model_id)
        p = dict(self.PRESETS[os.environ.get("ELEVENLABS_PRESET", "nhk")])
        for env, name in [("ELEVENLABS_STABILITY", "stability"), ("ELEVENLABS_SIMILARITY", "similarity_boost")]:
            if os.environ.get(env):
                p[name] = float(os.environ[env])
        if "ELEVENLABS_PREFIX" in os.environ:
            p["prefix"] = os.environ["ELEVENLABS_PREFIX"]
        self.preset = p
        self.seed = int(os.environ.get("ELEVENLABS_SEED", "1234"))
        self.use_context = True

    def payload(self, text, prev_text=None, next_text=None):
        body = {"text": self.preset["prefix"] + text, "model_id": self.model, "language_code": "ja",
                "seed": self.seed,
                "voice_settings": {"stability": self.preset["stability"],
                                   "similarity_boost": self.preset["similarity_boost"]}}
        if self.use_context:
            if prev_text:
                body["previous_text"] = prev_text
            if next_text:
                body["next_text"] = next_text
        return body

    def synth(self, text, key=None, prev_text=None, next_text=None):
        import urllib.request, urllib.error
        url = (f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}"
               f"?output_format=mp3_44100_128")
        body = self.payload(text, prev_text, next_text)
        if self.dry:
            print(f"[dry-run] {key}: {json.dumps(body, ensure_ascii=False)}")
            return np.zeros(int(self.sr * (0.25 + 0.13 * len(text))))
        for attempt in range(4):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
                "xi-api-key": self.key, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    mp3 = r.read()
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if e.code == 422 and self.use_context and ("previous_text" in body or "next_text" in body):
                    # モデルが文脈指定に未対応なら外して再送
                    print(f"  [{key}] 422 with context -> retry without previous/next_text: {detail[:200]}")
                    self.use_context = False
                    body = self.payload(text)
                    continue
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    import time
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise RuntimeError(f"ElevenLabs {e.code} ({key}): {detail}") from None
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

    def synth(self, text, key=None, **kw):
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
