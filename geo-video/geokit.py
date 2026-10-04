# -*- coding: utf-8 -*-
"""geokit — 地理解説動画の「図解パターン」ライブラリ（skia-python によるCPU描画）。

座標はすべて Web メルカトル正規化 (u,v ∈ [0,1])。カメラ(Cam)が地図平面を3D投影し、
衛星写真レイヤー・地形メッシュ・赤線・ピン・テロップを同じカメラで重ねる。

パターン一覧（README.md に図解あり）
  P1  globe()            地球儀スピン → 平面地図へモーフィング
  P2  Cam(pitch=…)       平面地図が奥へ倒れて立体化（チルト/回り込み）
  P3  red_line()         赤線「うにょーん」：伸びる線＋先端マーカー＋グロー
  P4  route_compare      2ルート比較（遠回りを灰色破線化→近道を赤で）
  P5  auto_layers()      衛星写真ダイブズーム（Blue Marble → Sentinel-2 10m）
  P6  Terrain            地形の隆起3D（標高メッシュ＋衛星写真ドレープ）
  P7  pin()/place()      地名ピンのポップ表示（引き出し線つき）
  P8  ring()/spotlight() 赤丸で注目＋周囲を暗転
  P9  area()             国・地域の塗り＋輪郭なぞり
  P10 counter()/donut()  数字カウンター・円グラフ
  P11 headline()/subtitle()/chapter_card() テロップ類
  P12 photo_card()       実写写真カードが3Dでパタンと倒れて出る
"""
import os, math, json
import numpy as np
import skia
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
W, H = 1920, 1080
FPS = 30
ASSETS = os.environ.get("GEO_ASSETS", os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"))
R_EARTH = 6378137.0

# ------------------------------------------------------------------ fonts
_FONT_DIRS = [os.environ.get("GEO_FONT_DIR", ""), "/usr/share/fonts/opentype/noto",
              "/usr/share/fonts/noto-cjk", "C:/Windows/Fonts"]


def _typeface(*names):
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if d and os.path.exists(p):
                return skia.Typeface.MakeFromFile(p, 0)
    raise FileNotFoundError(f"font not found: {names}")


TF_BLACK = _typeface("NotoSansCJK-Black.ttc", "BIZ-UDGothicB.ttc")
TF_BOLD = _typeface("NotoSansCJK-Bold.ttc", "BIZ-UDGothicB.ttc")
TF_MED = _typeface("NotoSansCJK-Medium.ttc", "meiryo.ttc")

# ------------------------------------------------------------------ palette
RED = (232, 36, 36)
RED_HI = (255, 92, 80)
WHITE = (255, 255, 255)
INK = (20, 22, 28)
GRAY = (190, 196, 205)
YELLOW = (255, 214, 64)


def C(rgb, a=1.0):
    return skia.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(max(0, min(1, a)) * 255))


# ------------------------------------------------------------------ easing / timing
def clamp01(x):
    return max(0.0, min(1.0, x))


def seg(t, t0, t1):
    """t0→t1 の進行度 0..1"""
    return clamp01((t - t0) / max(t1 - t0, 1e-6))


def ease_io(x):
    x = clamp01(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_o(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_back(x, s=1.9):
    x = clamp01(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


def ease_elastic(x):
    x = clamp01(x)
    if x in (0, 1):
        return x
    return 2 ** (-10 * x) * math.sin((x * 10 - 0.75) * (2 * math.pi / 3)) + 1


def unyon(x):
    """赤線用イージング：ゆっくり出て、ぐいっと伸び、最後にわずかに行き過ぎて戻る"""
    x = clamp01(x)
    e = ease_io(x)
    return min(1.0, e + 0.035 * math.sin(math.pi * x) * (1 - x) * 4) if x < 1 else 1.0


def lerp(a, b, x):
    return a + (b - a) * x


def fade_window(t, t0, t1, fi=0.35, fo=0.35):
    """t0..t1 の間だけ見える（フェードイン/アウト付き）"""
    return min(seg(t, t0, t0 + fi), 1 - seg(t, t1 - fo, t1))


# ------------------------------------------------------------------ geo
def merc(lon, lat):
    lon = np.asarray(lon, np.float64)
    lat = np.clip(np.asarray(lat, np.float64), -85.05, 85.05)
    u = (lon + 180.0) / 360.0
    v = 0.5 - np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) / (2 * np.pi)
    return u, v


def unmerc(u, v):
    lon = np.asarray(u) * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * np.asarray(v)))))
    return lon, lat


