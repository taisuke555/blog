# -*- coding: utf-8 -*-
"""台本一貫スタジオ：トークン予算の部品。

方針（README「予算主義」）：
  - 仕様（設計図・設計書・固有ルール・素材・直す対象）は全文で渡す。
  - 参照（参考台本・成功台本・既に書いた本文・前後の文脈）は予算内の抜粋で渡す。
  - 1回の呼び出しの入力に上限を設け、優先度の低い部分から機械的に削る。
  - 呼び出しごとの消費（推定または CLI 実測）をジョブ単位で集計する。
"""
import json
import math
import re
import threading

# ----------------------------------------------------------------
# 概算
# ----------------------------------------------------------------

_ASCII_RE = re.compile(r"[\x00-\x7f]")


def est_tokens(text):
    """日本語中心の文字列のトークン概算。非ASCII≒1字1トークン、ASCII≒3.5字1トークン。
    実測が取れない時の目安であり、相対比較（削減前後）に使う。"""
    if not text:
        return 0
    s = str(text)
    ascii_n = len(_ASCII_RE.findall(s))
    return int(math.ceil(ascii_n / 3.5 + (len(s) - ascii_n)))


def fmt_tokens(n):
    """表示用：1万単位で丸める（12,345 → 約1.2万）。"""
    n = int(n or 0)
    if n >= 10000:
        return f"約{n / 10000:.1f}万"
    if n >= 1000:
        return f"約{n / 1000:.1f}千"
    return str(n)


# ----------------------------------------------------------------
# 抜粋
# ----------------------------------------------------------------

_CUT = "\n…（中略）…\n"


def _cut_at_boundary(text, start, end):
    """文の切れ目（。！？や改行）に寄せて切る。見つからなければそのまま。"""
    seg = text[start:end]
    if start > 0:
        m = re.search(r"[。！？\n]", seg[:120])
        if m:
            seg = seg[m.end():]
    if end < len(text):
        last = max(seg.rfind("。"), seg.rfind("！"), seg.rfind("？"), seg.rfind("\n"))
        if last >= len(seg) - 120 and last > 0:
            seg = seg[:last + 1]
    return seg.strip("\n")


