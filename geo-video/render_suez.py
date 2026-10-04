# -*- coding: utf-8 -*-
"""スエズ運河の地理解説動画をレンダリング（timeline.json 駆動）。

usage:
  GEO_ASSETS=... GEO_WORK=... python render_suez.py              # 全編（4並列）→ suez.mp4
  GEO_ASSETS=... GEO_WORK=... python render_suez.py --still 12.5 40 95   # 指定秒の静止画QA
"""
import os, sys, json, math, subprocess, importlib.util
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geokit import *  # noqa

WORK = os.environ.get("GEO_WORK", os.path.join(os.getcwd(), "work"))
TL = json.load(open(os.path.join(WORK, "timeline.json")))
SBY = {s["key"]: s for s in TL["sents"]}
CUE = TL["cues"]
CHS = TL["chapters"]
spec = importlib.util.spec_from_file_location("script", os.path.join(os.path.dirname(__file__), "scripts", "suez.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def T(k):
    return SBY[k]["t0"]


def E(k):
    return SBY[k]["t1"]


def Q(n):
    return CUE[n]


def CH(i):
    return CHS[i]["t0"], CHS[i]["t1"]


# ---------------------------------------------------------------- static data
class Ctx:
    pass


def setup():
    X = Ctx()
    X.A = Assets()
    X.G = Globe(X.A)
    X.T = Terrain(X.A)
    X.suez = route_uv(S.ROUTE_SUEZ, 700)
    X.cape = route_uv(S.ROUTE_CAPE, 900)
    X.suez_ll = np.c_[unmerc(X.suez[:, 0], X.suez[:, 1])]
    cn = np.asarray(S.CANAL)
    X.canal = np.stack(merc(cn[:, 0], cn[:, 1]), 1)
    X.canal_km = km_along(cn)
    X.canal_z = X.T.height_at(X.canal[:, 0], X.canal[:, 1])
    # 断面図用：運河沿い と シナイ半島を東西に横切る線（北緯28.6度）
    X.prof_canal = X.canal_z
    lon = np.linspace(32.6, 34.4, 160)
    u, v = merc(lon, np.full_like(lon, 28.6))
    X.prof_sinai = X.T.height_at(u, v)
    X.ships = detect_ships(X.A.layer("queue_s2.png"))
    X.egypt = X.A.vec["countries"]["Egypt"]
    return X


def detect_ships(layer):
    """スエズ湾の待機船を衛星写真から検出（暗い海面上の小さな明るい点）"""
    from scipy import ndimage
    a = layer.arr[..., :3].astype(np.float32)
    lum = a.mean(2)
    water = (lum < 95) & (a[..., 2] + a[..., 1] > a[..., 0] * 1.6)
    water = ndimage.binary_opening(water, iterations=2)
    water = ndimage.binary_closing(water, iterations=4)
    water = ndimage.binary_erosion(water, iterations=14)        # 岸・港の構造物は除外
    bg = ndimage.median_filter(lum, 9)
    cand = (lum - bg > 22) & water
    lab, n = ndimage.label(cand)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        size = (lab[sl] == i).sum()
        if 2 <= size <= 40:
            cy = (sl[0].start + sl[0].stop) / 2
            cx = (sl[1].start + sl[1].stop) / 2
            out.append((layer.u0 + cx / layer.iw * (layer.u1 - layer.u0), layer.v0 + cy / layer.ih * (layer.v1 - layer.v0)))
    return np.array(out)


# ---------------------------------------------------------------- cameras
CAM_WORLD0 = Cam.at(60, 18, 15000)
CAM_ROUTE1 = Cam.at(52, 14, 15500, pitch=40, oy=40)
CAM_ROUTE2 = Cam.at(40, -6, 20500, pitch=38, bearing=-4, oy=-30)
CAM_ROUTE3 = Cam.at(54, 25, 14500, pitch=44, bearing=2, oy=70)
CAM_ROUTE4 = Cam.at(58, 25, 15500, pitch=46, bearing=5, oy=150)
CAM_SAT1 = Cam.at(32.85, 30.38, 560, pitch=0)
CAM_SAT2 = Cam.at(32.48, 30.55, 420, pitch=14)
CAM_SAT3 = Cam.at(32.40, 30.55, 330, pitch=40, bearing=-12, ox=-120)
CAM_SAT4 = Cam.at(32.30, 30.45, 300, pitch=46, bearing=-20, ox=-220)
CAM_TER1 = Cam.at(32.75, 29.85, 300, pitch=56, bearing=-22)
CAM_TER2 = Cam.at(33.65, 29.00, 300, pitch=55, bearing=-30)
CAM_TER3 = Cam.at(32.55, 30.15, 260, pitch=56, bearing=-18, ox=-160)
CAM_TER4 = Cam.at(32.38, 30.42, 150, pitch=52, bearing=-10)
CAM_DATA = Cam.at(32.40, 30.55, 230, pitch=50, bearing=4, ox=300)
CAM_EG0 = Cam.at(32.58, 30.02, 70, pitch=0)
CAM_EG1 = Cam.at(32.5800, 30.0176, 4.2, pitch=0)
CAM_EG2 = Cam.at(32.5800, 30.0176, 3.4, pitch=28, bearing=-10)
CAM_Q1 = Cam.at(32.56, 29.86, 30, pitch=34, bearing=-6)
CAM_Q2 = Cam.at(32.56, 29.86, 26, pitch=40, bearing=4, ox=-200)
CAM_END = Cam.at(50, 22, 15000, pitch=38, oy=30)


def end_cam_route(t):
    return cam_path(t, [(Q("panel"), CAM_ROUTE4), (CH(1)[1], CAM_ROUTE4)])


# ---------------------------------------------------------------- common overlays
def subtitles(c, t):
    for s in TL["sents"]:
        a = fade_window(t, s["t0"] - 0.08, s["t1"] + 0.3, 0.12, 0.18)
        if a > 0:
            subtitle(c, s["text"], a)


def chapter_headline(c, t, i, text, sub):
    t0, t1 = CH(i)
    headline(c, text, seg(t, t0 + 0.2, t0 + 1.2), sub=sub, out=seg(t, t1 - 0.6, t1 - 0.1))


def red_canal(c, cam, prog, t, z=None, width=8, head="dot"):
    zz = None if z is None else z
    x, y, d = cam.project(X.canal[:, 0], X.canal[:, 1], zz)
    return red_line(c, x, y, prog, width=width, t=t, head=head)


# ---------------------------------------------------------------- scenes
def sc_op(c, t):
    t0, t1 = CH(0)
    G = X.G
    m = seg(t, 9.0, t1)                         # 地球儀 → 平面へのモーフ
    zoom = ease_io(seg(t, 6.2, 9.4))
    lon0 = lerp(105, 60, ease_io(seg(t, 0, 6.5)))
    lat0 = lerp(14, 18, ease_io(seg(t, 0, 6.5)))
    S0 = W / CAM_WORLD0.span
    R_flat = S0 / (2 * math.pi * math.cos(math.radians(18)))
    R = lerp(lerp(400, 440, seg(t, 0, 6)), R_flat, zoom)
    G.space(c, t, 1 - m)
    if m > 0:
        draw_layer(c, CAM_WORLD0, X.A.layer("world_merc.jpg"), ease_io(seg(m, 0.7, 1.0)))
    G.draw(c, lon0, lat0, R, morph=m, cam=CAM_WORLD0)
    gx, gy, vis = G.line(X.suez_ll, lon0, lat0, R, morph=m, cam=CAM_WORLD0)
    prog = unyon(seg(t, 1.5, 5.6))
    n = int(len(gx) * prog)
    seen = vis[:n]
    if n > 2 and seen.any():
        idx = np.where(seen)[0]
        red_line(c, gx[idx], gy[idx], 1.0, width=7, t=t, head=None)
    # スエズの位置で脈打つ点
    sx, sy, sv = G.line(np.array([[32.4, 30.6]]), lon0, lat0, R, morph=m, cam=CAM_WORLD0)
    if sv[0]:
        pin(c, float(sx[0]), float(sy[0]), None, seg(t, 5.4, 6.0), t=t)
    title_card(c, S.TITLE, "世界の物流を左右する193km", seg(t, Q("title"), Q("title") + 1.1), out=seg(t, 9.0, 9.6))
    dim(c, 1 - seg(t, 0, 0.8))
    credit(c, "画像: NASA Blue Marble", seg(t, 1, 2) * (1 - seg(t, 9, 9.5)))


def sc_route(c, t):
    t0, t1 = CH(1)
    cam = cam_path(t, [(t0, CAM_WORLD0), (T("r1") + 3.2, CAM_ROUTE1), (Q("draw_cape") + 3.8, CAM_ROUTE2),
                       (Q("draw_suez") + 3.2, CAM_ROUTE3), (Q("panel") + 2.0, CAM_ROUTE4), (t1 + 3.0, CAM_ROUTE4)])
    c.clear(skia.Color(4, 8, 18))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    haze(c, cam, color=(120, 150, 190))
    # 喜望峰ルート：赤で伸びる → スエズ登場で灰色の破線へ
    gray = ease_io(seg(t, Q("draw_suez") - 0.3, Q("draw_suez") + 0.6))
    x, y = geo_line(cam, X.cape)
    pc = unyon(seg(t, Q("draw_cape"), Q("draw_cape") + 4.4))
    if gray < 1:
        red_line(c, x, y, pc, t=t, alpha=1 - gray)
    if gray > 0:
        red_line(c, x, y, pc, t=t, color=(200, 205, 214), alpha=gray * 0.85, glow=False, dashed=True, head=None,
                 width=7)
    x, y = geo_line(cam, X.suez)
    red_line(c, x, y, unyon(seg(t, Q("draw_suez"), Q("draw_suez") + 3.3)), t=t, width=10)
    for name, ll, key, side, up in [("上海", S.SHANGHAI, "pin_asia", -40, 90), ("ロッテルダム", S.ROTTERDAM, "pin_europe", 150, 50)]:
        px, py = cam.lonlat(*ll)
        pin(c, px, py, name, seg(t, Q(key), Q(key) + 0.5), size=38, t=t, side=side, up=up)
    px, py = cam.lonlat(*S.CAPE)
    pin(c, px, py, "喜望峰", seg(t, Q("pin_cape"), Q("pin_cape") + 0.5), size=38, t=t, up=70, side=-150,
        sub="アフリカ南端")
    px, py = cam.lonlat(32.4, 30.5)
    pin(c, px, py, "スエズ運河", seg(t, Q("draw_suez") + 1.6, Q("draw_suez") + 2.1), size=40, t=t, up=110, side=-90)
    # 距離差パネル（上部中央の横長カード：地図の主役を隠さない）
    k = seg(t, Q("panel"), Q("panel") + 1.0) * (1 - seg(t, t1 - 0.5, t1))
    if k > 0:
        a = clamp01(k * 3)
        x0, y0 = 660, 96 - (1 - ease_back(k, 1.2)) * 60
        rrect(c, x0, y0, 1190, 250, 20, (12, 16, 24), 0.88 * a)
        c.drawString("喜望峰まわりだと…", x0 + 40, y0 + 58, font(TF_BOLD, 34), skia.Paint(Color=C(GRAY, a), AntiAlias=True))
        counter(c, x0 + 40, y0 + 180, 6000, seg(t, Q("panel") + 0.3, Q("panel") + 1.8), prefix="+", unit="km",
                size=104, align="left", color=(255, 120, 110))
        c.drawString("距離（およそ）", x0 + 44, y0 + 228, font(TF_BOLD, 28), skia.Paint(Color=C(GRAY, a), AntiAlias=True))
        k2 = seg(t, T("r4") + 3.4, T("r4") + 4.2)
        if k2 > 0:
            stroke_text(c, "+1〜2週間", x0 + 640 + (1 - ease_back(k2)) * 40, y0 + 180, font(TF_BLACK, 96),
                        fill=YELLOW, sw=10, alpha=a * clamp01(k2 * 3))
            c.drawString("日数", x0 + 644, y0 + 228, font(TF_BOLD, 28), skia.Paint(Color=C(GRAY, a * k2), AntiAlias=True))
    chapter_headline(c, t, 1, "航路をくらべる", "アジア ⇄ ヨーロッパ")
    vignette(c, 0.5)
    credit(c, "画像: NASA Blue Marble")


def sat_cam(t):
    t0, t1 = CH(2)
    if t < T("z2"):
        return fly(CAM_ROUTE4, CAM_SAT1, ease_io(seg(t, t0 - 0.2, T("z2") - 0.1)))
    return cam_path(t, [(T("z2"), CAM_SAT1), (T("z3") + 0.2, CAM_SAT2), (T("z4"), CAM_SAT3), (t1 + 2.0, CAM_SAT4)])


def sc_sat(c, t):
    t0, t1 = CH(2)
    cam = sat_cam(t)
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",))
    haze(c, cam)
    # 海の名前
    x, y = cam.lonlat(32.62, 31.58)
    place(c, x, y, "地中海", seg(t, Q("label_med"), Q("label_med") + 0.8), size=64, italic_spacing=True)
    x, y = cam.lonlat(32.66, 29.62)
    red_k = seg(t, Q("label_red"), Q("label_red") + 0.8) * (1 - seg(t, T("z3") + 0.2, T("z3") + 0.8))
    place(c, x, y, "紅海", red_k, size=64, italic_spacing=True)
    if red_k > 0:
        stroke_text(c, "（スエズ湾）", x + 90, y - 6, font(TF_BOLD, 32), alpha=ease_o(red_k))
    # 運河を赤線でなぞる＋距離カウンター
    p = unyon(seg(t, Q("draw_canal"), Q("draw_canal") + 3.6))
    head = red_canal(c, cam, p, t, width=9)
    if head and p < 1:
        (hx, hy), _ = head
        stroke_text(c, f"{193 * p:,.0f} km", hx + 30, hy + 12, font(TF_BLACK, 46), fill=WHITE, sw=8)
    if p >= 1:
        x, y = cam.lonlat(32.47, 30.35)
        k = seg(t, Q("draw_canal") + 3.6, Q("draw_canal") + 4.2)
        stroke_text(c, "全長 193km", x - 70, y, font(TF_BLACK, 70), fill=WHITE, stroke=RED, sw=14, align="right",
                    alpha=ease_o(k) * (1 - seg(t, t1 - 0.5, t1)))
    for name, ll, key, up, side in [("ポートサイド", (32.30, 31.26), "draw_canal", 80, -150),
                                    ("スエズ", (32.55, 29.965), "draw_canal", 70, 170)]:
        x, y = cam.lonlat(*ll)
        off = 0 if name == "ポートサイド" else 3.4
        pin(c, x, y, name, seg(t, Q(key) + off, Q(key) + off + 0.5) * (1 - seg(t, t1 - 0.5, t1)), size=34,
            t=t, up=up, side=side)
    # 1869年 開通
    k = seg(t, Q("year"), Q("year") + 1.2) * (1 - seg(t, t1 - 0.6, t1))
    if k > 0:
        x0 = W - 600
        rrect(c, x0 + (1 - ease_back(k, 1.2)) * 60, 290, 500, 340, 20, (12, 16, 24), 0.86 * clamp01(k * 3))
        counter(c, x0 + 50, 500, 1869, seg(t, Q("year") + 0.2, Q("year") + 1.4), fmt="{:.0f}", unit="年",
                size=130, align="left", label=None, start=1800)
        c.drawString("開通", x0 + 52, 352, font(TF_BOLD, 40), skia.Paint(Color=C(YELLOW, clamp01(k * 3)), AntiAlias=True))
        k2 = seg(t, T("z4") + 2.6, T("z4") + 3.4)
        if k2 > 0:
            stroke_text(c, "150年以上 現役", x0 + 50, 590, font(TF_BLACK, 52), fill=WHITE, sw=8, alpha=clamp01(k2 * 2))
    chapter_headline(c, t, 2, "衛星写真で見る", "Sentinel-2 実写")
    vignette(c, 0.45)
    credit(c, "画像: Copernicus Sentinel-2 (2021) / NASA Blue Marble")


def ter_state(t):
    t0, t1 = CH(3)
    cam = cam_path(t, [(t0, CAM_SAT4), (T("t2"), CAM_TER1), (Q("pin_sinai") + 3.0, CAM_TER2),
                       (T("t4") + 2.5, CAM_TER3), (T("t5") + 3.0, CAM_TER4), (t1, CAM_TER4)])
    exag = 12 * ease_io(seg(t, Q("rise"), Q("rise") + 2.6))
    exag = lerp(exag, 6, ease_io(seg(t, T("t5"), T("t5") + 2)))
    return cam, exag


def sc_terrain(c, t, data=False):
    t0, t1 = CH(3)
    if not data:
        cam, exag = ter_state(t)
    else:
        cam = cam_path(t, [(CH(4)[0], CAM_TER4), (CH(4)[0] + 2.4, CAM_DATA), (CH(4)[1] + 2, CAM_DATA)])
        exag = lerp(6, 3, seg(t, CH(4)[0], CH(4)[0] + 2))
    c.clear(skia.Color(150, 172, 200))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    if exag < 0.05:
        auto_layers(c, cam, X.A, fine=("canal_s2.png",))
    else:
        X.T.draw(c, cam, exag=exag)
        # 立体化の直前は平面の高解像レイヤーをクロスフェード
        k = 1 - seg(t, Q("rise"), Q("rise") + 0.6) if not data else 0
        if k > 0:
            draw_layer(c, cam, X.A.layer("canal_s2.png"), k * smoothstep(300, 110, cam.mpp))
    haze(c, cam)
    zc = X.canal_z * exag + 30 * exag
    if not data:
        # 平地に沿う赤線
        p = 1.0 if t < T("t1") + 0.01 else 1.0
        x, y, _ = cam.project(X.canal[:, 0], X.canal[:, 1], zc)
        if t < Q("draw_low"):
            red_line(c, x, y, 1.0, width=7, t=t, head=None, alpha=0.9)
        else:
            red_line(c, x, y, unyon(seg(t, Q("draw_low"), Q("draw_low") + 2.4)), width=10, t=t)
        # シナイ山地
        sx, sy, _ = cam.project(*merc(33.95, 28.56), X.T.height_at(*merc(33.95, 28.56)) * exag)
        pin(c, float(sx), float(sy), "シナイ山地", seg(t, Q("pin_sinai"), Q("pin_sinai") + 0.6) * (1 - seg(t, T("t4") + 1.5, T("t4") + 2.0)),
            size=40, t=t, up=120, sub="標高 2,000m 超")
        # 断面図（運河沿い vs シナイ半島）
        profile_chart(c, W - 760, 440, 700, 380,
                      [("運河沿い", X.prof_canal, RED_HI), ("シナイ半島（東西断面）", X.prof_sinai, YELLOW)],
                      seg(t, T("t4"), T("t4") + 3.4), title="標高の断面図",
                      out=seg(t, T("t5") + 1.6, T("t5") + 2.1))
        # 湖
        for name, ll, dt, side in [("ティムサー湖", (32.29, 30.565), 0.0, -170), ("グレートビター湖", (32.39, 30.33), 0.8, 190)]:
            u, v = merc(*ll)
            lx, ly, _ = cam.project(u, v, 40 * exag)
            k = seg(t, Q("lakes") + dt, Q("lakes") + dt + 0.7) * (1 - seg(t, t1 - 0.4, t1))
            ring(c, float(lx), float(ly), 64 if dt else 40, k, width=6, t=t)
            pin(c, float(lx), float(ly) - (66 if dt else 42), name, k, size=34, t=t, up=60, side=side * 0.3)
        k = seg(t, T("t5") + 2.4, T("t5") + 3.2) * (1 - seg(t, t1 - 0.4, t1))
        if k > 0:
            stroke_text(c, "水門なしの", 1530, 380, font(TF_BLACK, 64), fill=WHITE, sw=10,
                        align="center", alpha=ease_o(k))
            stroke_text(c, "「水平式運河」", 1530, 480, font(TF_BLACK, 84), fill=WHITE, stroke=RED, sw=14,
                        align="center", alpha=ease_o(seg(t, T("t5") + 2.8, T("t5") + 3.6)))
        chapter_headline(c, t, 3, "地形で見る", "標高を約12倍に強調")
        credit(c, "標高: Mapzen Terrain Tiles (AWS) / 画像: Copernicus Sentinel-2")
    else:
        x, y, _ = cam.project(X.canal[:, 0], X.canal[:, 1], zc)
        red_line(c, x, y, 1.0, width=10 + 3 * math.sin(t * 5), t=t, head=None)
        t0d, t1d = CH(4)
        dim(c, 0.35 * fade_window(t, t0d, t1d, 0.6, 0.6))
        k = seg(t, Q("donut"), Q("donut") + 1.4) * (1 - seg(t, t1d - 0.4, t1d))
        donut(c, 520, 590, 185, 0.12, k)
        counter(c, 520, 632, 12, seg(t, Q("donut") + 0.2, Q("donut") + 1.6), fmt="{:.0f}", unit="%", size=124,
                color=WHITE, prefix="約")
        if k > 0:
            stroke_text(c, "世界の貿易量のうち", 520, 330, font(TF_BLACK, 50), align="center", alpha=clamp01(k * 3),
                        fill=YELLOW, sw=8)
        k2 = seg(t, T("d2"), T("d2") + 0.6) * (1 - seg(t, t1d - 0.4, t1d))
        if k2 > 0:
            x, y = cam.lonlat(32.33, 30.8)
            stroke_text(c, "この細い水路を通過", x + 60, y, font(TF_BLACK, 60), fill=WHITE, stroke=RED, sw=12,
                        alpha=ease_o(k2))
        chapter_headline(c, t, 4, "数字で見る", "世界の貿易と運河")
        credit(c, "標高: Mapzen Terrain Tiles (AWS) / 画像: Copernicus Sentinel-2")
    vignette(c, 0.45)


EG_LAYERS = ("canal_s2.png",)


def eg_cam(t):
    t0, t1 = CH(5)
    if t < T("e2"):
        c0 = cam_path(t, [(t0, CAM_DATA), (t0 + 0.01, CAM_DATA)])
        return c0.mix(CAM_EG0, ease_io(seg(t, t0, T("e2") - 0.2)))
    if t < T("e4"):
        return cam_path(t, [(T("e2") - 0.2, CAM_EG0), (Q("photo") + 2.4, CAM_EG1), (T("e3") + 3.0, CAM_EG2),
                            (T("e4"), CAM_EG2)])
    return cam_path(t, [(T("e4"), CAM_EG2), (T("e4") + 2.6, CAM_Q1), (T("e5") + 1.5, CAM_Q2), (t1 + 3, CAM_Q2)])


def sc_evergiven(c, t):
    t0, t1 = CH(5)
    cam = eg_cam(t)
    exag = lerp(3, 0, seg(t, t0, t0 + 1.6))
    c.clear(skia.Color(4, 8, 18))
    if exag > 0.05:
        draw_layer(c, cam, X.A.layer("world_merc.jpg"))
        X.T.draw(c, cam, exag=exag)
    else:
        mpp = cam.mpp
        extra = [("queue_s2.png", smoothstep(80, 35, mpp)), ("evergiven_s2.png", smoothstep(60, 22, mpp))]
        auto_layers(c, cam, X.A, fine=("canal_s2.png",), extra=extra)
    haze(c, cam)
    # 撮影のフラッシュ
    fl = seg(t, Q("photo"), Q("photo") + 0.5)
    if 0 < fl < 1:
        dim(c, (1 - fl) * 0.85, (255, 255, 255))
    photo_frame(c, seg(t, Q("photo"), Q("photo") + 0.6) * (1 - seg(t, T("e4") + 0.5, T("e4") + 1.0)),
                sub="2021年3月24日 撮影（座礁の翌日）")
    # エバーギブン号を赤丸で囲む
    u, v = merc(*S.EVER_GIVEN)
    x, y, _ = cam.project(u, v)
    kk = seg(t, Q("ring_ship"), Q("ring_ship") + 1.0) * (1 - seg(t, T("e4") + 0.3, T("e4") + 0.9))
    spotlight(c, float(x), float(y), 230, kk)
    ring(c, float(x), float(y), 120, kk, t=t, width=8)
    pin(c, float(x) + 85, float(y) - 85, "エバーギブン号", seg(t, Q("ring_ship") + 0.8, Q("ring_ship") + 1.3) * (1 - seg(t, T("e4") + 0.3, T("e4") + 0.9)),
        size=42, sub="全長 400m", up=110, side=170, t=t)
    # 待機船
    if len(X.ships):
        sx, sy, sz = cam.project(X.ships[:, 0], X.ships[:, 1])
        order = np.argsort(X.ships[:, 1])
        for j, i in enumerate(order):
            k = seg(t, Q("ships") + j * 0.05, Q("ships") + j * 0.05 + 0.4) * (1 - seg(t, t1 - 0.4, t1))
            if k > 0 and -20 < sx[i] < W + 20 and -20 < sy[i] < H + 20:
                q = (t * 1.3 + j * 0.37) % 1
                c.drawCircle(float(sx[i]), float(sy[i]), 9 + 14 * q, skia.Paint(Color=C(RED, 0.55 * (1 - q) * k), AntiAlias=True,
                                                                                 Style=skia.Paint.kStroke_Style, StrokeWidth=3))
                c.drawCircle(float(sx[i]), float(sy[i]), 7 * ease_back(k), skia.Paint(Color=C(RED, k), AntiAlias=True,
                                                                                      Style=skia.Paint.kStroke_Style, StrokeWidth=3.5))
    k = seg(t, Q("ships") + 1.2, Q("ships") + 1.8) * (1 - seg(t, T("e5") - 0.3, T("e5")))
    if k > 0:
        stroke_text(c, "待機する船", W / 2 + 330, 300, font(TF_BLACK, 64), fill=WHITE, stroke=RED, sw=12,
                    align="center", alpha=ease_o(k))
    # 6日間 / 400隻以上
    k = seg(t, Q("count"), Q("count") + 1.0) * (1 - seg(t, t1 - 0.5, t1))
    if k > 0:
        x0 = W - 620 + (1 - ease_back(k, 1.2)) * 80
        rrect(c, x0, 230, 540, 450, 20, (12, 16, 24), 0.88 * clamp01(k * 3))
        c.drawString("座礁から離礁まで", x0 + 40, 300, font(TF_BOLD, 34), skia.Paint(Color=C(GRAY, clamp01(k * 3)), AntiAlias=True))
        counter(c, x0 + 40, 420, 6, seg(t, Q("count") + 0.2, Q("count") + 1.0), fmt="{:.0f}", unit="日間", size=110,
                align="left")
        c.drawString("足止めされた船", x0 + 40, 500, font(TF_BOLD, 34), skia.Paint(Color=C(GRAY, clamp01(k * 3)), AntiAlias=True))
        counter(c, x0 + 40, 620, 400, seg(t, T("e5") + 2.6, T("e5") + 3.8), fmt="{:.0f}", unit="隻以上", size=110,
                align="left", color=(255, 120, 110))
    # 章カード
    chapter_card(c, "2021", "エバーギブン号の座礁", seg(t, t0 + 0.1, t0 + 1.0), out=seg(t, T("e2") - 1.2, T("e2") - 0.6))
    chapter_headline(c, t, 5, "2021年3月の事故", "座礁したコンテナ船")
    vignette(c, 0.45)
    credit(c, "画像: Copernicus Sentinel-2（2021年3月24日 08:41 UTC 撮影）", 1.0)


def sc_end(c, t):
    t0, t1 = CH(6)
    cam = fly(CAM_Q2, CAM_END, ease_io(seg(t, t0 - 0.3, T("m1") + 4.2)))
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",))
    haze(c, cam, color=(120, 150, 190))
    x, y = geo_line(cam, X.suez)
    red_line(c, x, y, unyon(seg(t, T("m1") + 1.8, T("m1") + 4.6)), t=t, width=10)
    px, py = cam.lonlat(32.4, 30.5)
    pin(c, px, py, "スエズ運河", seg(t, T("m1") + 3.6, T("m1") + 4.2), size=40, t=t, up=110, side=-90)
    k = seg(t, T("m2"), T("m2") + 1.0)
    if k > 0:
        dim(c, 0.45 * k)
        f = font(TF_BLACK, 84)
        stroke_text(c, "地図を見ると、", W / 2, H / 2 - 40, f, align="center", alpha=ease_o(k), sw=12)
        stroke_text(c, "世界のつながりが見えてくる。", W / 2, H / 2 + 80, f, fill=WHITE, stroke=RED, sw=14,
                    align="center", alpha=ease_o(seg(t, T("m2") + 0.5, T("m2") + 1.5)))
    vignette(c, 0.5)
    credit(c, "画像: NASA Blue Marble / Copernicus Sentinel-2", 1 - seg(t, T("m2"), T("m2") + 0.5))
    # 出典（エンドクレジット）
    k = seg(t, T("m2") + 1.2, T("m2") + 1.8)
    if k > 0:
        f = font(TF_MED, 24)
        lines = ["出典: NASA Blue Marble / NASA Earth at Night",
                 "Contains modified Copernicus Sentinel data 2021（Sentinel-2, AWS Open Data）",
                 "標高: Mapzen Terrain Tiles（AWS Open Data） / 国境: Natural Earth"]
        for i, ln in enumerate(lines):
            c.drawString(ln, 60, 70 + i * 34, f, skia.Paint(Color=C(WHITE, 0.85 * k), AntiAlias=True))
    dim(c, seg(t, TL["total"] - 1.6, TL["total"] - 0.2))


def render(c, t):
    if t < CH(1)[0]:
        sc_op(c, t)
    elif t < CH(2)[0]:
        sc_route(c, t)
    elif t < CH(3)[0]:
        if t < T("z2"):
            sc_route_to_sat(c, t)
        else:
            sc_sat(c, t)
    elif t < CH(4)[0]:
        sc_terrain(c, t)
    elif t < CH(5)[0]:
        sc_terrain(c, t, data=True)
    elif t < CH(6)[0]:
        sc_evergiven(c, t)
    else:
        sc_end(c, t)
    subtitles(c, t)


def sc_route_to_sat(c, t):
    """世界地図から衛星写真へダイブ（ルート線を残したまま）"""
    cam = sat_cam(t)
    c.clear(skia.Color(4, 8, 18))
    auto_layers(c, cam, X.A, fine=("canal_s2.png",))
    haze(c, cam)
    x, y = geo_line(cam, X.suez)
    red_line(c, x, y, 1.0, t=t, width=10, head=None, alpha=1 - seg(t, CH(2)[0] + 1.2, T("z2") - 0.3))
    chapter_headline(c, t, 2, "衛星写真で見る", "Sentinel-2 実写")
    vignette(c, 0.45)
    credit(c, "画像: Copernicus Sentinel-2 (2021) / NASA Blue Marble")


# ---------------------------------------------------------------- drivers
X = None


def init():
    global X
    if X is None:
        X = setup()


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
    k = max(1, workers * 3)
    bounds = np.linspace(0, n, k + 1).astype(int)
    jobs = [(int(bounds[i]), int(bounds[i + 1]), os.path.join(seg_dir, f"seg_{i:03d}.mp4")) for i in range(k)]
    with mp.get_context("fork").Pool(workers) as pool:
        for i, out in enumerate(pool.imap(render_range, jobs)):
            print(f"  segment {i + 1}/{len(jobs)} done", flush=True)
    lst = os.path.join(seg_dir, "list.txt")
    open(lst, "w").write("".join(f"file '{j[2]}'\n" for j in jobs))
    out = os.path.join(WORK, "suez.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-i", os.path.join(WORK, "audio.wav"), "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-shortest", out], check=True)
    print("->", out)


def thumbnail():
    """サムネイル：地形3D＋赤線＋大きなコピー"""
    init()
    from PIL import Image
    s = new_surface()
    c = s.getCanvas()
    cam = Cam.at(32.62, 30.05, 280, pitch=56, bearing=-20, ox=330)
    c.clear(skia.Color(150, 172, 200))
    draw_layer(c, cam, X.A.layer("world_merc.jpg"))
    X.T.draw(c, cam, exag=10)
    haze(c, cam)
    x, y, _ = cam.project(X.canal[:, 0], X.canal[:, 1], X.canal_z * 10 + 300)
    red_line(c, x, y, 1.0, width=16, head=None)
    vignette(c, 0.6)
    sh = skia.GradientShader.MakeLinear([skia.Point(0, 0), skia.Point(W * 0.62, 0)],
                                        [C((0, 0, 0), 0.75), C((0, 0, 0), 0)], [0, 1])
    c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Shader=sh))
    stroke_text(c, "たった193kmが", 80, 330, font(TF_BLACK, 120), sw=18)
    stroke_text(c, "止まると", 80, 500, font(TF_BLACK, 150), sw=20)
    stroke_text(c, "世界が止まる", 80, 690, font(TF_BLACK, 170), fill=WHITE, stroke=RED, sw=26)
    f = font(TF_BLACK, 58)
    rrect(c, 80, 760, f.measureText("スエズ運河を衛星写真で見る") + 64, 110, 12, YELLOW, 1.0)
    c.drawString("スエズ運河を衛星写真で見る", 112, 838, f, skia.Paint(Color=C(INK), AntiAlias=True))
    p = os.path.join(WORK, "thumbnail.jpg")
    Image.fromarray(s.makeImageSnapshot().toarray()[..., :3]).save(p, quality=92)
    print(p)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--thumb":
        thumbnail()
    elif len(sys.argv) > 1 and sys.argv[1] == "--still":
        stills(sys.argv[2:])
    else:
        main(int(os.environ.get("GEO_WORKERS", "4")))