def catmull(pts, n=400):
    """制御点を滑らかな曲線に（航路などの手描き風カーブ）"""
    p = np.asarray(pts, np.float64)
    if len(p) < 3:
        t = np.linspace(0, 1, n)[:, None]
        return p[0] * (1 - t) + p[-1] * t
    p = np.vstack([p[0] * 2 - p[1], p, p[-1] * 2 - p[-2]])
    d = np.sqrt(((p[1:] - p[:-1]) ** 2).sum(1)) ** 0.5 + 1e-9  # centripetal
    out = []
    per = max(4, n // (len(p) - 3))
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        t0, t1 = 0, d[i - 1]
        t2, t3 = t1 + d[i], t1 + d[i] + d[i + 1]
        for t in np.linspace(t1, t2, per, endpoint=False):
            a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    out.append(p[-2])
    return np.array(out)


def route_uv(lonlat, n=500, smooth=True):
    """[(lon,lat),...] → メルカトル上の滑らかな線 (N,2)"""
    a = np.asarray(lonlat, np.float64)
    u, v = merc(a[:, 0], a[:, 1])
    uv = np.stack([u, v], 1)
    return catmull(uv, n) if smooth else uv


def km_along(lonlat):
    a = np.radians(np.asarray(lonlat, np.float64))
    dlat = a[1:, 1] - a[:-1, 1]
    dlon = a[1:, 0] - a[:-1, 0]
    h = np.sin(dlat / 2) ** 2 + np.cos(a[:-1, 1]) * np.cos(a[1:, 1]) * np.sin(dlon / 2) ** 2
    return np.concatenate([[0], np.cumsum(2 * 6371.0 * np.arcsin(np.sqrt(h)))])


# ------------------------------------------------------------------ camera
class Cam:
    """地図カメラ。cu,cv=注視点(メルカトル), span=画面幅に入るメルカトル幅,
    pitch=倒し角(度,0で真上から), bearing=方位回転(度), ox,oy=注視点の画面オフセット(px)"""

    def __init__(self, cu, cv, span, pitch=0.0, bearing=0.0, fov=38.0, ox=0.0, oy=0.0):
        self.cu, self.cv, self.span = cu, cv, span
        self.pitch, self.bearing, self.fov, self.ox, self.oy = pitch, bearing, fov, ox, oy
        self._setup()

    @classmethod
    def at(cls, lon, lat, span_km, **kw):
        u, v = merc(lon, lat)
        span = span_km * 1000 / (2 * math.pi * R_EARTH * math.cos(math.radians(lat)))
        return cls(float(u), float(v), span, **kw)

    def _setup(self):
        self.S = W / self.span
        self.D = (H / 2) / math.tan(math.radians(self.fov) / 2)
        p, b = math.radians(self.pitch), math.radians(self.bearing)
        cb, sb = math.cos(b), math.sin(b)
        cam = np.array([0.0, self.D * math.sin(p), self.D * math.cos(p)])
        self.C = np.array([cb * cam[0] - sb * cam[1], sb * cam[0] + cb * cam[1], cam[2]])
        self.f = -self.C / np.linalg.norm(self.C)
        self.r = np.array([cb, sb, 0.0])
        self.d = np.cross(self.r, self.f)
        self.cx, self.cy = W / 2 + self.ox, H / 2 + self.oy
        r, d, f, C, D = self.r, self.d, self.f, self.C, self.D
        Mw = np.array([
            [D * r[0] + self.cx * f[0], D * r[1] + self.cx * f[1], -(D * r @ C + self.cx * f @ C)],
            [D * d[0] + self.cy * f[0], D * d[1] + self.cy * f[1], -(D * d @ C + self.cy * f @ C)],
            [f[0], f[1], -(f @ C)]])
        T = np.array([[self.S, 0, -self.cu * self.S], [0, self.S, -self.cv * self.S], [0, 0, 1.0]])
        self.Huv = Mw @ T
        # 地上距離あたりの px（注視点の緯度）
        lat = float(unmerc(self.cu, self.cv)[1])
        self.lat = lat
        self.m_per_unit = 2 * math.pi * R_EARTH * math.cos(math.radians(lat))
        self.mpp = self.span * self.m_per_unit / W   # m / px（画面中央, 真上視点）

    def project(self, u, v, z_m=None):
        """(u,v[,標高m]) → 画面 x,y と深度。z_m は誇張済みの標高(m)"""
        u = np.asarray(u, np.float64)
        v = np.asarray(v, np.float64)
        X = (u - self.cu) * self.S
        Y = (v - self.cv) * self.S
        Z = np.zeros_like(X) if z_m is None else np.asarray(z_m, np.float64) * self.S / self.m_per_unit
        rx, ry, rz = X - self.C[0], Y - self.C[1], Z - self.C[2]
        zc = rx * self.f[0] + ry * self.f[1] + rz * self.f[2]
        xc = rx * self.r[0] + ry * self.r[1] + rz * self.r[2]
        yc = rx * self.d[0] + ry * self.d[1] + rz * self.d[2]
        zs = np.maximum(zc, 1e-3)
        return self.cx + self.D * xc / zs, self.cy + self.D * yc / zs, zc

    def lonlat(self, lon, lat, z_m=None):
        u, v = merc(lon, lat)
        x, y, z = self.project(u, v, z_m)
        return float(x), float(y)

    def matrix(self, u0, v0, u1, v1, iw, ih):
        """画像(iw×ih px, 範囲u0..u1,v0..v1)を画面へ写す skia.Matrix"""
        A = np.array([[(u1 - u0) / iw, 0, u0], [0, (v1 - v0) / ih, v0], [0, 0, 1.0]])
        Hm = self.Huv @ A
        Hm = Hm / Hm[2, 2]
        return skia.Matrix.MakeAll(*[float(x) for x in Hm.ravel()])

    def mix(self, other, x):
        """カメラ補間（ズームは対数で）"""
        x = clamp01(x)
        ls = lerp(math.log(self.span), math.log(other.span), x)
        span = math.exp(ls)
        # 大きなズームでは中心の移動をズーム量に比例させる（寄っている間は目標を画面内に保つ）
        if max(self.span, other.span) / min(self.span, other.span) > 3:
            w = (span - self.span) / (other.span - self.span)
        else:
            w = x
        return Cam(lerp(self.cu, other.cu, w), lerp(self.cv, other.cv, w), span,
                   lerp(self.pitch, other.pitch, x), lerp(self.bearing, other.bearing, x),
                   lerp(self.fov, other.fov, x), lerp(self.ox, other.ox, x), lerp(self.oy, other.oy, x))


def cam_path(t, keys, ease=ease_io):
    """keys=[(time, Cam), ...] を時刻 t で補間"""
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, c0), (t1, c1) in zip(keys, keys[1:]):
        if t <= t1:
            return c0.mix(c1, ease(seg(t, t0, t1)))
    return keys[-1][1]


def fly(c0, c1, x):
    """ズームアウト→移動→ズームインの弧を描くフライト（遠距離移動用）"""
    x = clamp01(x)
    base = c0.mix(c1, x)
    du = math.hypot(c1.cu - c0.cu, c1.cv - c0.cv)
    bump = max(0.0, du * 1.6 - max(c0.span, c1.span) * 0.5)
    if bump > 0:
        base = Cam(base.cu, base.cv, base.span + bump * math.sin(math.pi * x), base.pitch,
                   base.bearing, base.fov, base.ox, base.oy)
    return base


# ------------------------------------------------------------------ assets
class Layer:
    def __init__(self, path, meta, alpha=False):
        a = np.asarray(Image.open(path).convert("RGBA"))
        self.iw, self.ih = a.shape[1], a.shape[0]
        self.img = skia.Image.fromarray(np.ascontiguousarray(a), colorType=skia.kRGBA_8888_ColorType,
                                        alphaType=skia.kUnpremul_AlphaType).withDefaultMipmaps()
        self.u0, self.u1, self.v0, self.v1 = meta["u0"], meta["u1"], meta["v0"], meta["v1"]
        self.res_m = meta.get("res_m", 0)
        self.arr = a

    def contains(self, u, v):
        return self.u0 <= u <= self.u1 and self.v0 <= v <= self.v1


SAMPLING = skia.SamplingOptions(skia.FilterMode.kLinear, skia.MipmapMode.kLinear)


class Assets:
    def __init__(self, root=ASSETS):
        self.root = root
        self.meta = json.load(open(os.path.join(root, "layers.json")))
        self.meta["world_merc.jpg"] = {"u0": 0, "u1": 1, "v0": 0, "v1": 1}
        self.meta["night_merc.jpg"] = {"u0": 0, "u1": 1, "v0": 0, "v1": 1}
        self._layers = {}
        self.vec = json.load(open(os.path.join(root, "vectors.json")))

    def layer(self, name):
        if name not in self._layers:
            self._layers[name] = Layer(os.path.join(self.root, name), self.meta[name])
        return self._layers[name]


def draw_layer(c, cam, layer, alpha=1.0, blend=None):
    if alpha <= 0.002:
        return
    # 全球レイヤーは東西につなげて描く（日付変更線・広域ズームアウト対策）
    offs = (-1, 0, 1) if (layer.u0 == 0 and layer.u1 == 1 and cam.span > 0.25) else (0,)
    paint = skia.Paint(Alphaf=float(alpha), AntiAlias=True)
    if blend is not None:
        paint.setBlendMode(blend)
    for o in offs:
        c.save()
        c.concat(cam.matrix(layer.u0 + o, layer.v0, layer.u1 + o, layer.v1, layer.iw, layer.ih))
        c.drawImage(layer.img, 0, 0, SAMPLING, paint)
        c.restore()


def smoothstep(e0, e1, x):
    t = clamp01((x - e0) / (e1 - e0))
    return t * t * (3 - 2 * t)


def auto_layers(c, cam, assets, fine=("canal_s2.jpg",), extra=()):
    """P5 衛星写真ダイブズーム：画面の縮尺(m/px)に応じて解像度の違う実写レイヤーを自動で重ねる"""
    draw_layer(c, cam, assets.layer("world_merc.jpg"))
    mpp = cam.mpp
    draw_layer(c, cam, assets.layer("region_s2.png"), smoothstep(2600, 900, mpp))
    for name in fine:
        draw_layer(c, cam, assets.layer(name), smoothstep(300, 110, mpp))
    for name, a in extra:
        draw_layer(c, cam, assets.layer(name), a)


