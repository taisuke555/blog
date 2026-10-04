# -*- coding: utf-8 -*-
"""図解パターン集（カタログ動画）のレンダリング。章 = 1パターン（scripts/reel.py）。

usage:
  GEO_ASSETS=... GEO_WORK=... python make_audio.py scripts/reel.py
  GEO_ASSETS=... GEO_WORK=... python render_reel.py              # → reel.mp4
  GEO_ASSETS=... GEO_WORK=... python render_reel.py --still 3 9  # 静止画QA
"""
import os, sys, json, math, subprocess, importlib.util
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geokit import *  # noqa

WORK = os.environ.get("GEO_WORK", os.path.join(os.getcwd(), "work"))
TL = json.load(open(os.path.join(WORK, "timeline.json")))
CHS = TL["chapters"]
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "scripts", f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


R = load("reel")
SZ = load("suez")


class Ctx:
    pass


X = None


def init():
    global X
    if X is not None:
        return
    X = Ctx()
    X.A = Assets()
    X.G = Globe(X.A)
    X.TS = Terrain(X.A)
    X.TJ = Terrain(X.A, "dem_japan.npy", step=8, style="relief")
    X.suez = route_uv(SZ.ROUTE_SUEZ, 700)
    X.suez_ll = np.c_[unmerc(X.suez[:, 0], X.suez[:, 1])]
    cn = np.asarray(SZ.CANAL)
    X.canal = np.stack(merc(cn[:, 0], cn[:, 1]), 1)
    X.tokaido = route_uv(R.TOKAIDO, 300)
    X.panama = route_uv(R.ROUTE_PANAMA, 500)
    X.horn = route_uv(R.ROUTE_HORN, 700)
    lon = np.linspace(138.25, 139.25, 200)
    u, v = merc(lon, np.full_like(lon, FUJI_LAT))
    X.fuji_profile = X.TJ.height_at(u, v)
    lon2 = np.linspace(139.30, 140.30, 200)
    u2, v2 = merc(lon2, np.full_like(lon2, 35.70))
    X.kanto_profile = X.TJ.height_at(u2, v2)
    a = X.A.layer("evergiven_s2.png").arr
    h, w = a.shape[:2]
    crop = np.ascontiguousarray(a[int(h * 0.12):int(h * 0.88), int(w * 0.1):int(w * 0.9)].copy())
    crop[..., 3] = 255
    X.eg_img = skia.Image.fromarray(crop, colorType=skia.kRGBA_8888_ColorType).withDefaultMipmaps()


FUJI_LAT = 35.3606


def chap(t):
    for c in CHS:
        if c["t0"] - 0.45 <= t < c["t1"] - 0.45:
            return c
    return CHS[-1]


def label_card(c, ch, t):
    """左上：パターン番号と名前（全パターン共通）"""
    k = seg(t, ch["t0"] - 0.3, ch["t0"] + 0.5)
    out = seg(t, ch["t1"] - 0.9, ch["t1"] - 0.5)
    headline(c, ch["label"], k, sub=f"PATTERN {ch['chip'][1:]}", out=out)


def subtitles(c, t):
    for s in TL["sents"]:
        a = fade_window(t, s["t0"] - 0.08, s["t1"] + 0.6, 0.12, 0.2)
        if a > 0:
            subtitle(c, s["text"], a, size=46)


def jp_cam(lon, lat, km, **kw):
    return Cam.at(lon, lat, km, **kw)


def draw_japan_flat(c, cam, exag=0.0):
    c.clear(skia.Color(18, 44, 92))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    X.TJ.draw(c, cam, exag=exag)


# ---------------------------------------------------------------- patterns
def p01(c, t, a, b):
    """地球儀 → 平面"""
    m = seg(t, a + 2.6, b - 0.8)
    lon0 = lerp(60, 138, ease_io(seg(t, a - 0.4, a + 2.6)))
    lat0 = lerp(10, 33, ease_io(seg(t, a - 0.4, a + 2.6)))
    cam = Cam.at(138, 33, 9000)
    Rf = (W / cam.span) / (2 * math.pi * math.cos(math.radians(33)))
    Rg = lerp(420, Rf, ease_io(seg(t, a + 1.5, a + 3.2)))
    X.G.space(c, t, 1 - m)
    if m > 0:
        draw_layer(c, cam, X.A.layer("world_merc.jpg"), ease_io(seg(m, 0.7, 1)))
    X.G.draw(c, lon0, lat0, Rg, morph=m, cam=cam)
    credit(c, "画像: NASA Blue Marble")


