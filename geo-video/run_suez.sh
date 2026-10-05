#!/usr/bin/env bash
# スエズ運河の動画を一発で作る：依存の導入 → 素材取得（無ければ）→ 音声 → レンダリング → output/
#   ElevenLabs v4 で作る例:  VE_TTS=elevenlabs ./run_suez.sh
#   （ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID を環境に設定しておく。声の調子は ELEVENLABS_PRESET=nhk|nhk_tag|natural）
set -euo pipefail
cd "$(dirname "$0")"
python3 -c "import skia, pyopenjtalk, rasterio" 2>/dev/null || pip install -q -r requirements.txt
fc-list | grep -q "Noto Sans CJK" || (apt-get update -qq && apt-get install -y -qq fonts-noto-cjk fonts-noto-cjk-extra)
python3 -c "import skia" 2>/dev/null || apt-get install -y -qq libegl1 libgl1 libfontconfig1

export GEO_ASSETS=${GEO_ASSETS:-$PWD/assets}
export GEO_WORK=${GEO_WORK:-$PWD/work/suez}
[ -f "$GEO_ASSETS/region_s2.png" ] && [ -f "$GEO_ASSETS/dem_japan.npy" ] || python3 fetch_assets.py
python3 make_audio.py scripts/suez.py
python3 render_suez.py
python3 render_suez.py --thumb
mkdir -p output
tag=${VE_TTS:-openjtalk}
ffmpeg -v error -y -i "$GEO_WORK/suez.mp4" -c:v libx264 -preset slow -crf 25 -pix_fmt yuv420p \
  -c:a aac -b:a 160k -movflags +faststart "output/suez_${tag}.mp4"
echo "-> output/suez_${tag}.mp4"