# ------------------------------------------------------------------ canvas helpers
def new_surface():
    return skia.Surface.MakeRaster(skia.ImageInfo.Make(W, H, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType))


def path_from(xs, ys, close=False):
    p = skia.Path()
    if len(xs) == 0:
        return p
    p.addPoly(list(map(skia.Point, np.asarray(xs, np.float32).tolist(), np.asarray(ys, np.float32).tolist())), close)
    return p


def dim(c, a, rgb=(0, 0, 0)):
    if a > 0:
        c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Color=C(rgb, a)))


_VIGNETTE = None


def vignette(c, strength=0.55):
    global _VIGNETTE
    if _VIGNETTE is None:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.70)) ** 2)
        a = np.clip((r - 0.55) / 0.6, 0, 1) ** 1.6
        arr = np.zeros((H, W, 4), np.uint8)
        arr[..., 3] = (a * 255).astype(np.uint8)
        _VIGNETTE = skia.Image.fromarray(arr, colorType=skia.kRGBA_8888_ColorType)
    c.drawImage(_VIGNETTE, 0, 0, skia.SamplingOptions(), skia.Paint(Alphaf=strength))


def haze(c, cam, color=(170, 190, 215), strength=1.0):
    """チルト時の遠景かすみ（空気遠近法）"""
    k = clamp01(cam.pitch / 60.0) * strength
    if k <= 0.01:
        return
    sh = skia.GradientShader.MakeLinear(
        [skia.Point(0, 0), skia.Point(0, H * 0.55)],
        [C(color, 0.85 * k), C(color, 0.25 * k), C(color, 0)], [0, 0.45, 1])
    c.drawRect(skia.Rect(0, 0, W, H * 0.55), skia.Paint(Shader=sh))


# ------------------------------------------------------------------ text
def font(tf, size):
    f = skia.Font(tf, size)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    f.setSubpixel(True)
    return f


def text_w(text, f):
    return f.measureText(text)


def stroke_text(c, text, x, y, f, fill=WHITE, stroke=INK, sw=None, align="left", alpha=1.0, shadow=True):
    """縁取り文字（地図上の地名・テロップの基本形）"""
    w = f.measureText(text)
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    sw = f.getSize() * 0.16 if sw is None else sw
    if shadow:
        c.drawString(text, x + 2, y + 4, f, skia.Paint(Color=C((0, 0, 0), 0.45 * alpha), AntiAlias=True,
                                                         MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 5)))
    if sw > 0:
        c.drawString(text, x, y, f, skia.Paint(Color=C(stroke, alpha), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                                               StrokeWidth=sw, StrokeJoin=skia.Paint.kRound_Join))
    c.drawString(text, x, y, f, skia.Paint(Color=C(fill, alpha), AntiAlias=True))
    return w


def wrap_ja(text, f, maxw):
    """日本語の折り返し。2行になる時は読点の位置で左右バランスよく割る（行頭禁則つき）"""
    if f.measureText(text) > maxw and f.measureText(text) < maxw * 1.95:
        best = None
        for i, ch in enumerate(text[:-1]):
            if ch in "、。":
                a, b = text[:i + 1], text[i + 1:]
                if max(f.measureText(a), f.measureText(b)) <= maxw:
                    score = abs(f.measureText(a) - f.measureText(b))
                    if best is None or score < best[0]:
                        best = (score, [a, b])
        if best:
            return best[1]
        half = f.measureText(text) / 2
        for i in range(1, len(text)):
            if f.measureText(text[:i]) >= half and text[i] not in "、。」』）ーっゃゅょ":
                return [text[:i], text[i:]]
    return _wrap_greedy(text, f, maxw)


def _wrap_greedy(text, f, maxw):
    lines, cur = [], ""
    for ch in text:
        if f.measureText(cur + ch) > maxw and cur:
            if ch in "、。」』）!?！？ー":
                cur += ch
                lines.append(cur)
                cur = ""
                continue
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def rrect(c, x, y, w, h, r, color, alpha=1.0, shadow=True):
    rect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), r, r)
    if shadow:
        c.drawRRect(rect.makeOffset(0, 6), skia.Paint(Color=C((0, 0, 0), 0.35 * alpha), AntiAlias=True,
                                                      MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 10)))
    c.drawRRect(rect, skia.Paint(Color=C(color, alpha), AntiAlias=True))


# ------------------------------------------------------------------ P3: red line
def red_line(c, xs, ys, prog, width=9.0, color=RED, alpha=1.0, head="dot", glow=True,
             dashed=False, t=0.0, outline=True):
    """P3 赤線うにょーん。xs,ys=画面座標の折れ線, prog=0..1 の伸び具合"""
    if prog <= 0 or alpha <= 0 or len(xs) < 2:
        return None
    path = path_from(xs, ys)
    pm = skia.PathMeasure(path, False)
    L = pm.getLength()
    if L <= 0:
        return None
    d = L * clamp01(prog)
    sub = skia.Path()
    pm.getSegment(0, d, sub, True)
    cap, join = skia.Paint.kRound_Cap, skia.Paint.kRound_Join
    pe = skia.DashPathEffect.Make([width * 2.2, width * 1.6], -t * 60) if dashed else None

    def P(rgb, a, w, blur=0):
        p = skia.Paint(Color=C(rgb, a * alpha), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                       StrokeWidth=w, StrokeCap=cap, StrokeJoin=join)
        if blur:
            p.setMaskFilter(skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, blur))
        if pe is not None and not blur:
            p.setPathEffect(pe)
        return p

    c.save()
    c.translate(0, 3)
    c.drawPath(sub, P((0, 0, 0), 0.40, width + 4, 4))       # 影
    c.restore()
    if glow:
        c.drawPath(sub, P(color, 0.55, width * 2.8, width * 1.1))   # グロー
    if outline:
        c.drawPath(sub, P(WHITE, 0.95, width + 5))           # 白フチ
    c.drawPath(sub, P(color, 1.0, width))                    # 本体
    c.drawPath(sub, P(RED_HI if color == RED else WHITE, 0.55, width * 0.3))   # ハイライト
    pos, tan = pm.getPosTan(d)
    if head and prog < 1.0 or head == "arrow":
        if head == "arrow":
            arrow(c, pos.x(), pos.y(), tan.x(), tan.y(), width * 3.0, color, alpha)
        else:
            r = width * 1.25
            pulse = 1 + 0.25 * math.sin(t * 12)
            c.drawCircle(pos.x(), pos.y(), r * 2.4 * pulse, skia.Paint(Color=C(color, 0.30 * alpha), AntiAlias=True,
                                                                       MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, 6)))
            c.drawCircle(pos.x(), pos.y(), r + 3, skia.Paint(Color=C(WHITE, alpha), AntiAlias=True))
            c.drawCircle(pos.x(), pos.y(), r, skia.Paint(Color=C(color, alpha), AntiAlias=True))
    return (pos.x(), pos.y()), d / L


