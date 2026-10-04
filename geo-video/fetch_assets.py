# -*- coding: utf-8 -*-
"""地理解説動画の素材取得（実写の衛星写真・地形・海岸線）。

すべて Web メルカトル正規化座標 (u,v ∈ [0,1]) にそろえて GEO_ASSETS に保存する。
  world_merc.jpg      NASA Blue Marble（パブリックドメイン）をメルカトルに変換
  region_s2.png       Sentinel-2 モザイク（スエズ周辺 約500km四方, ~140m/px）
  canal_s2.png        Sentinel-2 2021-03/04（運河全体, 40m/px）
  evergiven_s2.png    Sentinel-2 2021-03-24（座礁地点, 10m/px）
  queue_s2.png        Sentinel-2 2021-03-24（スエズ湾の待機船, 20m/px）
  dem_region.npy      AWS Terrain Tiles（標高, 地形3D用）
  vectors.json        Natural Earth の海岸線・国境（必要範囲だけ）
  layers.json         上記ラスタの地理範囲メタデータ

出典表記: NASA Blue Marble / Contains modified Copernicus Sentinel data 2021 /
          Mapzen Terrain Tiles (AWS Open Data) / Natural Earth
usage: GEO_ASSETS=/path python fetch_assets.py
"""
import os, io, json, math, urllib.request, concurrent.futures as cf
import numpy as np
from PIL import Image

os.environ.setdefault("CURL_CA_BUNDLE", "/root/.ccr/ca-bundle.crt")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
import rasterio, mgrs
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from pyproj import Transformer

OUT = os.environ.get("GEO_ASSETS", os.path.join(os.path.dirname(__file__), "assets"))
os.makedirs(OUT, exist_ok=True)
Image.MAX_IMAGE_PIXELS = None

RAW = "https://raw.githubusercontent.com/"
S2 = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/"
R = 6378137.0
HALF = math.pi * R
TO3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)


