# -*- coding: utf-8 -*-
"""ElevenLabs の声選び：日本語の声を探して、本編の冒頭2文で聴き比べる。

usage:
  python voices.py mine                         # 自分の「My Voices」一覧
  python voices.py search [キーワード]           # ボイスライブラリから日本語の声を検索（既定: news）
  python voices.py sample VOICE_ID [VOICE_ID…]  # 各声で冒頭2文を生成 → $GEO_WORK/voice_samples/*.mp3
  python voices.py audition [人数]              # 男性・日本語のニュース/ナレーション向けの声を探し、候補を自動で聴き比べ用に生成

性別は ELEVENLABS_GENDER（既定 male）で絞り込む。

ライブラリの声が API で使えない場合は、ElevenLabs の画面で「My Voices」に追加してから使う。
実在の人物（特定のアナウンサー等）の声を本人の同意なくクローンしないこと。
"""
import os, sys, json, urllib.request, urllib.parse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
API = "https://api.elevenlabs.io/v1"
KEY = os.environ.get("ELEVENLABS_API_KEY", "")
GENDER = os.environ.get("ELEVENLABS_GENDER", "male")   # ユーザー指定：男性の声


def get(path, **params):
    url = f"{API}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"xi-api-key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"ElevenLabs {e.code}: {e.read().decode('utf-8', 'replace')}")


def show(voices):
    for v in voices:
        labels = v.get("labels") or {}
        meta = " / ".join(x for x in [v.get("gender") or labels.get("gender"), v.get("age") or labels.get("age"),
                                      v.get("accent") or labels.get("accent"), v.get("use_case") or labels.get("use_case")] if x)
        print(f"{v['voice_id']}  {v.get('name', '')}  [{meta}]")
        if v.get("description"):
            print(f"    {v['description'][:110]}")


def sample(voice_ids):
    from tts_adapters import ElevenLabsTTS
    import importlib.util
    spec = importlib.util.spec_from_file_location("s", os.path.join(os.path.dirname(__file__), "scripts", "suez.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    lines = [(k, d or t) for _, _, L in S.CH for k, t, d in L][:2]
    out = os.path.join(os.environ.get("GEO_WORK", "work"), "voice_samples")
    os.makedirs(out, exist_ok=True)
    for vid in voice_ids:
        os.environ["ELEVENLABS_VOICE_ID"] = vid
        tts = ElevenLabsTTS(sr=44100)
        import numpy as np, wave
        parts = []
        for i, (k, text) in enumerate(lines):
            w = tts.synth(text, key=k, prev_text=lines[i - 1][1] if i else None,
                          next_text=lines[i + 1][1] if i + 1 < len(lines) else None)
            parts += [w, np.zeros(int(44100 * 0.5))]
        a = np.concatenate(parts)
        wav = os.path.join(out, f"{vid}.wav")
        with wave.open(wav, "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(44100)
            f.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", wav, "-b:a", "128k", wav[:-4] + ".mp3"], check=True)
        os.remove(wav)
        print("->", wav[:-4] + ".mp3")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "search"
    if cmd == "mine":
        show(get("/voices")["voices"])
    elif cmd == "search":
        q = sys.argv[2] if len(sys.argv) > 2 else "news"
        show(get("/shared-voices", language="ja", gender=GENDER, search=q, page_size=30)["voices"])
    elif cmd == "sample":
        sample(sys.argv[2:])
    elif cmd == "audition":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
        seen, picks = set(), []
        for q in ["news", "narration", "documentary", "announcer"]:
            for v in get("/shared-voices", language="ja", gender=GENDER, search=q, page_size=20)["voices"]:
                if v["voice_id"] not in seen:
                    seen.add(v["voice_id"])
                    picks.append(v)
        picks = picks[:n]
        show(picks)
        ok = []
        for v in picks:
            try:
                sample([v["voice_id"]])
                ok.append(v["voice_id"])
            except Exception as e:  # ライブラリの声は My Voices への追加が必要な場合がある
                print(f"  skip {v['voice_id']} ({v.get('name', '')}): {e}")
        print("samples:", ok)
    else:
        print(__doc__)