def arrow(c, x, y, tx, ty, size, color=RED, alpha=1.0):
    n = math.hypot(tx, ty) or 1
    tx, ty = tx / n, ty / n
    nx, ny = -ty, tx
    p = skia.Path()
    p.moveTo(x + tx * size * 0.6, y + ty * size * 0.6)
    p.lineTo(x - tx * size * 0.6 + nx * size * 0.55, y - ty * size * 0.6 + ny * size * 0.55)
    p.lineTo(x - tx * size * 0.3, y - ty * size * 0.3)
    p.lineTo(x - tx * size * 0.6 - nx * size * 0.55, y - ty * size * 0.6 - ny * size * 0.55)
    p.close()
    c.drawPath(p, skia.Paint(Color=C(WHITE, alpha), AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeWidth=5,
                             StrokeJoin=skia.Paint.kRound_Join))
    c.drawPath(p, skia.Paint(Color=C(color, alpha), AntiAlias=True))


def geo_line(cam, uv, z_m=None):
    x, y, z = cam.project(uv[:, 0], uv[:, 1], z_m)
    ok = z > 1
    return x[ok], y[ok]


# ------------------------------------------------------------------ P7: pins / labels
def pin(c, x, y, label, k, size=40, sub=None, color=RED, up=90, side=0, t=0.0, box=True):
    """P7 地名ピン。k=0..1 の出現アニメ（ポップ）。up=引き出し線の長さ, side=横ずらし"""
    if k <= 0:
        return
    s = ease_back(k)
    a = clamp01(k * 3)
    # 着地点の波紋
    ring_k = (t * 0.8) % 1.0
    c.drawCircle(x, y, 10 + 26 * ring_k, skia.Paint(Color=C(color, 0.5 * (1 - ring_k) * a), AntiAlias=True,
                                                     Style=skia.Paint.kStroke_Style, StrokeWidth=3))
    c.drawCircle(x, y, 11 * s, skia.Paint(Color=C(WHITE, a), AntiAlias=True))
    c.drawCircle(x, y, 7.5 * s, skia.Paint(Color=C(color, a), AntiAlias=True))
    if not label:
        return
    ty = y - up * ease_o(k)
    tx = x + side * ease_o(k)
    # ラベルが画面外に出ないように寄せる
    ty = min(max(ty, size * 1.4 + (size * 0.75 if sub else 0) + 16), H - 40)
    tx = min(max(tx, size * 2.2), W - size * 2.2)
    c.drawLine(x, y - 10, tx, ty + 6, skia.Paint(Color=C(WHITE, a), AntiAlias=True, StrokeWidth=3))
    f = font(TF_BLACK, size)
    w = f.measureText(label)
    if box:
        pad = size * 0.38
        bh = size * 1.32 + (size * 0.75 if sub else 0)
        bw = max(w, font(TF_BOLD, size * 0.55).measureText(sub) if sub else 0) + pad * 2
        c.save()
        c.translate(tx, ty)
        c.scale(s, s)
        rrect(c, -bw / 2, -bh, bw, bh, size * 0.22, WHITE, a)
        c.drawRect(skia.Rect.MakeXYWH(-bw / 2, -bh, size * 0.16, bh), skia.Paint(Color=C(color, a)))
        c.drawString(label, -w / 2 + size * 0.05, -bh + size * 1.05, f, skia.Paint(Color=C(INK, a), AntiAlias=True))
        if sub:
            fs = font(TF_BOLD, size * 0.55)
            c.drawString(sub, -fs.measureText(sub) / 2, -size * 0.32, fs, skia.Paint(Color=C(color, a), AntiAlias=True))
        c.restore()
    else:
        stroke_text(c, label, tx, ty, f, align="center", alpha=a)


def place(c, x, y, label, k, size=44, color=WHITE, stroke=INK, italic_spacing=False):
    """地図に直接書く大きな地名（海・半島など）。字間を広げてフェードイン"""
    if k <= 0:
        return
    a = ease_o(k)
    f = font(TF_BLACK, size)
    sp = size * 0.18 * (1 + 0.6 * (1 - a)) if italic_spacing else 0
    if sp:
        widths = [f.measureText(ch) for ch in label]
        total = sum(widths) + sp * (len(label) - 1)
        cx = x - total / 2
        for ch, w in zip(label, widths):
            stroke_text(c, ch, cx, y, f, fill=color, stroke=stroke, alpha=a)
            cx += w + sp
    else:
        stroke_text(c, label, x, y, f, fill=color, stroke=stroke, align="center", alpha=a)


# ------------------------------------------------------------------ P8: focus
def ring(c, x, y, r, k, width=8, color=RED, t=0.0, pulse=True):
    """P8 赤丸：ぐるっと描かれて囲む（うにょーん円）"""
    if k <= 0:
        return
    p = skia.Path()
    p.addOval(skia.Rect.MakeXYWH(x - r, y - r, 2 * r, 2 * r), skia.PathDirection.kCW, 3)
    pm = skia.PathMeasure(p, False)
    L = pm.getLength()
    sub = skia.Path()
    pm.getSegment(0, L * min(1, ease_io(k) * 1.04), sub, True)
    st = dict(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeCap=skia.Paint.kRound_Cap)
    c.drawPath(sub, skia.Paint(Color=C(color, 0.55), StrokeWidth=width * 2.6,
                               MaskFilter=skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, width), **st))
    c.drawPath(sub, skia.Paint(Color=C(WHITE, 0.95), StrokeWidth=width + 5, **st))
    c.drawPath(sub, skia.Paint(Color=C(color, 1), StrokeWidth=width, **st))
    if pulse and k >= 1:
        q = (t * 0.9) % 1
        c.drawCircle(x, y, r * (1 + 0.35 * q), skia.Paint(Color=C(color, 0.6 * (1 - q)), StrokeWidth=4, **st))


def spotlight(c, x, y, r, a):
    """P8 周囲を暗くして一点に視線を集める"""
    if a <= 0:
        return
    sh = skia.GradientShader.MakeRadial(skia.Point(x, y), r * 1.6, [C((0, 0, 0), 0), C((0, 0, 0), 0.62 * a)],
                                        [0.55, 1.0])
    p = skia.Paint(Shader=sh)
    c.drawRect(skia.Rect(0, 0, W, H), p)


# ------------------------------------------------------------------ P9: area
def area(c, cam, polys, k, color=RED, fill_a=0.28, width=4, t=0.0):
    """P9 国・地域ハイライト：輪郭をなぞってから内側を塗る"""
    if k <= 0:
        return
    for poly in polys:
        outer = np.asarray(poly[0])
        x, y, z = cam.project(outer[:, 0], outer[:, 1])
        p = path_from(x, y, close=True)
        fa = ease_o(seg(k, 0.45, 1.0)) * fill_a
        if fa > 0:
            c.drawPath(p, skia.Paint(Color=C(color, fa), AntiAlias=True))
        pm = skia.PathMeasure(p, False)
        L = pm.getLength()
        sub = skia.Path()
        pm.getSegment(0, L * ease_io(seg(k, 0, 0.6)), sub, True)
        st = dict(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeJoin=skia.Paint.kRound_Join)
        c.drawPath(sub, skia.Paint(Color=C(WHITE, 0.9), StrokeWidth=width + 4, **st))
        c.drawPath(sub, skia.Paint(Color=C(color, 1), StrokeWidth=width, **st))


