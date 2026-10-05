# geo-video — 地理解説動画の図解エンジン

顔出しなし（非属人）の地理解説動画を、**台本 → ナレーション → 実写の地図・衛星写真 → 図解アニメ → MP4** まで自動で作る仕組みです。
地図が奥へ倒れて立体になり、赤い線が「うにょーん」と伸び、テロップと字幕が入ります。

- `output/suez.mp4` … 完成動画「スエズ運河」（約2分・実写の衛星写真を使用）
- `output/reel.mp4` … 図解パターン集（14パターンを1本にまとめたカタログ）
- `output/thumbnail.jpg` … サムネイル

## 図解パターン（14種）

| # | パターン | 関数 | 使いどころ |
|---|---|---|---|
| 01 | 地球儀 → 平面地図 | `Globe.draw(morph=…)` | オープニング、「世界のどこの話か」 |
| 02 | 地図チルト（立体化） | `Cam(pitch=…, bearing=…)` | 真上の2D地図を奥へ倒して3Dに |
| 03 | 赤線うにょーん | `red_line(prog=unyon(…))` | ルート・川・国境・移動の軌跡 |
| 04 | 2ルート比較 | `red_line(dashed=True)` | 遠回り（灰色点線）vs 近道（赤） |
| 05 | 衛星写真ダイブ | `auto_layers()` | 宇宙 → 10m解像度の実写まで連続ズーム |
| 06 | 地形の隆起3D | `Terrain.draw(exag=…)` | 標高データで山がむくむく盛り上がる |
| 07 | 地名ピン | `pin()` / `place()` | 都市はピン＋ラベル、海や半島は直書き |
| 08 | 赤丸＋スポットライト | `ring()` / `spotlight()` | 「ここに注目」 |
| 09 | 国・地域ハイライト | `area()` | 輪郭をなぞってから塗る |
| 10 | 数字カウンター | `counter()` / `donut()` | 割合・距離・年号 |
| 11 | 断面図 | `profile_chart()` | 高さの違いを比較 |
| 12 | 実写フォトカード | `photo_card()` / `photo_frame()` | 実写の提示（撮影日・出典つき） |
| 13 | テロップ3点セット | `chapter_card()` / `headline()` / `subtitle()` | 章タイトル・見出し・字幕 |
| 14 | 夜の地球 | `night_merc.jpg` レイヤー | 都市の明かり・人の集まり |

赤線の「うにょーん」は `unyon()` イージング（ゆっくり出て → ぐいっと伸び → わずかに行き過ぎて戻る）＋先端マーカー＋グロー＋白フチで表現しています。

## 使っている実写データ（すべて公開データ）

| 素材 | 出典 | 用途 |
|---|---|---|
| 地球全体の写真 | NASA Blue Marble / Earth at Night（パブリックドメイン） | 地球儀・世界地図・夜景 |
| 衛星写真 | Copernicus Sentinel-2（ESA、AWS Open Data）※要クレジット | スエズ周辺モザイク、運河、2021/3/24 のエバーギブン号 |
| 標高 | Mapzen Terrain Tiles（AWS Open Data） | 地形3D・断面図 |
| 海岸線・国境 | Natural Earth（パブリックドメイン） | 国のハイライト |

画面右上に出典を常時表示しています（Sentinel-2 は「Contains modified Copernicus Sentinel data」の表記が必要）。

## 作り方

```bash
pip install numpy scipy pillow skia-python pyopenjtalk-plus rasterio pyproj mgrs
apt-get install ffmpeg fonts-noto-cjk fonts-noto-cjk-extra

export GEO_ASSETS=$PWD/assets            # 素材の置き場（約100MB）
python fetch_assets.py                   # 衛星写真・標高・海岸線を取得（初回のみ・約5分）

export GEO_WORK=$PWD/work/suez
python make_audio.py scripts/suez.py     # 台本 → ナレーション・BGM・効果音・timeline.json
python render_suez.py --still 12 40 97   # 静止画で目視チェック
python render_suez.py                    # 全編レンダリング（4並列、約4分）
python render_suez.py --thumb            # サムネイル
```