def p02(c, t, a, b):
    """地図チルト"""
    k = ease_io(seg(t, a + 0.6, a + 3.6))
    cam = Cam.at(138.95, 35.30, 230, pitch=lerp(0, 55, k), bearing=lerp(0, -22, k))
    draw_japan_flat(c, cam, exag=0)
    haze(c, cam)
    k2 = seg(t, a + 0.3, a + 1.0) * (1 - seg(t, a + 2.0, a + 2.6))
    if k2 > 0:
        stroke_text(c, "真上から（2D）", W / 2, 300, font(TF_BLACK, 70), align="center", alpha=k2, sw=10)
    k3 = seg(t, a + 3.4, a + 4.0)
    if k3 > 0:
        stroke_text(c, "奥へ倒して立体に（3D）", W / 2, 300, font(TF_BLACK, 70), align="center", alpha=k3, sw=10,
                    stroke=RED)
    credit(c, "標高: Mapzen Terrain Tiles（段彩・陰影で表現）")


def p03(c, t, a, b):
    """赤線うにょーん（東海道）"""
    cam = cam_path(t, [(a - 0.5, Cam.at(139.05, 35.30, 190, pitch=45, bearing=-12)),
                       (b, Cam.at(139.0, 35.25, 170, pitch=50, bearing=-20))])
    draw_japan_flat(c, cam)
    haze(c, cam)
    x, y = geo_line(cam, X.tokaido)
    p = unyon(seg(t, a + 0.6, a + 4.2))
    head = red_line(c, x, y, p, t=t, width=11, head="dot" if p < 1 else "arrow")
    for name, ll, up, side in [R.PLACES_JP[0], R.PLACES_JP[4]]:
        px, py = cam.lonlat(*ll)
        pin(c, px, py, name, seg(t, a + (0.3 if name == "東京" else 4.1), a + (0.8 if name == "東京" else 4.6)),
            size=40, t=t, up=up, side=side)
    k = seg(t, a + 4.4, a + 5.0)
    if k > 0:
        stroke_text(c, "東海道（東京→静岡）", W / 2 + 240, 300, font(TF_BLACK, 60), fill=WHITE, stroke=RED, sw=12,
                    align="center", alpha=ease_o(k))
    credit(c, "標高: Mapzen Terrain Tiles")


def p04(c, t, a, b):
    """2ルート比較（パナマ運河）"""
    cam = cam_path(t, [(a - 0.5, Cam.at(-76, -6, 31000, pitch=8, oy=-40)),
                       (b, Cam.at(-78, -4, 29000, pitch=18, bearing=3, oy=-40))])
    c.clear(skia.Color(4, 8, 18))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    haze(c, cam, color=(120, 150, 190))
    gray = ease_io(seg(t, a + 3.0, a + 3.6))
    x, y = geo_line(cam, X.horn)
    ph = unyon(seg(t, a + 0.4, a + 3.0))
    if gray < 1:
        red_line(c, x, y, ph, t=t, alpha=1 - gray)
    if gray > 0:
        red_line(c, x, y, ph, t=t, color=(200, 205, 214), alpha=gray * 0.85, dashed=True, glow=False, head=None, width=7)
    x, y = geo_line(cam, X.panama)
    red_line(c, x, y, unyon(seg(t, a + 3.4, a + 5.4)), t=t, width=10)
    area(c, cam, X.A.vec["countries"]["Panama"], seg(t, a + 4.6, a + 5.6), t=t)
    for name, ll, up, side in [("ニューヨーク", R.NYC, 70, 130), ("サンフランシスコ", R.SF, 40, 200)]:
        px, py = cam.lonlat(*ll)
        pin(c, px, py, name, seg(t, a + 0.1, a + 0.6), size=34, t=t, up=up, side=side)
    px, py = cam.lonlat(-67.3, -56.6)
    pin(c, px, py, "ホーン岬", seg(t, a + 1.8, a + 2.3), size=34, t=t, up=50, side=-160)
    px, py = cam.lonlat(-79.7, 9.1)
    pin(c, px, py, "パナマ運河", seg(t, a + 4.8, a + 5.3), size=38, t=t, up=90, side=170)
    credit(c, "画像: NASA Blue Marble / 国境: Natural Earth")