# ------------------------------------------------------------------ P10: numbers
def counter(c, x, y, value, k, fmt="{:,.0f}", size=150, unit="", unit_size=None, color=WHITE,
            label=None, label_size=46, align="center", prefix="", start=0.0):
    """P10 数字カウンター。k=0..1 で start→value に増える。prefix が「約」等の文字なら小さく添える"""
    if k <= 0:
        return
    a = clamp01(k * 4)
    v = start + (value - start) * ease_o(k)
    f = font(TF_BLACK, size)
    fu = font(TF_BLACK, unit_size or size * 0.42)
    num = fmt.format(v)
    parts = []
    if prefix and prefix in "+-±":
        num = prefix + num
    elif prefix:
        parts.append((prefix, fu, size * 0.06))
    parts.append((num, f, size * 0.10))
    if unit:
        parts.append((unit, fu, size * 0.06))
    gap = size * 0.05
    w = sum(ft.measureText(tx) for tx, ft, _ in parts) + gap * (len(parts) - 1)
    x0 = x - w / 2 if align == "center" else x
    pop = 1 + 0.12 * (1 - ease_back(seg(k, 0.85, 1.0))) if k > 0.85 else 1
    c.save()
    c.translate(x0 + w / 2, y)
    c.scale(pop, pop)
    c.translate(-(x0 + w / 2), -y)
    xx = x0
    for tx, ft, sw in parts:
        stroke_text(c, tx, xx, y, ft, fill=color, stroke=INK, sw=sw, alpha=a)
        xx += ft.measureText(tx) + gap
    c.restore()
    if label:
        fl = font(TF_BOLD, label_size)
        stroke_text(c, label, x0 + w / 2 if align == "center" else x0, y - size * 0.92, fl, fill=YELLOW,
                    align="center" if align == "center" else "left", alpha=a, sw=label_size * 0.14)


def donut(c, x, y, r, frac, k, color=RED, label="", sub=""):
    """P10 円グラフ（割合を強調）"""
    if k <= 0:
        return
    a = clamp01(k * 3)
    st = dict(AntiAlias=True, Style=skia.Paint.kStroke_Style, StrokeCap=skia.Paint.kButt_Cap)
    rect = skia.Rect.MakeXYWH(x - r, y - r, 2 * r, 2 * r)
    c.drawCircle(x, y, r + r * 0.32, skia.Paint(Color=C((10, 14, 22), 0.75 * a), AntiAlias=True))
    c.drawArc(rect, 0, 360, False, skia.Paint(Color=C((255, 255, 255), 0.22 * a), StrokeWidth=r * 0.38, **st))
    c.drawArc(rect, -90, 360 * frac * ease_o(seg(k, 0.1, 1)), False,
              skia.Paint(Color=C(color, a), StrokeWidth=r * 0.38, **st))
    if label:
        f = font(TF_BLACK, r * 0.62)
        stroke_text(c, label, x, y + r * 0.22, f, align="center", alpha=a, sw=0, shadow=False)


# ------------------------------------------------------------------ P11: telops
def headline(c, text, k, x=70, y=120, size=64, sub=None, color=RED, out=0.0):
    """P11 見出しテロップ（左上）：赤い帯がシュッと伸びて文字がスライドイン"""
    if k <= 0 or out >= 1:
        return
    f = font(TF_BLACK, size)
    w = f.measureText(text)
    a = 1 - ease_io(out)
    bar = ease_o(seg(k, 0, 0.5))
    tx = ease_back(seg(k, 0.2, 0.8), 1.4)
    pad = size * 0.35
    bw = (w + pad * 2) * bar
    c.save()
    c.clipRect(skia.Rect.MakeXYWH(x - 10, y - size * 1.2, bw + size * 0.5 + 20, size * 2.6))
    rrect(c, x, y - size * 1.05, bw, size * 1.42, 6, INK, 0.88 * a)
    c.drawRect(skia.Rect.MakeXYWH(x, y - size * 1.05, size * 0.18, size * 1.42), skia.Paint(Color=C(color, a)))
    c.drawString(text, x + pad + (1 - tx) * -60, y, f, skia.Paint(Color=C(WHITE, a * clamp01(tx * 2)), AntiAlias=True))
    c.restore()
    if sub:
        fs = font(TF_BOLD, size * 0.48)
        k2 = seg(k, 0.5, 1.0)
        if k2 > 0:
            sw = fs.measureText(sub) + pad * 1.6
            rrect(c, x + size * 0.18, y + size * 0.48, sw * ease_o(k2), size * 0.78, 4, color, a)
            c.save()
            c.clipRect(skia.Rect.MakeXYWH(x, y + size * 0.4, sw * ease_o(k2) + 20, size))
            c.drawString(sub, x + size * 0.18 + pad * 0.8, y + size * 1.03, fs, skia.Paint(Color=C(WHITE, a), AntiAlias=True))
            c.restore()


def subtitle(c, text, a, size=50, bottom=74, maxw=1500):
    """P11 字幕（下部中央・白文字黒フチ）"""
    if a <= 0 or not text:
        return
    f = font(TF_BOLD, size)
    lines = wrap_ja(text, f, maxw)
    lh = size * 1.32
    y0 = H - bottom - lh * (len(lines) - 1)
    sh = skia.GradientShader.MakeLinear([skia.Point(0, y0 - lh * 1.4), skia.Point(0, H)],
                                        [C((0, 0, 0), 0), C((0, 0, 0), 0.55 * a)], [0, 1])
    c.drawRect(skia.Rect(0, y0 - lh * 1.4, W, H), skia.Paint(Shader=sh))
    for i, ln in enumerate(lines):
        stroke_text(c, ln, W / 2, y0 + i * lh, f, align="center", alpha=a, sw=size * 0.2)


def chapter_card(c, num, title, k, out=0.0, color=RED):
    """P11 章タイトル：中央に大きく。赤帯が左右から閉じて開く"""
    if k <= 0 or out >= 1:
        return
    a = 1 - ease_io(out)
    band = ease_o(seg(k, 0, 0.45))
    h = 210
    y = H / 2 - h / 2
    c.drawRect(skia.Rect(0, y, W * band, y + h), skia.Paint(Color=C(INK, 0.86 * a)))
    c.drawRect(skia.Rect(W * (1 - band), y + h - 10, W, y + h), skia.Paint(Color=C(color, a)))
    c.drawRect(skia.Rect(0, y, W * band, y + 10), skia.Paint(Color=C(color, a)))
    k2 = ease_back(seg(k, 0.25, 0.75), 1.2)
    if k2 > 0:
        fn = font(TF_BLACK, 72)
        ft = font(TF_BLACK, 92)
        gap = 44
        nw = fn.measureText(num)
        x = W / 2 - (nw + gap + ft.measureText(title)) / 2
        c.save()
        c.translate((1 - k2) * 120, 0)
        stroke_text(c, num, x, y + h / 2 + 30, fn, fill=RED, stroke=WHITE, sw=0, alpha=a * clamp01(k2), shadow=False)
        c.drawRect(skia.Rect.MakeXYWH(x + nw + gap / 2 - 2, y + 50, 4, h - 100), skia.Paint(Color=C(WHITE, 0.5 * a)))
        c.drawString(title, x + nw + gap, y + h / 2 + 34, ft, skia.Paint(Color=C(WHITE, a * clamp01(k2)), AntiAlias=True))
        c.restore()