def get(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def cached(name, url):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        open(p, "wb").write(get(url))
    return p


def merc(lon, lat):
    """lon/lat -> 正規化メルカトル(u,v)"""
    x, y = TO3857.transform(lon, lat)
    return (x + HALF) / (2 * HALF), (HALF - y) / (2 * HALF)


# ---------------------------------------------------------------- world
def build_world():
    out = os.path.join(OUT, "world_merc.jpg")
    if os.path.exists(out):
        return
    src = np.asarray(Image.open(cached("earth-blue-marble.jpg",
        RAW + "vasturiano/three-globe/master/example/img/earth-blue-marble.jpg")).convert("RGB"))
    H, W = src.shape[:2]
    N = 4096
    v = (np.arange(N) + 0.5) / N
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * v))))
    rows = np.clip(((90 - lat) / 180 * H).astype(int), 0, H - 1)
    img = src[rows][:, (np.arange(N) * W // N)]
    Image.fromarray(img).save(out, quality=93)
    # 夜景（都市の明かり）も同様に変換しておく（パターン用）
    src = np.asarray(Image.open(cached("earth-night.jpg",
        RAW + "vasturiano/three-globe/master/example/img/earth-night.jpg")).convert("RGB"))
    img = src[rows][:, (np.arange(N) * W // N)]
    Image.fromarray(img).save(os.path.join(OUT, "night_merc.jpg"), quality=92)


# ---------------------------------------------------------------- sentinel-2
LO, HI, GAMMA = 0.025, 0.50, 0.80   # 全シーン共通のトーン（モザイクの継ぎ目を出さない）


def scene_meta(prefix):
    name = prefix.rstrip("/").split("/")[-1]
    d = json.loads(get(S2 + prefix + name + ".json"))
    # 注: 再処理版(_1_, baseline 05.00)は STAC 上 offset=-0.1 とあるが、実測では
    # DN が旧版(_0_)とほぼ同値（+1000 されていない）。offset を当てると真っ暗になるので 0 固定。
    return d, 0.0


def list_scenes(tile, months):
    import re
    z, b, g = tile[:2], tile[2], tile[3:]
    out = []
    for mo in months:
        x = get(S2 + f"?list-type=2&delimiter=/&prefix=sentinel-s2-l2a-cogs/{int(z)}/{b}/{g}/{mo}/").decode()
        out += re.findall(r"<Prefix>([^<]*L2A/)</Prefix>", x)
    return out


def read_reflectance(prefix, off, bounds_3857, res, shape):
    """シーンの R,G,B を 3857 グリッドへ再投影して反射率(0..1)で返す。nodata=NaN"""
    W, H = shape
    dst_tr = from_origin(bounds_3857[0], bounds_3857[3], res, res)
    out = np.full((3, H, W), np.nan, np.float32)
    for i, band in enumerate(["B04", "B03", "B02"]):
        with rasterio.open("/vsicurl/" + S2 + prefix + band + ".tif") as s:
            # 必要解像度に近いオーバービューで読む
            f = max(1, min(16, int(res / 10 / 1.2)))
            f = max([o for o in [1, 2, 4, 8, 16] if o <= f])
            t = Transformer.from_crs("EPSG:3857", s.crs, always_xy=True)
            xs, ys = t.transform([bounds_3857[0], bounds_3857[2], bounds_3857[0], bounds_3857[2]],
                                 [bounds_3857[1], bounds_3857[1], bounds_3857[3], bounds_3857[3]])
            win = from_bounds(min(xs), min(ys), max(xs), max(ys), s.transform)
            win = win.intersection(rasterio.windows.Window(0, 0, s.width, s.height))
            ow, oh = max(1, int(win.width / f)), max(1, int(win.height / f))
            a = s.read(1, window=win, out_shape=(oh, ow), resampling=Resampling.average).astype(np.float32)
            wt = s.window_transform(win) * rasterio.Affine.scale(win.width / ow, win.height / oh)
            a[a == 0] = np.nan
            band_out = np.full((H, W), np.nan, np.float32)
            reproject(a, band_out, src_transform=wt, src_crs=s.crs, dst_transform=dst_tr,
                      dst_crs="EPSG:3857", resampling=Resampling.bilinear,
                      src_nodata=np.nan, dst_nodata=np.nan)
            out[i] = band_out * 1e-4 + off
    return out


def tone_f(refl):
    """反射率 → 表示用RGB (3,H,W) float 0..1。欠損は NaN のまま"""
    v = np.clip((refl - LO) / (HI - LO), 0, 1) ** GAMMA
    m = v.mean(0, keepdims=True)
    return np.clip(m + (v - m) * 1.18, 0, 1)          # ほんの少し彩度を上げる


def match_color(d, ref, mask, lo=0.6, hi=1.6):
    """d の色を ref に合わせる（チャンネル別のゲイン）。撮影日違いの継ぎ目対策"""
    if mask.sum() < 500:
        return d
    g = np.median(ref[:, mask], 1) / np.maximum(np.median(d[:, mask], 1), 1e-3)
    return np.clip(d * np.clip(g, lo, hi)[:, None, None], 0, 1)


def finalize(d, fill_holes=True, feather=6):
    """float RGB(NaN=欠損) → RGBA uint8。欠損は透明、外周はぼかして下のレイヤーへ馴染ませる"""
    from scipy.ndimage import gaussian_filter, binary_erosion, binary_closing, distance_transform_edt
    valid = ~np.isnan(d[0])
    rgb = (np.transpose(np.nan_to_num(d), (1, 2, 0)) * 255).astype(np.uint8)
    filled = valid
    if fill_holes:
        # 小さな欠損は最寄りの画素で埋める（データ範囲外の海など大きな欠損は透明のまま）
        filled = binary_closing(valid, iterations=12) | valid
        idx = distance_transform_edt(~valid, return_distances=False, return_indices=True)
        rgb = rgb[idx[0], idx[1]]
    m = np.pad(filled, 1)        # 画像の外周も「欠損」扱いにしてフェードさせる
    a = gaussian_filter(binary_erosion(m, iterations=feather).astype(np.float32), feather)[1:-1, 1:-1]
    return np.dstack([rgb, (a * 255).astype(np.uint8)])


def sample_rgb(name, bounds_3857, shape, meta):
    """保存済みレイヤー（または world_merc）を 3857 グリッドへ再標本化 → (3,H,W) float"""
    im = Image.open(os.path.join(OUT, name)).convert("RGB")
    L = meta.get(name, {"u0": 0, "u1": 1, "v0": 0, "v1": 1})
    iw, ih = im.size
    m = layer_meta(bounds_3857)
    box = ((m["u0"] - L["u0"]) / (L["u1"] - L["u0"]) * iw, (m["v0"] - L["v0"]) / (L["v1"] - L["v0"]) * ih,
           (m["u1"] - L["u0"]) / (L["u1"] - L["u0"]) * iw, (m["v1"] - L["v0"]) / (L["v1"] - L["v0"]) * ih)
    out = im.transform(shape, Image.EXTENT, box, Image.BILINEAR)
    return np.transpose(np.asarray(out).astype(np.float32) / 255, (2, 0, 1))


def grid(lon0, lat0, lon1, lat1, res):
    x0, y0 = TO3857.transform(lon0, lat0)
    x1, y1 = TO3857.transform(lon1, lat1)
    W, H = int((x1 - x0) / res), int((y1 - y0) / res)
    return (x0, y0, x0 + W * res, y0 + H * res), (W, H)


def layer_meta(b):
    return {"u0": (b[0] + HALF) / (2 * HALF), "u1": (b[2] + HALF) / (2 * HALF),
            "v0": (HALF - b[3]) / (2 * HALF), "v1": (HALF - b[1]) / (2 * HALF)}


def build_region(meta):
    name = "region_s2.png"
    lon0, lat0, lon1, lat1 = 30.2, 28.0, 35.0, 32.6
    res = 160.0
    b, (W, H) = grid(lon0, lat0, lon1, lat1, res)
    meta[name] = layer_meta(b) | {"res_m": res}
    if os.path.exists(os.path.join(OUT, name)):
        return
    m = mgrs.MGRS()
    tiles = sorted({m.toMGRS(la, lo, MGRSPrecision=0)
                    for la in np.arange(lat0, lat1 + 0.01, 0.2) for lo in np.arange(lon0, lon1 + 0.01, 0.2)}
                   # UTM 35 帯のタイルも東へはみ出して範囲西端を覆うので、穴埋め用に加える
                   | {m.toMGRS(la, 29.95, MGRSPrecision=0) for la in np.arange(lat0, lat1 + 0.01, 0.2)})

    def best(tile):
        cands = []
        for p in list_scenes(tile, ["2021/3", "2021/4", "2021/2"]):
            try:
                d, off = scene_meta(p)
                pr = d["properties"]
                cands.append((pr["eo:cloud_cover"] + 0.3 * pr["s2:nodata_pixel_percentage"], p, off,
                              pr["s2:nodata_pixel_percentage"]))
            except Exception:
                pass
        cands.sort()
        if not cands:
            return tile, []
        # 最良シーン + 別日付の予備（雲・欠損の穴埋め用）
        picks, dates = [cands[0]], {cands[0][1].split("_")[-3]}
        for c in cands[1:]:
            d = c[1].split("_")[-3]
            if d not in dates and (c[3] < 1 or len(picks) < 3):
                picks.append(c); dates.add(d)
            if len(picks) >= (2 if cands[0][3] < 1 else 3):
                break
        return tile, picks

    with cf.ThreadPoolExecutor(12) as ex:
        picks = dict(ex.map(best, tiles))
    acc = np.zeros((3, H, W), np.float32)
    # 色の基準 = NASA Blue Marble（全球で色がそろっている）。陸地だけで比べる
    bm = sample_rgb("world_merc.jpg", b, (W, H), meta)
    land = ~((bm[2] > bm[0] + 0.04) & (bm[2] > bm[1]))

    def job(args):
        _, p, off, _ = args
        d = tone_f(read_reflectance(p, off, b, res, (W, H)))
        return match_color(d, bm, land & ~np.isnan(d[0]))

    # 重なり部分はフェザー（端ほど軽い重み）で混ぜ、タイルの継ぎ目を段差ではなくグラデーションにする。
    # 予備シーン（2番手以降）は重みを下げ、主に穴埋めに使う。メモリ節約のため 6 シーンずつ処理。
    from scipy.ndimage import distance_transform_edt
    primary = {picks[t][0][1] for t in tiles if picks[t]}
    jobs = sorted((c for t in tiles for c in picks[t]), key=lambda c: c[0])
    print(f"region mosaic: {len(tiles)} tiles / {len(jobs)} scenes")
    wsum = np.zeros((H, W), np.float32)
    with cf.ThreadPoolExecutor(6) as ex:
        for k in range(0, len(jobs), 6):
            for job_args, r in zip(jobs[k:k + 6], ex.map(job, jobs[k:k + 6])):
                valid = ~np.isnan(r[0])
                w = np.clip(distance_transform_edt(valid) / 45.0, 0, 1) ** 1.5
                w *= 1.0 if job_args[1] in primary else 0.08
                acc0 = np.nan_to_num(acc)
                acc = acc0 + np.nan_to_num(r) * w
                wsum += w
                del r
    with np.errstate(invalid="ignore", divide="ignore"):
        acc = np.where(wsum > 1e-4, acc / np.maximum(wsum, 1e-6), np.nan)
    Image.fromarray(finalize(acc)).save(os.path.join(OUT, name))


def build_scene_crop(meta, name, prefixes, bbox, res, ref="region_s2.png", feather=12, contrast=1.0):
    """1シーン（または数シーン）の切り出し。色は ref レイヤーに合わせる"""
    b, (W, H) = grid(*bbox, res)
    meta[name] = layer_meta(b) | {"res_m": res}
    if os.path.exists(os.path.join(OUT, name)):
        return
    acc = np.full((3, H, W), np.nan, np.float32)
    for p in prefixes:
        _, off = scene_meta(p)
        r = tone_f(read_reflectance(p, off, b, res, (W, H)))
        hole = np.isnan(acc[0]) & ~np.isnan(r[0])
        acc[:, hole] = r[:, hole]
    refimg = sample_rgb(ref, b, (W, H), meta)
    acc = match_color(acc, refimg, ~np.isnan(acc[0]))
    if contrast != 1.0:
        # 拡大して見せる実写は少しだけコントラストを上げる（砂塵のかすみ対策）
        m = np.nanmean(acc, axis=(1, 2), keepdims=True)
        acc = np.clip(m + (acc - m) * contrast, 0, 1)
    Image.fromarray(finalize(acc, fill_holes=False, feather=feather)).save(os.path.join(OUT, name))


# ---------------------------------------------------------------- DEM
def build_dem(meta, z=9, name="dem_region.npy", bbox=(30.2, 28.0, 35.0, 32.6)):
    lon0, lat0, lon1, lat1 = bbox
    n = 2 ** z
    tx0 = int((lon0 + 180) / 360 * n); tx1 = int((lon1 + 180) / 360 * n)
    ty0 = int(merc(lon0, lat1)[1] * n); ty1 = int(merc(lon0, lat0)[1] * n)
    meta[name] = {"u0": tx0 / n, "u1": (tx1 + 1) / n, "v0": ty0 / n, "v1": (ty1 + 1) / n}
    if os.path.exists(os.path.join(OUT, name)):
        return

    def tile(xy):
        x, y = xy
        png = get(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png")
        a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.float32)
        return xy, a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768

    xy = [(x, y) for y in range(ty0, ty1 + 1) for x in range(tx0, tx1 + 1)]
    dem = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    with cf.ThreadPoolExecutor(16) as ex:
        for (x, y), a in ex.map(tile, xy):
            dem[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = a
    np.save(os.path.join(OUT, name), dem)


# ---------------------------------------------------------------- vectors
def build_vectors():
    out = os.path.join(OUT, "vectors.json")
    if os.path.exists(out):
        return
    nat = RAW + "nvkelso/natural-earth-vector/master/geojson/"
    c50 = json.load(open(cached("ne_50m_coastline.geojson", nat + "ne_50m_coastline.geojson")))
    c10 = json.load(open(cached("ne_10m_coastline.geojson", nat + "ne_10m_coastline.geojson")))
    cty = json.load(open(cached("ne_50m_admin_0_countries.geojson", nat + "ne_50m_admin_0_countries.geojson")))

    def lines(fc, bbox=None):
        res = []
        for f in fc["features"]:
            g = f["geometry"]
            parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for p in parts:
                if bbox and not any(bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3] for x, y in p):
                    continue
                res.append([[round(c, 5) for c in merc(x, max(-85, min(85, y)))] for x, y in p])
        return res

    def polys(name):
        for f in cty["features"]:
            if f["properties"]["NAME"] == name:
                g = f["geometry"]
                ps = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
                return [[[[round(c, 6) for c in merc(x, y)] for x, y in ring] for ring in p] for p in ps]
    json.dump({
        "coast50": lines(c50),
        "coast10": lines(c10, (25, 24, 40, 36)),
        "countries": {k: polys(k) for k in ["Egypt", "Panama", "Japan", "Saudi Arabia", "Israel", "Jordan"]},
    }, open(out, "w"))


if __name__ == "__main__":
    meta = {}
    build_world(); print("world ok")
    build_vectors(); print("vectors ok")
    build_dem(meta); print("dem ok")
    # パターン集用：富士山〜関東（地形3D・断面図のデモ）
    build_dem(meta, z=10, name="dem_japan.npy", bbox=(137.4, 34.0, 141.2, 37.2)); print("dem japan ok")
    build_region(meta); print("region ok")
    P = "sentinel-s2-l2a-cogs/36/R/"
    # 運河全体: 雲・砂塵の少ない日（北: 4/13, 南: 3/19）
    build_scene_crop(meta, "canal_s2.png",
                     [P + "VV/2021/4/S2A_36RVV_20210413_1_L2A/", P + "VU/2021/3/S2B_36RVU_20210319_1_L2A/"],
                     (31.95, 29.80, 32.75, 31.35), 40.0, feather=16)
    print("canal ok")
    # 座礁翌日 2021-03-24 の実写（エバーギブン号・スエズ湾の待機船）
    build_scene_crop(meta, "evergiven_s2.png", [P + "VU/2021/3/S2A_36RVU_20210324_0_L2A/"],
                     (32.535, 29.985, 32.625, 30.050), 10.0, ref="canal_s2.png", feather=24, contrast=1.3)
    build_scene_crop(meta, "queue_s2.png", [P + "VU/2021/3/S2A_36RVU_20210324_0_L2A/"],
                     (32.40, 29.74, 32.72, 29.99), 20.0, ref="canal_s2.png", feather=20, contrast=1.2)
    print("crops ok")
    json.dump(meta, open(os.path.join(OUT, "layers.json"), "w"), indent=1)
    print("done ->", OUT)