def p05(c, t, a, b):
    """衛星写真ダイブ"""
    k = seg(t, a - 0.3, b - 0.6)
    c0 = Cam.at(40, 25, 16000, pitch=0)
    c1 = Cam.at(32.58, 30.02, 40, pitch=10)
    c2 = Cam.at(32.58, 30.0176, 5.0, pitch=28, bearing=-12)
    cam = c0.mix(c1, ease_io(seg(k, 0, 0.72))) if k < 0.72 else c1.mix(c2, ease_io(seg(k, 0.72, 1)))
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",), extra=[("evergiven_s2.png", smoothstep(60, 22, cam.mpp))])
    haze(c, cam)
    km = cam.span * cam.m_per_unit / 1000
    stroke_text(c, f"画面幅 {km:,.0f} km" if km >= 10 else f"画面幅 {km:,.1f} km", W - 60, H - 210,
                font(TF_BLACK, 44), align="right", sw=8)
    credit(c, "画像: NASA Blue Marble → Copernicus Sentinel-2（10m解像度）")


def p06(c, t, a, b):
    """地形の隆起（富士山）"""
    cam = cam_path(t, [(a - 0.5, Cam.at(138.75, 35.30, 110, pitch=50, bearing=-30)),
                       (b, Cam.at(138.73, 35.33, 85, pitch=60, bearing=-62))])
    exag = 3.0 * ease_io(seg(t, a + 0.8, a + 3.4))
    draw_japan_flat(c, cam, exag=exag)
    haze(c, cam)
    zf = X.TJ.height_at(*merc(*R.FUJI)) * exag
    fx, fy, _ = cam.project(*merc(*R.FUJI), zf)
    pin(c, float(fx), float(fy), "富士山", seg(t, a + 3.2, a + 3.7), size=42, sub="3,776m", t=t, up=110, side=60)
    stroke_text(c, f"高さの強調 ×{exag:.1f}", 60, H - 200, font(TF_BLACK, 44), sw=8)
    credit(c, "標高: Mapzen Terrain Tiles（段彩・陰影）")


def p07(c, t, a, b):
    """地名ピン"""
    cam = Cam.at(139.15, 35.35, 190, pitch=42, bearing=-8)
    draw_japan_flat(c, cam, exag=1.2)
    haze(c, cam)
    for i, (name, ll, up, side) in enumerate(R.PLACES_JP):
        z = X.TJ.height_at(*merc(*ll)) * 1.2
        px, py, _ = cam.project(*merc(*ll), z)
        pin(c, float(px), float(py), name, seg(t, a + 0.3 + i * 0.45, a + 0.8 + i * 0.45), size=40, t=t, up=up,
            side=side, sub="3,776m" if name == "富士山" else None)
    x, y = cam.lonlat(139.6, 35.05)
    place(c, x, y, "相模湾", seg(t, a + 2.8, a + 3.6), size=52, italic_spacing=True)
    credit(c, "標高: Mapzen Terrain Tiles")


def p08(c, t, a, b):
    """赤丸＋スポットライト"""
    cam = Cam.at(32.5800, 30.0176, 3.6, pitch=20, bearing=-8)
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",), extra=[("evergiven_s2.png", 1.0)])
    x, y, _ = cam.project(*merc(*SZ.EVER_GIVEN))
    k = seg(t, a + 0.6, a + 1.6)
    spotlight(c, float(x), float(y), 240, k)
    ring(c, float(x), float(y), 125, k, t=t)
    pin(c, float(x) + 90, float(y) - 90, "エバーギブン号", seg(t, a + 1.5, a + 2.0), size=40, sub="全長400m", up=100,
        side=180, t=t)
    credit(c, "画像: Copernicus Sentinel-2（2021年3月24日）")


def p09(c, t, a, b):
    """国・地域ハイライト（日本）"""
    cam = cam_path(t, [(a - 0.5, Cam.at(137, 37, 3600, pitch=20)), (b, Cam.at(137, 37, 3200, pitch=35, bearing=-6))])
    c.clear(skia.Color(4, 8, 18))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    haze(c, cam, color=(120, 150, 190))
    dim(c, 0.25)
    polys = X.A.vec["countries"]["Japan"]
    area(c, cam, polys, seg(t, a + 0.4, a + 3.6), t=t, width=4)
    x, y = cam.lonlat(144.5, 33.5)
    place(c, x, y, "日本", seg(t, a + 2.6, a + 3.4), size=90, italic_spacing=True)
    credit(c, "画像: NASA Blue Marble / 国境: Natural Earth")