def title_card(c, title, sub, k, out=0.0):
    """オープニングの大タイトル（中央）"""
    if k <= 0 or out >= 1:
        return
    a = 1 - ease_io(out)
    s = 1 + 0.25 * (1 - ease_back(seg(k, 0, 0.55), 1.3))
    f = font(TF_BLACK, 150)
    c.save()
    c.translate(W / 2, H / 2 + 20)
    c.scale(s, s)
    stroke_text(c, title, 0, 0, f, fill=WHITE, stroke=RED, sw=26, align="center", alpha=a * clamp01(k * 3))
    c.restore()
    k2 = seg(k, 0.4, 1)
    if k2 > 0 and sub:
        fs = font(TF_BLACK, 56)
        w = fs.measureText(sub) + 60
        rrect(c, W / 2 - w / 2, H / 2 + 70, w * ease_o(k2), 84, 8, INK, 0.9 * a)
        c.save()
        c.clipRect(skia.Rect.MakeXYWH(W / 2 - w / 2, H / 2 + 60, w * ease_o(k2), 110))
        c.drawString(sub, W / 2 - w / 2 + 30, H / 2 + 132, fs, skia.Paint(Color=C(YELLOW, a), AntiAlias=True))
        c.restore()


def credit(c, text, a=1.0):
    if a <= 0:
        return
    f = font(TF_MED, 22)
    w = f.measureText(text)
    rrect(c, W - w - 46, 24, w + 28, 40, 6, (0, 0, 0), 0.45 * a, shadow=False)
    c.drawString(text, W - w - 32, 52, f, skia.Paint(Color=C(WHITE, 0.9 * a), AntiAlias=True))


def info_panel(c, x, y, w, rows, k, title=None):
    """数字の比較パネル（右側に出る黒カード）"""
    if k <= 0:
        return
    a = clamp01(k * 3)
    h = 70 + 120 * len(rows) + (60 if title else 0)
    dx = (1 - ease_back(k, 1.2)) * 80
    rrect(c, x + dx, y, w, h, 18, (12, 16, 24), 0.86 * a)
    yy = y + 40
    if title:
        ft = font(TF_BOLD, 36)
        c.drawString(title, x + dx + 36, yy + 26, ft, skia.Paint(Color=C(GRAY, a), AntiAlias=True))
        yy += 60
    for i, (lab, val, col) in enumerate(rows):
        kk = seg(k, 0.15 + 0.25 * i, 0.55 + 0.25 * i)
        if kk <= 0:
            continue
        fl = font(TF_BOLD, 34)
        fv = font(TF_BLACK, 72)
        c.drawString(lab, x + dx + 36, yy + 30, fl, skia.Paint(Color=C(GRAY, a * kk), AntiAlias=True))
        c.drawString(val, x + dx + 36, yy + 104, fv, skia.Paint(Color=C(col, a * kk), AntiAlias=True))
        yy += 120


# ------------------------------------------------------------------ P12: photo card
def photo_card(c, img, k, x, y, w, caption="", tilt=1.0, out=0.0):
    """P12 実写写真カード：奥から手前へパタンと起き上がって出る"""
    if k <= 0 or out >= 1:
        return
    a = (1 - ease_io(out)) * clamp01(k * 3)
    e = ease_back(k, 1.1)
    h = w * img.height() / img.width()
    ang = (1 - e) * 70 * tilt
    c.save()
    c.translate(x, y)
    m = skia.Matrix()
    # 下辺を軸に起き上がる（簡易3D）
    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    persp = sa / (w * 1.6)
    m.setAll(1, 0, -w / 2, 0, ca, -h * ca, 0, persp, 1)
    c.concat(m)
    rrect(c, -10, -10, w + 20, h + 20 + (56 if caption else 0), 10, WHITE, a)
    c.drawImageRect(img, skia.Rect.MakeWH(w, h), SAMPLING, skia.Paint(Alphaf=a))
    if caption:
        f = font(TF_BOLD, 30)
        c.drawString(caption, 6, h + 44, f, skia.Paint(Color=C(INK, a), AntiAlias=True))
    c.restore()


# ------------------------------------------------------------------ P1: globe
class Globe:
    """P1 地球儀。正射投影の球メッシュに Blue Marble を貼る。flat へのモーフィング対応"""

    def __init__(self, assets, step=2.5):
        a = np.asarray(Image.open(os.path.join(assets.root, "earth-blue-marble.jpg")).convert("RGBA"))
        self.tw, self.th = a.shape[1], a.shape[0]
        self.img = skia.Image.fromarray(np.ascontiguousarray(a), colorType=skia.kRGBA_8888_ColorType).withDefaultMipmaps()
        lons = np.arange(-180, 180 + 1e-6, step)
        lats = np.arange(-84, 84 + 1e-6, step)
        self.lon, self.lat = [x.ravel() for x in np.meshgrid(lons, lats)]
        self.nx, self.ny = len(lons), len(lats)
        i0 = (np.arange(self.ny - 1)[:, None] * self.nx + np.arange(self.nx - 1)[None, :]).ravel()
        self.tris = np.stack([i0, i0 + 1, i0 + self.nx, i0 + 1, i0 + self.nx + 1, i0 + self.nx], 1).reshape(-1, 3)
        self.tex = np.stack([(self.lon + 180) / 360 * self.tw, (90 - self.lat) / 180 * self.th], 1)
        self.shader = self.img.makeShader(skia.TileMode.kClamp, skia.TileMode.kClamp, SAMPLING)
        rng = np.random.default_rng(7)
        self.stars = np.c_[rng.uniform(0, W, 900), rng.uniform(0, H, 900), rng.uniform(0.4, 2.2, 900),
                           rng.uniform(0.2, 1.0, 900)]

    @staticmethod
    def ortho(lon, lat, lon0, lat0):
        lam, phi = np.radians(lon - lon0), np.radians(lat)
        p0 = math.radians(lat0)
        x = np.cos(phi) * np.sin(lam)
        y = math.cos(p0) * np.sin(phi) - math.sin(p0) * np.cos(phi) * np.cos(lam)
        cosc = math.sin(p0) * np.sin(phi) + math.cos(p0) * np.cos(phi) * np.cos(lam)
        return x, y, cosc

    def space(self, c, t=0.0, a=1.0):
        c.clear(skia.Color(4, 6, 14))
        sh = skia.GradientShader.MakeRadial(skia.Point(W * 0.5, H * 0.55), W * 0.75,
                                            [C((16, 30, 62)), C((4, 6, 14))], [0, 1])
        c.drawRect(skia.Rect(0, 0, W, H), skia.Paint(Shader=sh))
        for x, y, r, b in self.stars:
            tw = 0.6 + 0.4 * math.sin(t * 2 + x)
            c.drawCircle(float(x), float(y), float(r), skia.Paint(Color=C((255, 255, 255), b * tw * a), AntiAlias=True))

    def draw(self, c, lon0, lat0, R, cx=W / 2, cy=H / 2, morph=0.0, cam=None, alpha=1.0):
        x, y, cosc = self.ortho(self.lon, self.lat, lon0, lat0)
        gx, gy = cx + R * x, cy - R * y
        if morph > 0 and cam is not None:
            u, v = merc(self.lon, self.lat)
            fx, fy, _ = cam.project(u, v)
            m = ease_io(morph)
            gx, gy = gx * (1 - m) + fx * m, gy * (1 - m) + fy * m
        vis = cosc[self.tris].min(1) > 0.0
        tri = self.tris[vis]
        # 画面外の三角形は捨てる
        tx, ty = gx[tri], gy[tri]
        on = (tx.max(1) > -50) & (tx.min(1) < W + 50) & (ty.max(1) > -50) & (ty.min(1) < H + 50)
        tri = tri[on]
        if len(tri) == 0:
            return
        used, inv = np.unique(tri.ravel(), return_inverse=True)
        # 縁を暗く（リムダークニング）※モーフ中は徐々に解除
        shade = np.clip(0.35 + 0.65 * np.sqrt(np.clip(cosc[used], 0, 1)), 0, 1)
        shade = shade * (1 - morph) + morph
        col = (np.clip(shade * 255, 0, 255).astype(np.uint32) * 0x010101) | (int(alpha * 255) << 24)
        verts = skia.Vertices.MakeCopy(
            skia.Vertices.kTriangles_VertexMode,
            list(map(skia.Point, gx[used].tolist(), gy[used].tolist())),
            list(map(skia.Point, self.tex[used, 0].tolist(), self.tex[used, 1].tolist())),
            col.tolist(), inv.astype(np.int64).tolist())
        if morph < 0.999:
            # 大気のにじみ
            ga = (1 - morph) * alpha
            sh = skia.GradientShader.MakeRadial(skia.Point(cx, cy), R * 1.12,
                                                [C((90, 160, 255), 0.0), C((90, 160, 255), 0.55 * ga), C((90, 160, 255), 0)],
                                                [0.86, 0.9, 1.0])
            c.drawCircle(cx, cy, R * 1.12, skia.Paint(Shader=sh, AntiAlias=True))
        c.drawVertices(verts, skia.Paint(Shader=self.shader, AntiAlias=True), skia.BlendMode.kModulate)

    def line(self, lonlat_dense, lon0, lat0, R, cx=W / 2, cy=H / 2, morph=0.0, cam=None):
        a = np.asarray(lonlat_dense)
        x, y, cosc = self.ortho(a[:, 0], a[:, 1], lon0, lat0)
        gx, gy = cx + R * x, cy - R * y
        if morph > 0 and cam is not None:
            u, v = merc(a[:, 0], a[:, 1])
            fx, fy, _ = cam.project(u, v)
            m = ease_io(morph)
            gx, gy = gx * (1 - m) + fx * m, gy * (1 - m) + fy * m
        return gx, gy, cosc > 0.02