パターン集は `scripts/reel.py` と `render_reel.py` で同じ手順です。

### 新しいテーマで作るとき

1. `scripts/<テーマ>.py` に台本 `CH`（キー, 読み上げ, 表示）と効果音の同期点 `CUES` を書く
2. 必要なら `fetch_assets.py` に対象地域の衛星写真・標高の取得範囲を足す
3. `render_suez.py` をコピーし、章ごとのシーン関数でカメラと図解パターンを並べる
   （時刻は `T("キー")`＝その文の開始、`Q("cue名")`＝効果音の位置。ナレーションの実測で自動同期）

### ナレーションの声（ElevenLabs v4 / NHK風）

既定は Open JTalk（無料・ローカル、ただし機械音声）。ElevenLabs の **Eleven v4**（`eleven_v4`）に切り替えられます。

```bash
export VE_TTS=elevenlabs
export ELEVENLABS_API_KEY=...        # 環境の設定（環境変数）に入れる。チャットには貼らない
export ELEVENLABS_VOICE_ID=...       # python voices.py search news / sample <ID> で聴き比べて選ぶ
export ELEVENLABS_PRESET=nhk         # nhk（既定）/ nhk_tag / natural
./run_suez.sh                        # → output/suez_elevenlabs.mp4
```

| プリセット | 設定 | 狙い |
|---|---|---|
| `nhk`（既定） | Stability 1.0（揺れ最小）・Similarity 0.75・タグなし | ニュース読みのように平らで明瞭 |
| `nhk_tag` | Stability 0.5 ＋ 各文の頭に `[calm, clear, measured delivery, like a public-broadcast news anchor]` | 落ち着いた抑揚をタグで指示 |
| `natural` | Stability 0.5・タグなし | 声本来の表現 |

- v4 の声の設定は Stability と Similarity だけ（Speed / Style / SSML は無し）。`language_code=ja` と `seed` を毎回付けています
- 前後の文を `previous_text` / `next_text` で渡し、文をまたいだ抑揚をそろえます（未対応なら自動で外して再送）
- ElevenLabs では表示文（漢字まじり）をそのまま読ませ、「km」「〜」「6日間」など割れやすい箇所だけ `scripts/*.py` の `TTS_TEXT["elevenlabs"]` で書き換えています
- 同じ文・同じ設定は `work/*/tts_cache` から再利用するので、作り直しで課金は増えません
- 実在のアナウンサーの声を本人の同意なくクローンしないこと。ライブラリの声か Voice Design で「標準語（東京アクセント）の落ち着いたニュース読み」の声を使います
- `ELEVENLABS_DRY_RUN=1` で API を呼ばずに送信内容だけ確認できます

収録済みの音声を使う場合は `VE_TTS=wavdir VE_WAV_DIR=./voice`（ファイル名は台本キー.wav）。
どの声でも、尺・字幕・映像のタイミングは自動で追従します（video-engine スキルと同じ設計）。

## ファイル

| ファイル | 役割 |
|---|---|
| `geokit.py` | 図解パターンのライブラリ（カメラ、レイヤー、赤線、ピン、テロップ、地形、地球儀） |
| `fetch_assets.py` | 実写素材の取得とメルカトル座標への変換、Sentinel-2 の色合わせ・モザイク |
| `make_audio.py` | 台本 → TTS → 実測タイムライン → BGM（ダッキング）＋効果音 |
| `tts_adapters.py` | 音声合成の切替層（openjtalk / elevenlabs v4 / wavdir） |
| `voices.py` | ElevenLabs の日本語の声を検索・聴き比べ |
| `run_suez.sh` | 依存導入 → 素材取得 → 音声 → レンダリングを一発実行 |
| `render_suez.py` | 「スエズ運河」本編の演出 |
| `render_reel.py` | 図解パターン集の演出 |
| `scripts/` | 台本 |