def p10(c, t, a, b):
    """数字カウンター＋円グラフ"""
    cam = Cam.at(32.40, 30.55, 230, pitch=50, bearing=4 + (t - a) * 1.5, ox=300)
    c.clear(skia.Color(150, 172, 200))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    X.TS.draw(c, cam, exag=3)
    haze(c, cam)
    zc = X.TS.height_at(X.canal[:, 0], X.canal[:, 1]) * 3 + 90
    x, y, _ = cam.project(X.canal[:, 0], X.canal[:, 1], zc)
    red_line(c, x, y, 1.0, t=t, head=None)
    dim(c, 0.35)
    k = seg(t, a + 0.4, a + 1.8)
    donut(c, 520, 590, 185, 0.12, k)
    counter(c, 520, 632, 12, seg(t, a + 0.6, a + 2.0), fmt="{:.0f}", unit="%", size=124, prefix="約")
    stroke_text(c, "世界の貿易量のうち", 520, 330, font(TF_BLACK, 50), align="center", fill=YELLOW, sw=8,
                alpha=clamp01(k * 3))
    counter(c, 1400, 500, 193, seg(t, a + 1.6, a + 3.0), fmt="{:.0f}", unit="km", size=140, label="運河の全長",
            label_size=48)
    credit(c, "標高: Mapzen Terrain Tiles / 画像: Copernicus Sentinel-2")


def p11(c, t, a, b):
    """断面図（富士山を東西に切る vs 関東平野）"""
    cam = Cam.at(139.25, 35.50, 330, pitch=45, bearing=-6, ox=270, oy=60)
    draw_japan_flat(c, cam, exag=2.0)
    haze(c, cam)
    for lat, lo0, lo1, col in [(FUJI_LAT, 138.25, 139.25, YELLOW), (35.70, 139.30, 140.30, RED_HI)]:
        lon = np.linspace(lo0, lo1, 60)
        u, v = merc(lon, np.full_like(lon, lat))
        z = X.TJ.height_at(u, v) * 2.0 + 60
        x, y, _ = cam.project(u, v, z)
        red_line(c, x, y, ease_io(seg(t, a + 0.3, a + 1.6)), color=RED if col == RED_HI else (230, 170, 20),
                 t=t, width=7, glow=False, head=None)
    profile_chart(c, 40, 230, 620, 400,
                  [("関東平野", X.kanto_profile, RED_HI), ("富士山を東西に", X.fuji_profile, YELLOW)],
                  seg(t, a + 0.8, a + 4.0), title="標高の断面図", ymax=4000)
    credit(c, "標高: Mapzen Terrain Tiles")


def p12(c, t, a, b):
    """実写フォトカード"""
    cam = Cam.at(32.56, 29.86, 26, pitch=40, bearing=4 + (t - a) * 1.2)
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",), extra=[("queue_s2.png", 1.0)])
    haze(c, cam)
    dim(c, 0.45 * seg(t, a + 0.3, a + 1.0))
    photo_card(c, X.eg_img, seg(t, a + 0.5, a + 1.6), W / 2 + 60, 790, 760,
               caption="2021年3月24日  Copernicus Sentinel-2")
    photo_frame(c, seg(t, a + 1.4, a + 2.0), sub="撮影日・出典を必ず表示")
    credit(c, "画像: Copernicus Sentinel-2（2021年3月24日 08:41 UTC）")


def p13(c, t, a, b):
    """テロップ3点セット"""
    cam = Cam.at(32.45, 30.55, 330, pitch=40, bearing=-12 + (t - a) * 1.5)
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",))
    haze(c, cam)
    x, y, _ = cam.project(X.canal[:, 0], X.canal[:, 1])
    red_line(c, x, y, 1.0, t=t, head=None)
    chapter_card(c, "02", "衛星写真で見る", seg(t, a + 0.2, a + 1.2), out=seg(t, a + 2.2, a + 2.8))
    headline(c, "衛星写真で見る", seg(t, a + 2.6, a + 3.4), sub="見出しテロップ", y=330, x=70)
    k = seg(t, a + 3.4, a + 3.8)
    if k > 0:
        stroke_text(c, "↓ 字幕（ナレーションと同期）", W / 2, H - 230, font(TF_BOLD, 40), align="center", fill=YELLOW,
                    alpha=k, sw=8)
    credit(c, "画像: Copernicus Sentinel-2")