# ------------------------------------------------------------------ P6: terrain
def hypsometric(dem):
    """段彩：標高で色分けした地図（海は水深で濃淡）"""
    stops = np.array([0, 50, 200, 500, 1000, 1600, 2400, 3200, 3800])
    cols = np.array([(96, 140, 82), (120, 160, 90), (164, 186, 108), (210, 204, 140), (196, 166, 112),
                     (164, 128, 92), (140, 112, 96), (220, 214, 208), (255, 255, 255)], np.float32)
    z = np.maximum(dem, 0)
    out = np.stack([np.interp(z, stops, cols[:, i]) for i in range(3)], -1)
    sea = dem <= 0
    d = np.clip(-dem / 3000.0, 0, 1)[..., None]
    seac = np.array([70, 128, 186], np.float32) * (1 - d) + np.array([18, 44, 92], np.float32) * d
    out = np.where(sea[..., None], seac, out)
    return out.clip(0, 255).astype(np.uint8)


class Terrain:
    """P6 地形3D。標高メッシュに衛星写真＋陰影をドレープし、exag(誇張倍率)で隆起させる"""

    def __init__(self, assets, dem_name="dem_region.npy", step=9, style="satellite"):
        """style="satellite": 衛星写真＋陰影 / "relief": 段彩（標高で色分け）＋陰影"""
        self.dem_name, self.style = dem_name, style
        dem = np.load(os.path.join(assets.root, dem_name)).astype(np.float32)
        m = assets.meta[dem_name]
        self.u0, self.u1, self.v0, self.v1 = m["u0"], m["u1"], m["v0"], m["v1"]
        self.full = np.maximum(dem, 0)            # 海面は0mに
        hh, ww = dem.shape
        hh2, ww2 = hh // step, ww // step
        g = self.full[:hh2 * step, :ww2 * step].reshape(hh2, step, ww2, step).mean((1, 3))
        self.gh, self.gw = g.shape
        self.elev = g.ravel()
        uu = self.u0 + (np.arange(self.gw) + 0.5) / self.gw * (self.u1 - self.u0) * (ww2 * step / ww)
        vv = self.v0 + (np.arange(self.gh) + 0.5) / self.gh * (self.v1 - self.v0) * (hh2 * step / hh)
        U, V = np.meshgrid(uu, vv)
        self.u, self.v = U.ravel(), V.ravel()
        i0 = (np.arange(self.gh - 1)[:, None] * self.gw + np.arange(self.gw - 1)[None, :]).ravel()
        self.tris = np.stack([i0, i0 + 1, i0 + self.gw, i0 + 1, i0 + self.gw + 1, i0 + self.gw], 1).reshape(-1, 3)
        self._build_texture(assets, dem)

    def _build_texture(self, assets, dem):
        cache = os.path.join(assets.root, f"terrain_tex_{self.style}_{self.dem_name.replace('.npy', '')}.jpg")
        hh, ww = dem.shape
        if not os.path.exists(cache):
            from scipy.ndimage import gaussian_filter
            if self.style == "relief":
                tex = Image.fromarray(hypsometric(dem)).convert("RGBA")
            else:
                base = Image.open(os.path.join(assets.root, "world_merc.jpg"))
                bw, bh = base.size
                box = (self.u0 * bw, self.v0 * bh, self.u1 * bw, self.v1 * bh)
                tex = base.transform((ww, hh), Image.EXTENT, box, Image.BILINEAR).convert("RGBA")
                reg = Image.open(os.path.join(assets.root, "region_s2.png")).convert("RGBA")
                rm = assets.meta["region_s2.png"]
                rw, rh = reg.size
                box = ((self.u0 - rm["u0"]) / (rm["u1"] - rm["u0"]) * rw, (self.v0 - rm["v0"]) / (rm["v1"] - rm["v0"]) * rh,
                       (self.u1 - rm["u0"]) / (rm["u1"] - rm["u0"]) * rw, (self.v1 - rm["v0"]) / (rm["v1"] - rm["v0"]) * rh)
                regt = reg.transform((ww, hh), Image.EXTENT, box, Image.BILINEAR)
                tex.alpha_composite(regt)
            # 陰影（北西から光）
            z = gaussian_filter(np.maximum(dem, 0), 1.0)
            lat_c = float(unmerc(0, (self.v0 + self.v1) / 2)[1])
            px_m = (self.u1 - self.u0) / ww * 2 * math.pi * R_EARTH * math.cos(math.radians(lat_c))
            gy, gx = np.gradient(z, px_m)
            slope = np.arctan(np.hypot(gx, gy) * 3.0)
            asp = np.arctan2(-gx, gy)
            az, alt = math.radians(315), math.radians(42)
            shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - asp)
            shade = np.clip(shade / math.sin(alt), 0, 1.4)
            t = np.asarray(tex.convert("RGB")).astype(np.float32)
            k = (0.55 + 0.45 * shade)[..., None]
            t = np.clip(t * k + (shade[..., None] - 1).clip(0) * 40, 0, 255)
            Image.fromarray(t.astype(np.uint8)).save(cache, quality=92)
        a = np.asarray(Image.open(cache).convert("RGBA"))
        self.tw, self.th = a.shape[1], a.shape[0]
        self.img = skia.Image.fromarray(np.ascontiguousarray(a), colorType=skia.kRGBA_8888_ColorType).withDefaultMipmaps()
        self.shader = self.img.makeShader(skia.TileMode.kClamp, skia.TileMode.kClamp, SAMPLING)
        self.tex = np.stack([(self.u - self.u0) / (self.u1 - self.u0) * self.tw,
                             (self.v - self.v0) / (self.v1 - self.v0) * self.th], 1)

    def height_at(self, u, v):
        """任意の点の標高(m)。線を地形に沿わせる時に使う"""
        hh, ww = self.full.shape
        x = np.clip((np.asarray(u) - self.u0) / (self.u1 - self.u0) * ww - 0.5, 0, ww - 1.001)
        y = np.clip((np.asarray(v) - self.v0) / (self.v1 - self.v0) * hh - 0.5, 0, hh - 1.001)
        x0, y0 = x.astype(int), y.astype(int)
        fx, fy = x - x0, y - y0
        f = self.full
        return (f[y0, x0] * (1 - fx) * (1 - fy) + f[y0, x0 + 1] * fx * (1 - fy) +
                f[y0 + 1, x0] * (1 - fx) * fy + f[y0 + 1, x0 + 1] * fx * fy)

    def draw(self, c, cam, exag=1.0, alpha=1.0):
        sx, sy, z = cam.project(self.u, self.v, self.elev * exag)
        tri = self.tris
        tz = z[tri]
        tx, ty = sx[tri], sy[tri]
        ok = (tz.min(1) > 5) & (tx.max(1) > -80) & (tx.min(1) < W + 80) & (ty.max(1) > -80) & (ty.min(1) < H + 80)
        tri = tri[ok]
        if len(tri) == 0:
            return
        order = np.argsort(-tz[ok].mean(1), kind="stable")   # 奥から手前へ（画家のアルゴリズム）
        tri = tri[order]
        a = int(alpha * 255) << 24
        paint = skia.Paint(Shader=self.shader, AntiAlias=True)
        # skia の頂点インデックスは16bitなので、2万三角形ずつに分けて奥から順に描く
        for k in range(0, len(tri), 20000):
            part = tri[k:k + 20000]
            used, inv = np.unique(part.ravel(), return_inverse=True)
            verts = skia.Vertices.MakeCopy(
                skia.Vertices.kTriangles_VertexMode,
                list(map(skia.Point, sx[used].tolist(), sy[used].tolist())),
                list(map(skia.Point, self.tex[used, 0].tolist(), self.tex[used, 1].tolist())),
                [a | 0xFFFFFF] * len(used), inv.astype(np.int64).tolist())
            c.drawVertices(verts, paint, skia.BlendMode.kModulate)