def excerpt(text, n, mode="hmt"):
    """text を約 n 字に抜粋する。
    mode: head（冒頭）／tail（末尾）／mid（中盤）／hmt（冒頭・中盤・末尾を1/3ずつ）／
          head_heavy（冒頭2/3＋末尾1/3）／tail_heavy（冒頭1/3＋末尾2/3）／mid_heavy（冒頭1/4・中盤1/2・末尾1/4）。
    n<=0 または本文が n 以下なら全文を返す。"""
    text = text or ""
    n = int(n or 0)
    if n <= 0 or len(text) <= n:
        return text
    L = len(text)
    if mode == "head":
        return _cut_at_boundary(text, 0, n) + "\n（以下略）"
    if mode == "tail":
        return "（前略）\n" + _cut_at_boundary(text, L - n, L)
    if mode == "mid":
        s = max(0, L // 2 - n // 2)
        return "（前略）\n" + _cut_at_boundary(text, s, s + n) + "\n（以下略）"
    if mode == "head_heavy":
        a, c = int(n * 2 / 3), n - int(n * 2 / 3)
        return _cut_at_boundary(text, 0, a) + _CUT + _cut_at_boundary(text, L - c, L)
    if mode == "tail_heavy":
        a, c = n - int(n * 2 / 3), int(n * 2 / 3)
        return _cut_at_boundary(text, 0, a) + _CUT + _cut_at_boundary(text, L - c, L)
    if mode == "mid_heavy":
        a, b = n // 4, n // 2
        c = n - a - b
        s = max(a, L // 2 - b // 2)
        return _cut_at_boundary(text, 0, a) + _CUT + _cut_at_boundary(text, s, s + b) + _CUT + _cut_at_boundary(text, L - c, L)
    # hmt
    a = n // 3
    b = n // 3
    c = n - a - b
    s = max(a, L // 2 - b // 2)
    return _cut_at_boundary(text, 0, a) + _CUT + _cut_at_boundary(text, s, s + b) + _CUT + _cut_at_boundary(text, L - c, L)


def excerpt_at(text, frac, n):
    """text の相対位置 frac（0.0〜1.0）を中心に約 n 字の連続窓を取る。窓の両端は文の切れ目に寄せる。
    執筆中の塊が台本全体のどの位置かを参考台本の同じ位置に写すために使う（冒頭はリズムの立ち上がり、
    中間CTAの位置には参考台本の中間CTA付近、締めにはCTAの温度が入る）。"""
    text = text or ""
    n = int(n or 0)
    L = len(text)
    if n <= 0 or L <= n:
        return text
    try:
        frac = min(1.0, max(0.0, float(frac)))
    except (TypeError, ValueError):
        frac = 0.5
    center = int(L * frac)
    start = max(0, min(L - n, center - n // 2))
    end = min(L, start + n)
    body = _cut_at_boundary(text, start, end)
    return ("（前略）\n" if start > 0 else "") + body + ("\n（以下略）" if end < L else "")


def split_budget(total, lengths):
    """合計 total 字を、各本の長さに比例しつつ下限を保って配分する。全文が入る本は全文の長さを返す。"""
    lengths = [max(0, int(x)) for x in lengths]
    if not lengths or total <= 0:
        return [0] * len(lengths)
    alloc = [0] * len(lengths)
    remaining = total
    pending = list(range(len(lengths)))
    # 全文が収まる短い本から確定していく
    while pending:
        share = remaining // len(pending)
        fits = [i for i in pending if lengths[i] <= share]
        if not fits:
            for i in pending:
                alloc[i] = share
            break
        for i in fits:
            alloc[i] = lengths[i]
            remaining -= lengths[i]
            pending.remove(i)
    return alloc


# ----------------------------------------------------------------
# 消費メーター（ジョブ単位）
# ----------------------------------------------------------------

_local = threading.local()


class Meter:
    """1ジョブ内の呼び出しを集計する。run_claude が current() に記録する。"""

    def __init__(self, label="", settings=None):
        self.label = label
        self.settings = settings  # ジョブ開始時の設定（途中で設定を変えても塊分けや予算が揺れないように固定する）
        self.calls = 0
        self.in_tokens = 0
        self.cache_tokens = 0  # 入力のうちキャッシュ読み（料金は約1/10）
        self.out_tokens = 0
        self.in_chars = 0
        self.out_chars = 0
        self.measured = 0  # CLI の usage が取れた回数
        self.cost_usd = 0.0
        self.by_phase = {}
        self.trimmed = []
        self.warnings = []
        self.notes = {}

    def record(self, phase, prompt, result, usage=None, cost=None):
        in_c, out_c = len(prompt or ""), len(result or "")
        cache_t = 0
        if usage and isinstance(usage, dict) and usage.get("input_tokens") is not None:
            cache_t = int(usage.get("cache_read_input_tokens") or 0)
            in_t = int(usage.get("input_tokens") or 0) + cache_t + int(usage.get("cache_creation_input_tokens") or 0)
            out_t = int(usage.get("output_tokens") or 0)
            self.measured += 1
        else:
            in_t, out_t = est_tokens(prompt), est_tokens(result)
        self.calls += 1
        self.in_tokens += in_t
        self.cache_tokens += cache_t
        self.out_tokens += out_t
        self.in_chars += in_c
        self.out_chars += out_c
        if cost:
            try:
                self.cost_usd += float(cost)
            except (TypeError, ValueError):
                pass
        d = self.by_phase.setdefault(phase or "?", {"calls": 0, "in_tokens": 0, "out_tokens": 0, "max_in_chars": 0})
        d["calls"] += 1
        d["in_tokens"] += in_t
        d["out_tokens"] += out_t
        d["max_in_chars"] = max(d["max_in_chars"], in_c)

    def note_trim(self, phase, trimmed):
        for t in trimmed or []:
            self.trimmed.append({"phase": phase, **t})

    def warn(self, text):
        if text and text not in self.warnings:
            self.warnings.append(text)

    def note(self, key, value):
        self.notes[key] = value

    def summary(self):
        cache = f"（うちキャッシュ読み{fmt_tokens(self.cache_tokens)}）" if self.cache_tokens else ""
        return {"label": self.label, "calls": self.calls, "in_tokens": self.in_tokens, "cache_tokens": self.cache_tokens,
                "out_tokens": self.out_tokens, "in_chars": self.in_chars, "out_chars": self.out_chars, "measured": self.measured,
                "cost_usd": round(self.cost_usd, 4) if self.cost_usd else None, "by_phase": self.by_phase,
                "trimmed": self.trimmed[-40:], "warnings": self.warnings[-20:], "notes": self.notes,
                "text": (f"呼び出し{self.calls}回／入力{fmt_tokens(self.in_tokens)}トークン{cache}／出力{fmt_tokens(self.out_tokens)}トークン"
                         + ("（実測）" if self.measured == self.calls and self.calls else "（推定）" if self.measured == 0 else "（一部推定）"))}


def start(label="", settings=None):
    m = Meter(label, settings)
    _local.meter = m
    return m


def current():
    return getattr(_local, "meter", None)


def stop():
    m = getattr(_local, "meter", None)
    _local.meter = None
    return m


def phase_of(prompt):
    m = re.match(r"#PHASE:([\w-]+)", prompt or "")
    return m.group(1) if m else "?"


def text_hash(text):
    """ブロック本文の変更検知用（軽量）。"""
    import hashlib
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:12]


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)