def p14(c, t, a, b):
    """夜の地球"""
    cam = cam_path(t, [(a - 0.5, Cam.at(75, 28, 14000, pitch=34)), (b + 1, Cam.at(70, 30, 12500, pitch=44, bearing=6))])
    c.clear(skia.Color(2, 3, 8))
    draw_layer(c, cam, X.A.layer("night_merc.jpg"))
    # 都市の明かりを加算でもう一度重ねて強調
    draw_layer(c, cam, X.A.layer("night_merc.jpg"), 0.7, blend=skia.BlendMode.kPlus)
    haze(c, cam, color=(30, 40, 70))
    x, y = geo_line(cam, X.suez)
    red_line(c, x, y, unyon(seg(t, a + 0.5, a + 3.6)), t=t, width=9, color=(255, 70, 60))
    px, py = cam.lonlat(32.4, 30.5)
    pin(c, px, py, "スエズ運河", seg(t, a + 2.2, a + 2.7), size=36, t=t, up=90, side=-80)
    credit(c, "画像: NASA Earth at Night")
    k = seg(t, b - 3.2, b - 2.6)
    if k > 0:
        f = font(TF_MED, 24)
        lines = ["出典: NASA Blue Marble / NASA Earth at Night",
                 "Contains modified Copernicus Sentinel data 2021（Sentinel-2, AWS Open Data）",
                 "標高: Mapzen Terrain Tiles（AWS Open Data） / 国境: Natural Earth"]
        for i, ln in enumerate(lines):
            c.drawString(ln, W - 60 - f.measureText(ln), 120 + i * 34, f, skia.Paint(Color=C(WHITE, 0.85 * k), AntiAlias=True))
    dim(c, seg(t, TL["total"] - 1.5, TL["total"] - 0.2))


PATTERNS = [p01, p02, p03, p04, p05, p06, p07, p08, p09, p10, p11, p12, p13, p14]


def render(c, t):
    ch = chap(t)
    i = ch["i"]
    a, b = ch["t0"], ch["t1"]
    PATTERNS[i](c, t, a, b)
    vignette(c, 0.45)
    # 章の切り替え：白いワイプ
    for cc in CHS[1:]:
        k = seg(t, cc["t0"] - 0.75, cc["t0"] - 0.15)
        if 0 < k < 1:
            w = math.sin(math.pi * k)
            c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Color=C((255, 255, 255), 0.85 * w)))
    label_card(c, ch, t)
    subtitles(c, t)
    if t < 1.0:
        dim(c, 1 - seg(t, 0, 1.0))


def render_range(args):
    a, b, out = args
    init()
    s = new_surface()
    w = Writer(out)
    for fi in range(a, b):
        c = s.getCanvas()
        c.save()
        render(c, fi / FPS)
        c.restore()
        w.write(s)
    w.close()
    return out


def stills(times):
    init()
    from PIL import Image
    s = new_surface()
    for t in times:
        c = s.getCanvas()
        c.save()
        render(c, float(t))
        c.restore()
        p = os.path.join(WORK, f"still_{float(t):06.2f}.jpg")
        Image.fromarray(s.makeImageSnapshot().toarray()[..., :3]).save(p, quality=90)
        print(p)


def main(workers=4):
    import multiprocessing as mp
    n = TL["frames"]
    seg_dir = os.path.join(WORK, "segments")
    os.makedirs(seg_dir, exist_ok=True)
    k = workers * 3
    bounds = np.linspace(0, n, k + 1).astype(int)
    jobs = [(int(bounds[i]), int(bounds[i + 1]), os.path.join(seg_dir, f"seg_{i:03d}.mp4")) for i in range(k)]
    with mp.get_context("fork").Pool(workers) as pool:
        for i, out in enumerate(pool.imap(render_range, jobs)):
            print(f"  segment {i + 1}/{len(jobs)} done", flush=True)
    lst = os.path.join(seg_dir, "list.txt")
    open(lst, "w").write("".join(f"file '{j[2]}'\n" for j in jobs))
    out = os.path.join(WORK, "reel.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-i", os.path.join(WORK, "audio.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-shortest", out], check=True)
    print("->", out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--still":
        stills(sys.argv[2:])
    else:
        main(int(os.environ.get("GEO_WORKERS", "4")))