# ------------------------------------------------------------------ output
class Writer:
    """フレームを ffmpeg に直接流して MP4 化（JPEG 中間ファイルなし）"""

    def __init__(self, path, fps=FPS, crf=18):
        import subprocess
        self.p = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
             "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
             "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)

    def write(self, surface):
        self.p.stdin.write(surface.makeImageSnapshot().toarray().tobytes())

    def close(self):
        self.p.stdin.close()
        self.p.wait()


# ------------------------------------------------------------------ P13: profile (断面図)
def profile_chart(c, x, y, w, h, series, k, title="", ymax=2500, unit="m", out=0.0):
    """P13 標高の断面図：線が左から右へうにょーんと伸びる。series=[(名前, 値の配列, 色), ...]"""
    if k <= 0 or out >= 1:
        return
    a = clamp01(k * 4) * (1 - ease_io(out))
    dy = (1 - ease_o(seg(k, 0, 0.3))) * 40
    y = y + dy
    rrect(c, x, y, w, h, 16, (12, 16, 24), 0.86 * a)
    px, py, pw, ph = x + 92, y + 70, w - 130, h - 120
    fl = font(TF_BOLD, 26)
    if title:
        c.drawString(title, x + 28, y + 46, font(TF_BOLD, 32), skia.Paint(Color=C(WHITE, a), AntiAlias=True))
    for i in range(0, ymax + 1, ymax // 2):
        yy = py + ph - ph * i / ymax
        c.drawLine(px, yy, px + pw, yy, skia.Paint(Color=C(WHITE, 0.18 * a), StrokeWidth=1.5))
        lab = f"{i:,}{unit}"
        c.drawString(lab, px - 14 - fl.measureText(lab), yy + 9, fl, skia.Paint(Color=C(GRAY, a), AntiAlias=True))
    for si, (name, vals, col) in enumerate(series):
        kk = ease_io(seg(k, 0.15 + si * 0.25, 0.75 + si * 0.25))
        if kk <= 0:
            continue
        v = np.asarray(vals, np.float64)
        n = max(2, int(len(v) * kk))
        xs = px + np.linspace(0, pw, len(v))[:n]
        ys = py + ph - ph * np.clip(v[:n], 0, ymax) / ymax
        fill = path_from(np.r_[xs, xs[-1], xs[0]], np.r_[ys, py + ph, py + ph], close=True)
        c.drawPath(fill, skia.Paint(Color=C(col, 0.28 * a), AntiAlias=True))
        c.drawPath(path_from(xs, ys), skia.Paint(Color=C(col, a), AntiAlias=True, Style=skia.Paint.kStroke_Style,
                                                 StrokeWidth=5, StrokeJoin=skia.Paint.kRound_Join))
        c.drawCircle(float(xs[-1]), float(ys[-1]), 7, skia.Paint(Color=C(WHITE, a), AntiAlias=True))
        fn = font(TF_BLACK, 30)
        ly = float(ys[-1]) - 16 if si == 0 else float(min(ys)) - 14
        c.drawString(name, float(xs[-1]) - fn.measureText(name) - 10, max(py + 28, ly), fn,
                     skia.Paint(Color=C(col, a * clamp01(kk * 3 - 2)), AntiAlias=True))


def photo_frame(c, k, label="実際の衛星写真", sub=""):
    """実写であることを示すファインダー枠（四隅のL字＋REC表示）"""
    if k <= 0:
        return
    a = clamp01(k * 3)
    m, L = 60, 90 * ease_o(k)
    p = skia.Paint(Color=C(WHITE, 0.9 * a), StrokeWidth=6, AntiAlias=True, StrokeCap=skia.Paint.kSquare_Cap)
    for (x, y, sx, sy) in [(m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)]:
        c.drawLine(x, y, x + sx * L, y, p)
        c.drawLine(x, y, x, y + sy * L, p)
    f = font(TF_BLACK, 34)
    lw = f.measureText(label) + 70
    x0, y0 = W - m - 24 - lw, m + 40
    rrect(c, x0, y0, lw, 56, 6, RED, a)
    blink = 0.5 + 0.5 * math.sin(k * 40) if k < 1 else 1
    c.drawCircle(x0 + 26, y0 + 28, 9, skia.Paint(Color=C(WHITE, a * blink), AntiAlias=True))
    c.drawString(label, x0 + 44, y0 + 40, f, skia.Paint(Color=C(WHITE, a), AntiAlias=True))
    if sub:
        fs = font(TF_BOLD, 30)
        stroke_text(c, sub, W - m - 24, y0 + 100, fs, alpha=a, sw=6, align="right")
