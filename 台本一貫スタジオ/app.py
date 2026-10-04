# -*- coding: utf-8 -*-
"""台本一貫スタジオ — 参考台本の型抽出 → チャンネル設計（コンセプト・キャラ）→ 企画・調査 →
執筆 → 自動検品の輪 → 人間の添削 → 差分学習 → カルテ蓄積 を、1台のPC内で完結させるローカルツール。

エンジン：settings.json の engine で切り替える。
  "claude" … ローカルの Claude Code CLI（claude -p）を呼ぶ
  "mock"   … UIテスト用のダミー応答
外部採点（Jev）は settings.json の jev.enabled が true で、Node と APIキーがある場合だけ動く。
無い場合は Claude の検品官だけで輪を回す（機能は落ちない）。
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import budget
import prompts
import prompts_ext as ext

BASE = Path(__file__).resolve().parent
PROJECTS_DIR = BASE / "projects"
KARTE_DIR = BASE / "karte"
SETTINGS_PATH = BASE / "settings.json"
MANUAL_PATH = BASE / "manual" / "manual.md"
JEV_DIR = BASE / "jev"
PORT = 5071

app = Flask(__name__)
jobs = {}
job_lock = threading.Lock()

DEFAULT_SETTINGS = {"engine": "claude", "timeout_sec": 900, "audit_rounds": 2, "auto_polish": True, "tone_check": True, "bp_autofix": True,
                    "write_mode": "blocks",
                    # --- トークン予算（README「予算主義」）。仕様は全文、参照は予算内の抜粋、変わっていない所は再処理しない ---
                    "input_cap_chars": 36000,     # 1回の呼び出しに渡す入力の上限（字）。超えそうなら優先度の低い参照から削る
                    "ref_mode": "budget",         # budget=予算内・位置対応の抜粋（推奨）／full=全文（旧方式・高コスト）／excerpt=冒頭中盤の抜粋（最軽量）
                    "ref_budget_chars": 6000,     # 執筆に渡す参考台本の合計（成功例で分け合う）
                    "ref_min_chars": 1500,        # 参考台本1本あたりの最低字数。足りなければ長い ref_max_count 本だけ使う
                    "ref_max_count": 3,
                    "ref_full_cap": 0,            # ref_mode=full のときの合計上限（0=無制限）
                    "tone_ref_chars": 4500,       # 口調照合に渡す参考台本の合計
                    "prev_text_cap": 2500,        # ブロック執筆で渡す「直前までの本文」の末尾字数
                    "style_sample_cap": 3000,     # 修正時の文体見本（最新の成功台本から抜粋）
                    "context_chars": 800,         # 部分修正で渡す隣接ブロックの末尾／冒頭
                    "polish_context_chars": 600,  # 推敲で渡す前の塊の末尾（次の塊の冒頭は400）
                    "write_chunk_chars": 2400,    # ブロック執筆の1回あたり目安字数（小さいブロックはまとめて1回で書く）
                    "max_blocks_per_chunk": 4,    # 1回で書くブロック数の上限（まとめ過ぎると名前ずれ・併合が起きる）
                    "polish_chunk_chars": 3600,   # 推敲の1回あたり字数（推敲はナレッジ約1.4万字が毎回載るので回数を減らす）
                    "mock_audit_ng": 0,           # モック時に検品官が返す不合格の数（部分修正の経路を画面テストで踏むため）
                    "settings_version": 2,
                    "jev": {"enabled": False, "rubric": "daihon.json"}}

# 旧 settings.json の 0（＝旧既定：1ブロックずつ／無制限）は新既定に読み替えるキー
_ZERO_MEANS_DEFAULT = ("write_chunk_chars", "prev_text_cap", "polish_chunk_chars", "input_cap_chars",
                       "ref_budget_chars", "tone_ref_chars", "style_sample_cap", "context_chars", "polish_context_chars",
                       "ref_min_chars", "ref_max_count", "max_blocks_per_chunk")


# ----------------------------------------------------------------
# 基盤
# ----------------------------------------------------------------

def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    raw = {}
    if SETTINGS_PATH.exists():
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            s.update(raw)
        except Exception:
            raw = {}
    migrated = False
    if raw and not raw.get("settings_version"):
        # 旧版の画面で設定を保存した人は ref_mode:"full" が書かれている。新版の既定（budget）へ一度だけ移行し、以後の full は尊重する
        if raw.get("ref_mode") == "full":
            s["ref_mode"] = "budget"
        s["settings_version"] = 2
        migrated = True
    for key in _ZERO_MEANS_DEFAULT:
        try:
            if int(s.get(key) or 0) <= 0:
                s[key] = DEFAULT_SETTINGS[key]
        except (TypeError, ValueError):
            s[key] = DEFAULT_SETTINGS[key]
    if s.get("ref_mode") not in ("budget", "full", "excerpt"):
        s["ref_mode"] = "budget"
    if migrated:
        try:
            save_settings(s)
        except Exception:
            pass
    return s


def current_settings():
    """ジョブの中では開始時の設定（budget.Meter に固定）を使う。ジョブ外は settings.json を読む。"""
    m = budget.current()
    return m.settings if m and m.settings else load_settings()


def setting_int(key):
    return int(current_settings().get(key) or DEFAULT_SETTINGS.get(key) or 0)

def save_settings(s):
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def run_claude(prompt, force_real=False):
    settings = current_settings()
    engine = settings.get("engine", "claude")
    phase = budget.phase_of(prompt)
    meter = budget.current()
    if meter and len(prompt) > int(settings.get("input_cap_chars") or 0) > 0:
        meter.note_trim(phase, [{"title": "入力上限超過", "from": len(prompt), "to": len(prompt)}])
    if engine == "mock" and not force_real:
        r = None
        n_ng = int(settings.get("mock_audit_ng") or 0)
        if phase == "audit" and n_ng > 0 and not (meter and (meter.by_phase.get("audit") or {}).get("calls")):
            r = prompts.mock_audit_with_ng(prompt, n_ng)  # 最初の検品だけ不合格を返し、部分修正→差分再検品の経路を踏ませる
        if r is None:
            r = ext.mock_response_ext(prompt)
        r = r if r is not None else prompts.mock_response(prompt)
        if meter:
            meter.record(phase, prompt, r)
        return r
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code（claudeコマンド）が見つかりません。Claude Codeをインストールしてログインしてから再度お試しください。")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE") and k not in ("AI_AGENT", "BAGGAGE")}
    cmd = [exe, "-p", "--output-format", "json"]
    if (settings.get("model") or "").strip():
        cmd += ["--model", settings["model"].strip()]
    proc = subprocess.run(cmd, input=prompt, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=int(settings.get("timeout_sec", 900)), env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"claude実行エラー: {(proc.stderr or proc.stdout or '')[-500:]}")
    data = parse_cli_json(proc.stdout or "")
    if data.get("is_error"):
        raise RuntimeError(f"claude応答エラー: {str(data.get('result'))[:500]}")
    result = data.get("result", "")
    if meter:
        try:
            # CLI の JSON には usage（input/cache_read/cache_creation/output）と total_cost_usd が入る。取れなければ概算
            meter.record(phase, prompt, result, usage=data.get("usage") if isinstance(data.get("usage"), dict) else None,
                         cost=data.get("total_cost_usd"))
        except Exception:
            pass
    return result


def parse_cli_json(out):
    """claude -p --output-format json の標準出力を読む。前置きの警告行、配列（stream 形式）、行ごとの JSON にも耐える。
    type=="result" の要素を返す。"""
    try:
        data = json.loads(out)
    except ValueError:
        data = None
        for open_, close in (("[", "]"), ("{", "}")):
            i, j = out.find(open_), out.rfind(close)
            if i >= 0 and j > i:
                try:
                    data = json.loads(out[i:j + 1])
                    break
                except ValueError:
                    continue
        if data is None:
            found = None
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict) and (found is None or d.get("type") == "result"):
                        found = d
            if found is None:
                raise RuntimeError(f"claude応答を読めません: {out[-300:]}")
            data = found
    if isinstance(data, list):
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), data[-1] if data else {})
    if not isinstance(data, dict):
        raise RuntimeError("claude応答の形式が想定外です")
    return data


def extract_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"JSONが見つかりません: {text[:200]}")
    return json.loads(text[start:end + 1])


def normalize(t):
    return re.sub(r"\s+", "", t or "")


def count_chars(t):
    return len(normalize(t))


def find_common_runs(new, ref, n=20):
    hits, ref_grams = [], {}
    for i in range(len(ref) - n + 1):
        ref_grams.setdefault(ref[i:i + n], i)
    i = 0
    while i <= len(new) - n:
        g = new[i:i + n]
        if g in ref_grams:
            j, k = ref_grams[g], n
            while i + k < len(new) and j + k < len(ref) and new[i + k] == ref[j + k]:
                k += 1
            hits.append(new[i:i + k])
            i += k
        else:
            i += 1
    return hits


def copycheck(script_text_, ref_texts, teikei_list):
    new = normalize(script_text_)
    teikei_norm = [normalize(t) for t in (teikei_list or []) if t and len(normalize(t)) >= 10]
    allowed, violations, seen = [], [], set()
    for ref in ref_texts:
        for hit in find_common_runs(new, normalize(ref), 20):
            if hit in seen:
                continue
            seen.add(hit)
            ok = any((hit in t) or (t in hit) for t in teikei_norm)
            (allowed if ok else violations).append(hit)
    return {"allowed": allowed, "violations": violations,
            "summary": f"一致{len(allowed) + len(violations)}件（定型句{len(allowed)}件・要修正{len(violations)}件）"}


def md_to_html(md):
    def inline(s):
        s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)

    out, i, lines = [], 0, md.split("\n")
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue
        if line.startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i].replace("&", "&amp;").replace("<", "&lt;"))
                i += 1
            out.append("<pre>" + "\n".join(code) + "</pre>")
            i += 1
            continue
        if line.startswith("#### "):
            out.append(f"<h4>{inline(line[5:])}</h4>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.startswith(">"):
            q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(inline(lines[i].lstrip("> ").rstrip()))
                i += 1
            out.append("<blockquote>" + "<br>".join(q) + "</blockquote>")
            continue
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            html = "<table>"
            for r_idx, r in enumerate(rows):
                tag = "th" if r_idx == 0 else "td"
                html += "<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in r) + "</tr>"
            out.append(html + "</table>")
            continue
        elif re.match(r"^\s*[-*] ", line):
            items = []
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                item_text = re.sub(r"^\s*[-*] ", "", lines[i])
                items.append(f"<li>{inline(item_text)}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif re.match(r"^\d+\. ", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                item_text = re.sub(r"^\d+\. ", "", lines[i])
                items.append(f"<li>{inline(item_text)}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        else:
            out.append(f"<p>{inline(line)}</p>")
        i += 1
    return "\n".join(out)


# ----------------------------------------------------------------
# プロジェクト・カルテ
# ----------------------------------------------------------------

def project_path(pid):
    return PROJECTS_DIR / pid / "project.json"


def load_project(pid):
    p = project_path(pid)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save_project(prj):
    (PROJECTS_DIR / prj["id"]).mkdir(parents=True, exist_ok=True)
    project_path(prj["id"]).write_text(json.dumps(prj, ensure_ascii=False, indent=2), encoding="utf-8")


def list_projects():
    out = []
    if PROJECTS_DIR.exists():
        for d in sorted(PROJECTS_DIR.iterdir(), reverse=True):
            p = d / "project.json"
            if p.exists():
                try:
                    prj = json.loads(p.read_text(encoding="utf-8"))
                    out.append({"id": prj["id"], "name": prj["name"], "created": prj.get("created", ""),
                                "step": prj.get("step", 1), "scripts": len(prj.get("scripts", [])),
                                "karte": prj.get("karte_name", ""), "usage": usage_total(prj)})
                except Exception:
                    continue
    return out


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", unicodedata.normalize("NFC", name or "")).strip() or "no-name"


def resolve_path(directory, name):
    """directory/name を、ファイル名の正規化（NFC/NFD）の違いを無視して探す。見つからなければ素直なパスを返す。
    （macOS で作った zip のファイル名は NFD で、Windows/Linux では NFC の名前と一致しない）"""
    direct = Path(directory) / name
    if direct.exists():
        return direct
    want = unicodedata.normalize("NFC", name)
    try:
        for q in Path(directory).iterdir():
            if unicodedata.normalize("NFC", q.name) == want:
                return q
    except OSError:
        pass
    return direct


def normalize_filenames():
    """knowledge/ と manual/ のファイル名を NFC に寄せる（できなければそのまま。読む側は resolve_path で両方に対応）。"""
    for d in (BASE / "knowledge", BASE / "manual"):
        try:
            for q in d.iterdir():
                nfc = unicodedata.normalize("NFC", q.name)
                if nfc != q.name and not (d / nfc).exists():
                    try:
                        q.rename(d / nfc)
                    except OSError:
                        pass
        except OSError:
            pass


def karte_path(name):
    return resolve_path(KARTE_DIR, f"{safe_name(name)}.json")


def load_karte(name):
    p = karte_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def new_karte(name):
    return {"name": safe_name(name), "history": [], "cross": None, "scripts_analyzed": 0,
            "updated": date.today().isoformat(), "concept_sheet": None, "character_sheet": None,
            "world_sheet": None, "house_style": [], "success_scripts": [], "patterns": [], "refs_norm": [], "style_samples": []}


def save_karte(karte):
    KARTE_DIR.mkdir(exist_ok=True)
    karte["updated"] = date.today().isoformat()
    karte_path(karte["name"]).write_text(json.dumps(karte, ensure_ascii=False, indent=2), encoding="utf-8")


def karte_summary(k):
    return {"name": k.get("name"), "file": karte_path(k.get("name", "")).name,
            "scripts_analyzed": k.get("scripts_analyzed", 0), "history": len(k.get("history", [])),
            "updated": k.get("updated", ""), "has_cross": bool(k.get("cross")),
            "has_concept": bool(k.get("concept_sheet")), "has_character": bool(k.get("character_sheet")),
            "has_world": bool(k.get("world_sheet")),
            "rules": len(k.get("house_style", [])), "success": len(k.get("success_scripts", []))}


def list_karte():
    out = []
    if KARTE_DIR.exists():
        for f in sorted(KARTE_DIR.glob("*.json")):
            try:
                out.append(karte_summary(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                continue
    return out


def project_karte(prj):
    """プロジェクトに紐づくカルテ（無ければプロジェクト名で新規）。"""
    name = prj.get("karte_name") or safe_name(prj["name"])
    k = load_karte(name) or new_karte(name)
    if prj.get("cross") and not k.get("cross"):
        k["cross"] = prj["cross"]
    return k


def concept_with_pack(prj, karte):
    """設計図・執筆に渡す構想＝ユーザー入力＋チャンネル設計（コンセプト・キャラ・固有ルール）。"""
    c = dict(prj.get("concept") or {})
    c.update(ext.channel_pack(karte))
    return c


def script_text(s):
    return "\n\n".join(b.get("text", "") for b in (s or {}).get("script_blocks", []))


def style_samples_text(karte, frac=None):
    """修正に渡す文体見本。最新の成功台本（オーナー添削後の最終文）1本から、対象ブロックの相対位置 frac に
    対応する連続窓（合計 style_sample_cap 字。frac 無しなら冒頭・中盤・末尾）。
    （旧：成功台本を全本・全文。カルテが育つほど修正1回の入力が増え続けていた）"""
    samples = karte.get("style_samples") or []
    if not samples:
        return ""
    n = setting_int("style_sample_cap")
    piece = budget.excerpt_at(samples[-1], frac, n) if frac is not None else budget.excerpt(samples[-1], n, "hmt")
    return "◆最新の成功台本（抜粋）\n" + piece


def voice_card(prj, karte):
    """このチャンネルの「声」を、横断分析・個別分析・キャラ設定から機械的に畳んだ1枚（上限1,200字。AIは呼ばない）。
    参考台本を抜粋で渡す代わりに、口癖・文末・一文の長さ・冒頭の手法・CTAの温度を明示して、執筆・修正・口調照合に添える。
    分析を飛ばした新作（カルテから）では、分析時にカルテへ保存した声カードを使う。"""
    cross = karte.get("cross") or prj.get("cross") or {}
    analyses = [a for a in (prj.get("analyses") or []) if isinstance(a, dict) and a.get("kind") != "fail"]
    if not analyses and karte.get("voice_card"):
        return karte["voice_card"]
    lines = []
    st = cross.get("style")
    if st:
        lines.append("話者の特徴（横断分析）：" + (st if isinstance(st, str) else json.dumps(st, ensure_ascii=False)))
    if cross.get("teikei"):
        lines.append("定型句（同一チャンネルの新作でのみ踏襲可）：" + "／".join(str(x) for x in cross["teikei"][:6]))

    def uniq(xs):
        out = []
        for x in xs:
            if x and x not in out:
                out.append(x)
        return out
    phrases, sentences, hooks, ctas, endings = [], [], [], [], []
    for a in analyses:
        sty = a.get("style") or {}
        phrases += [x for x in (sty.get("phrases") or []) if isinstance(x, str)]
        if sty.get("sentence"):
            sentences.append(str(sty["sentence"]))
        if sty.get("ending"):
            endings.append(str(sty["ending"]))
        if (a.get("hook") or {}).get("method"):
            hooks.append(str(a["hook"]["method"]))
        if (a.get("cta") or {}).get("tone"):
            ctas.append(str(a["cta"]["tone"]))
    if phrases:
        lines.append("口癖・頻出フレーズ：" + "／".join(uniq(phrases)[:12]))
    if endings:
        lines.append("文末：" + "／".join(uniq(endings)[:3]))
    if sentences:
        lines.append("一文の長さ・リズム：" + "／".join(uniq(sentences)[:3]))
    if hooks:
        lines.append("冒頭の手法：" + "／".join(uniq(hooks)[:3]))
    if ctas:
        lines.append("CTAの温度・話法：" + "／".join(uniq(ctas)[:3]))
    sp = (karte.get("character_sheet") or {}).get("speech") or {}
    if sp:
        lines.append("キャラの話し方：" + json.dumps(sp, ensure_ascii=False))
    text = "\n".join("・" + x for x in lines)
    return budget.excerpt(text, 1200, "head") if text else ""


def with_voice(prj, karte, body, label="参考"):
    """声カードを見本テキストの前に添える。"""
    card = voice_card(prj, karte)
    head = ("◆このチャンネルの声（分析から機械的にまとめたもの。言い方の型であり、文のコピー元ではない）\n" + card + "\n\n") if card else ""
    return head + (body or "")


def block_summaries(blocks, chars=60):
    """全ブロックの要点一覧。執筆時の recap（出した主張・具体例・数字・予告）があればそれ、無ければ先頭 chars 字。"""
    out = []
    for b in blocks:
        rc = (b.get("recap") or "").strip()
        out.append({"name": b.get("name", ""), "要点": rc if rc else normalize(b.get("text", ""))[:chars] + "…"})
    return out


CROSS_BLOCK_WORDS = ("重複", "定義", "予告", "回収", "順番", "順序", "矛盾", "プロット", "時系列", "繰り返", "先取り")


def neighbor_context(blocks, targets, n=None, wide=False):
    """部分修正に渡す文脈＝対象の直前ブロックの末尾・直後ブロックの冒頭（各 n 字）＋全ブロックの要点。
    wide=True（重複・定義前使用・予告→回収・プロット順など、離れたブロックに関わる指摘があるとき）は、
    対象以外の全ブロックの冒頭300字も添える。対象以外の全文は渡さない。"""
    n = n or setting_int("context_chars")
    names = [b.get("name") for b in blocks]
    picked, out = set(), []
    for i, b in enumerate(blocks):
        if b.get("name") not in targets:
            continue
        if i > 0 and names[i - 1] not in targets and names[i - 1] not in picked:
            picked.add(names[i - 1])
            out.append({"name": names[i - 1], "位置": "直前のブロック（末尾）", "text": budget.excerpt(blocks[i - 1].get("text", ""), n, "tail")})
        if i + 1 < len(blocks) and names[i + 1] not in targets and names[i + 1] not in picked:
            picked.add(names[i + 1])
            out.append({"name": names[i + 1], "位置": "直後のブロック（冒頭）", "text": budget.excerpt(blocks[i + 1].get("text", ""), n, "head")})
    ctx = {"隣接ブロック": out, "全ブロックの要点（順番どおり）": block_summaries(blocks)}
    if wide:
        ctx["他のブロックの冒頭300字（重複・定義・予告の照合用。書き直さない）"] = [
            {"name": b.get("name"), "text": budget.excerpt(b.get("text", ""), 300, "head")}
            for b in blocks if b.get("name") not in targets and b.get("name") not in picked]
    return ctx


def locate_blocks(blocks, item):
    """修正リストの1項目がどのブロックのものかを特定して、ブロック名の集合を返す。
    優先順位：機械照合済みの引用（見つかった全ブロック）→ where（ブロック名の部分一致）→ 自由文の2-gram類似（確信できる時だけ）。
    冒頭30秒の項目は、レビューが見た先頭ブロック（先頭が600字未満なら2番目も）を常に含める
    （旧：where を先に見ていたため、60字の定型挨拶が先頭だと挨拶だけ書き直してフック本体が変わらなかった）。"""
    names = [b.get("name") for b in blocks]
    hits = set()
    q = normalize(item.get("quote", ""))
    if len(q) >= 8:
        key = q[:20]
        hits |= {b.get("name") for b in blocks if key in normalize(b.get("text", ""))}
    where = str(item.get("where") or "").strip()
    if not hits and where:
        hits |= {n_ for n_ in names if n_ and (n_ == where or n_ in where or where in n_)}
    if not hits:
        t = normalize(f"{item.get('item', '')}{item.get('why', '')}")
        if len(t) >= 10:
            grams = {t[i:i + 2] for i in range(len(t) - 1)}
            scored = []
            for b in blocks:
                bt = normalize(b.get("text", ""))
                if not bt:
                    continue
                bg = {bt[i:i + 2] for i in range(len(bt) - 1)}
                scored.append((len(grams & bg) / max(1, len(grams)), b.get("name")))
            scored.sort(reverse=True)
            if scored and scored[0][0] >= 0.45 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
                hits.add(scored[0][1])
    if item.get("source") == "hook" and blocks:
        hits.add(names[0])
        if len(blocks) > 1 and len(blocks[0].get("text", "")) < 600:
            hits.add(names[1])
    return {h for h in hits if h}


def is_cross_block(item):
    text = f"{item.get('item', '')}{item.get('why', '')}{item.get('label', '')}"
    return any(w in text for w in CROSS_BLOCK_WORDS)


def changed_block_names(before, blocks):
    """before（名前→本文）と比べて本文が変わったブロック名の集合。"""
    return {b.get("name") for b in blocks if before.get(b.get("name")) != b.get("text", "")}


def shrink_to_cap(build, parts, cap):
    """build(dict) でプロンプトを組み、cap 字を超えたら parts（低優先→高優先の順の [名前, 本文, mode, 最低字数]）を
    順に縮めて組み直す。文字列操作だけなので何度組み直しても AI は呼ばない。
    仕様（削らない部分）だけで上限を超えるときは、参照を最低字数まで縮めた上で警告を記録する（黙って参照を0にはしない）。
    返り値 (prompt, 削った内訳)。"""
    vals = {name: text for name, text, _, _ in parts}
    prompt = build(vals)
    trims = []
    if not cap or cap <= 0:
        return prompt, trims
    for name, text, mode, floor in parts:
        over = len(prompt) - cap
        if over <= 0:
            break
        room = len(vals[name]) - floor
        if room <= 0:
            continue
        new_len = len(vals[name]) - min(room, over)
        before = len(vals[name])
        vals[name] = budget.excerpt(vals[name], new_len, mode)
        trims.append({"title": name, "from": before, "to": len(vals[name])})
        prompt = build(vals)
    m = budget.current()
    if m:
        if trims:
            m.note_trim(budget.phase_of(prompt), trims)
        if len(prompt) > cap:
            m.warn(f"{budget.phase_of(prompt)}：入力{len(prompt):,}字が上限{cap:,}字を超えています（設計書・素材など削らない部分だけで超過。"
                   f"設定の上限を上げるか、素材を絞ってください）")
    return prompt, trims


# ----------------------------------------------------------------
# ジョブ
# ----------------------------------------------------------------

def start_job(fn, *args):
    job_id = uuid.uuid4().hex[:12]
    with job_lock:
        jobs[job_id] = {"status": "running", "progress": "開始しています…", "result": None, "error": None}

    def runner():
        meter = budget.start(fn.__name__, load_settings())
        try:
            result = fn(job_id, *args)
            summary = meter.summary()
            pid = args[0] if args and isinstance(args[0], str) else None
            if pid and meter.calls:
                record_usage(pid, fn.__name__, summary)
            with job_lock:
                jobs[job_id].update(status="done", result=result, usage=summary)
        except Exception as e:
            with job_lock:
                jobs[job_id].update(status="error", error=str(e), usage=meter.summary() if meter.calls else None)
        finally:
            budget.stop()

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def record_usage(pid, job_name, summary):
    """ジョブ1回分のAI利用（呼び出し回数・トークン・費用）をプロジェクトに積む。画面の累計表示に使う。"""
    try:
        prj = load_project(pid)
        if not prj:
            return
        log = prj.setdefault("token_usage", [])
        log.append({"date": date.today().isoformat(), "job": job_name, "calls": summary.get("calls", 0),
                    "in_tokens": summary.get("in_tokens", 0), "out_tokens": summary.get("out_tokens", 0),
                    "measured": summary.get("measured", 0), "cost_usd": summary.get("cost_usd")})
        del log[:-200]
        save_project(prj)
    except Exception:
        pass


def usage_now():
    """実行中ジョブの消費（結果に明示的に添える用。設計書など保存されるデータには混ぜない）。"""
    m = budget.current()
    return m.summary() if m else None


def usage_total(prj):
    log = prj.get("token_usage") or []
    return {"calls": sum(x.get("calls", 0) for x in log), "in_tokens": sum(x.get("in_tokens", 0) for x in log),
            "out_tokens": sum(x.get("out_tokens", 0) for x in log),
            "cost_usd": round(sum(x.get("cost_usd") or 0 for x in log), 4) or None}


def set_progress(job_id, text):
    with job_lock:
        if job_id in jobs:
            jobs[job_id]["progress"] = text


def job_analyze(job_id, pid):
    prj = load_project(pid)
    scripts = prj["scripts"]
    analyses = []
    for i, s in enumerate(scripts):
        tag = "失敗例" if s.get("kind") == "fail" else "成功例"
        set_progress(job_id, f"台本 {i + 1}/{len(scripts)}（{tag}）「{s['title']}」を分析中…（1本あたり1〜3分）")
        a = extract_json(run_claude(prompts.p1_analyze(s["title"], s["text"], count_chars(s["text"]))))
        a["kind"] = s.get("kind", "success")
        analyses.append(a)
    set_progress(job_id, "横断分析（型と自由度の抽出）を実行中…")
    ok = [a for a in analyses if a.get("kind") != "fail"] or analyses
    cross = extract_json(run_claude(prompts.p2_cross(json.dumps(ok, ensure_ascii=False), len(ok))))
    fails = [a for a in analyses if a.get("kind") == "fail"]
    if fails:
        cross.setdefault("notes", []).append(
            "失敗例として入れた台本：" + "／".join(a.get("title", "") for a in fails)
            + "（これらの構成・話法は型として採用していません）")
    prj.update(analyses=analyses, cross=cross, step=2)
    k = project_karte(prj)
    k["cross"] = cross
    k["scripts_analyzed"] = len(scripts)
    k["voice_card"] = voice_card(prj, k)
    k["refs_norm"] = [normalize(s["text"])[:20000] for s in scripts if s.get("kind") != "fail"]
    save_karte(k)
    prj["karte_name"] = k["name"]
    save_project(prj)
    return {"analyses": analyses, "cross": cross, "karte": karte_summary(k)}


def job_diagnose(job_id, pid):
    prj = load_project(pid)
    k = project_karte(prj)
    set_progress(job_id, "ジャンルと必要な準備を診断中…")
    brief = prj.get("analyses") or []
    diag = extract_json(run_claude(ext.p27_diagnose(
        json.dumps(prj["cross"], ensure_ascii=False), json.dumps(brief, ensure_ascii=False),
        len(prj.get("scripts") or []), prj.get("intent", ""))))
    prj["diagnosis"] = diag
    save_project(prj)
    k["diagnosis"] = diag
    save_karte(k)
    return diag


def job_setup_all(job_id, pid, memos):
    """診断の提案どおりに、コンセプト→キャラ→（必要なら）世界設定を一括で作る。"""
    out = {}
    out["concept"] = job_concept(job_id, pid, memos.get("concept", ""))
    out["character"] = job_character(job_id, pid, memos.get("character", ""))
    if memos.get("world"):
        out["world"] = job_world(job_id, pid, memos.get("world", ""))
    return out


def job_reconsider(job_id, pid, theme):
    """STEP 4 でテーマが設計と合わないとき、コンセプトの柱と世界設定のプロットだけをテーマ前提で更新する。
    既存の設計書を土台に渡すので、出口・キャラ・相関図は保たれる。"""
    prj = load_project(pid)
    prj["intent"] = theme
    save_project(prj)
    k = project_karte(prj)
    out = {}
    memo = (k.get("concept_memo") or "") + f"\n\n今回のテーマ「{theme}」を柱と前出しに反映する。出口・集める層は変えない。"
    out["concept"] = job_concept(job_id, pid, memo)
    if k.get("world_sheet"):
        wmemo = (k.get("world_memo") or "") + f"\n\nプロットを今回のテーマ「{theme}」の1本として組み直す。登場人物・相関図・時代背景は変えない。"
        out["world"] = job_world(job_id, pid, wmemo)
    return out


def job_concept(job_id, pid, memo):
    prj = load_project(pid)
    k = project_karte(prj)
    set_progress(job_id, "チャンネルコンセプトを設計中…（出口逆算・6軸採点）")
    existing = json.dumps(k["concept_sheet"], ensure_ascii=False) if k.get("concept_sheet") else ""
    sheet = extract_json(run_claude(ext.p20_concept(json.dumps(prj["cross"], ensure_ascii=False), memo, existing,
                                                    prj.get("intent", ""))))
    k["concept_sheet"] = sheet
    k["concept_memo"] = memo
    save_karte(k)
    prj["karte_name"] = k["name"]
    prj["step"] = max(prj.get("step", 1), 3)
    save_project(prj)
    return sheet


def job_character(job_id, pid, memo):
    prj = load_project(pid)
    k = project_karte(prj)
    set_progress(job_id, "キャラクターを設計中…（人格・背景・沸点・口調）")
    existing = json.dumps(k["character_sheet"], ensure_ascii=False) if k.get("character_sheet") else ""
    sheet = extract_json(run_claude(ext.p21_character(
        json.dumps(prj["cross"], ensure_ascii=False),
        json.dumps(k.get("concept_sheet") or {}, ensure_ascii=False), memo, existing, prj.get("intent", ""))))
    k["character_sheet"] = sheet
    k["character_memo"] = memo
    save_karte(k)
    prj["karte_name"] = k["name"]
    prj["step"] = max(prj.get("step", 1), 3)
    save_project(prj)
    return sheet


def job_world(job_id, pid, memo):
    prj = load_project(pid)
    k = project_karte(prj)
    set_progress(job_id, "世界設定を作成中…（時代背景・経緯・登場人物・相関図・プロット）")
    existing = json.dumps(k["world_sheet"], ensure_ascii=False) if k.get("world_sheet") else ""
    sheet = extract_json(run_claude(ext.p26_world(
        json.dumps(prj["cross"], ensure_ascii=False),
        json.dumps(k.get("concept_sheet") or {}, ensure_ascii=False),
        json.dumps(k.get("character_sheet") or {}, ensure_ascii=False), memo, existing, prj.get("intent", ""))))
    k["world_sheet"] = sheet
    k["world_memo"] = memo
    save_karte(k)
    prj["karte_name"] = k["name"]
    prj["step"] = max(prj.get("step", 1), 3)
    save_project(prj)
    return sheet


def job_propose(job_id, pid, memo):
    prj = load_project(pid)
    k = project_karte(prj)
    set_progress(job_id, "この型とコンセプトに合う企画を考えています…")
    if k.get("concept_sheet"):
        memo += "\n\n# チャンネルコンセプト（柱と前に出す1つ）\n" + json.dumps(
            k["concept_sheet"], ensure_ascii=False)
    return extract_json(run_claude(prompts.p3_propose(json.dumps(prj["cross"], ensure_ascii=False), memo)))


def run_bp_audit(job_id, prj, k):
    """設計図の検品。引用を設計図本文と機械照合し、引用できない×は捨てる。任意でJev。"""
    set_progress(job_id, "設計図を検品中…（ネタ・ターゲット・コンセプト・キャラ・プロットの反映）")
    pack = ext.channel_pack(k)
    raw = extract_json(run_claude(ext.p28_blueprint_audit(
        json.dumps(prj["blueprint"], ensure_ascii=False), prj.get("intent", ""),
        json.dumps(prj.get("concept") or {}, ensure_ascii=False), json.dumps(pack, ensure_ascii=False))))
    body = normalize(json.dumps(prj["blueprint"], ensure_ascii=False))
    rows = []
    for a in raw.get("audit", []):
        q = normalize(str(a.get("quote", "")))
        verified = len(q) >= 6 and q[:30] in body
        res = "×" if "×" in str(a.get("result", "")) else "○"
        if res == "×" and not verified and "未設計" not in str(a.get("why", "")):
            continue
        rows.append({"item": a.get("item", ""), "result": res, "quote": str(a.get("quote", ""))[:60] if verified else "",
                     "why": a.get("why", ""), "fix": a.get("fix", "")})
    out = {"audit": rows, "reflection_score": raw.get("reflection_score"), "summary": raw.get("summary", ""),
           "ng": sum(1 for r in rows if r["result"] == "×"),
           "undesigned": not (k.get("concept_sheet") and k.get("character_sheet"))}
    if (current_settings().get("jev") or {}).get("enabled"):
        set_progress(job_id, "設計図の外部採点（Jev）を実行中…")
        out["jev"] = run_jev_rubric("blueprint.json",
                                    "【ユーザーの入力】\n" + json.dumps({"intent": prj.get("intent"), **(prj.get("concept") or {})}, ensure_ascii=False)
                                    + "\n\n【チャンネル設計】\n" + json.dumps(pack, ensure_ascii=False)
                                    + "\n\n【設計図】\n" + json.dumps(prj["blueprint"], ensure_ascii=False))
        if out["jev"].get("rows"):
            out["ng"] += sum(1 for r in out["jev"]["rows"] if r.get("verdict") == "×")
    return out


def run_jev_rubric(rubric_name, text):
    s = current_settings().get("jev") or {}
    node = shutil.which("node")
    rubric = JEV_DIR / "rubrics" / rubric_name
    if not node or not rubric.exists() or not (JEV_DIR / "node_modules").exists():
        return {"skipped": "Node または jev/node_modules または採点基準が見つかりません"}
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text[:40000])
        tmp = f.name
    try:
        proc = subprocess.run([node, str(JEV_DIR / "kenpin_json.mjs"), "--rubric", str(rubric), "--in", tmp],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(JEV_DIR))
        if proc.returncode != 0:
            return {"skipped": "Jev実行エラー: " + (proc.stderr or "")[-300:]}
        return {"rows": json.loads(proc.stdout)}
    except Exception as e:
        return {"skipped": f"Jev実行エラー: {e}"}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def job_blueprint(job_id, pid, concept, revise):
    prj = load_project(pid)
    prj["concept"] = concept
    if concept.get("theme") and not prj.get("intent"):
        prj["intent"] = concept["theme"]
    k = project_karte(prj)
    set_progress(job_id, "設計図（ブロック構成表）を作成中…")
    bp = extract_json(run_claude(prompts.p4_blueprint(
        json.dumps(prj["cross"], ensure_ascii=False),
        json.dumps(concept_with_pack(prj, k), ensure_ascii=False), revise)))
    prj.update(blueprint=bp, step=4)
    save_project(prj)
    audit = run_bp_audit(job_id, prj, k)
    fix = ext.build_bp_fix(audit)
    if fix and current_settings().get("bp_autofix", True):
        set_progress(job_id, f"設計図の不合格{audit['ng']}件を修正中…")
        bp = extract_json(run_claude(prompts.p4_blueprint(
            json.dumps(prj["cross"], ensure_ascii=False),
            json.dumps(concept_with_pack(prj, k), ensure_ascii=False), (revise + "\n\n" + fix).strip())))
        prj["blueprint"] = bp
        save_project(prj)
        audit = run_bp_audit(job_id, prj, k)
    prj["bp_audit"] = audit
    save_project(prj)
    bp["bp_audit"] = audit
    return bp


def _copyfix(job_id, prj, result):
    """機械コピーチェック（連続20文字一致）。一致を含むブロックだけを書き直す（他ブロックは触らない）。
    一致がブロック境界をまたぐ場合は隣接2ブロックの連結で探して両方を対象にする。
    どのブロックにも見つからない一致が残る時だけ、従来の全文版 p7_fixcopy に回す。
    （旧：数か所の一致のために台本全文を再生成していた）"""
    refs = [s["text"] for s in prj["scripts"] if s.get("kind") != "fail"]
    teikei = (prj.get("cross") or {}).get("teikei", [])
    set_progress(job_id, "機械コピーチェック（連続20文字一致）を実行中…")
    check = copycheck(script_text(result), refs, teikei)
    if check["violations"]:
        blocks = result.get("script_blocks", [])
        norm = [normalize(b.get("text", "")) for b in blocks]
        targets, orphan = set(), []
        for v in check["violations"]:
            found = {blocks[i].get("name") for i, t in enumerate(norm) if v in t}
            if not found:
                for i in range(len(blocks) - 1):
                    if v in norm[i] + norm[i + 1]:
                        found |= {blocks[i].get("name"), blocks[i + 1].get("name")}
            if found:
                targets |= found
            else:
                orphan.append(v)
        if targets:
            hit = [b for b in blocks if b.get("name") in targets]
            set_progress(job_id, f"コピー疑い{len(check['violations'])}件を含む{len(hit)}ブロックだけ書き直し中…")
            try:
                res = extract_json(run_claude(prompts.p7_fixcopy_partial(
                    json.dumps([{"name": b.get("name"), "text": b.get("text", "")} for b in hit], ensure_ascii=False),
                    json.dumps(check["violations"], ensure_ascii=False),
                    json.dumps(neighbor_context(blocks, targets), ensure_ascii=False))))
                got = {str(x.get("name", "")).strip(): x.get("text", "") for x in res.get("script_blocks", []) if isinstance(x, dict) and x.get("text")}
                for b in blocks:
                    hit_name = match_block_name(got, b.get("name")) if b.get("name") in targets else None
                    if hit_name:
                        b["text"] = got[hit_name]
            except Exception:
                pass
        elif orphan:
            set_progress(job_id, f"コピー疑い{len(orphan)}件の位置を特定できないため全文で書き直し中…")
            try:
                result = extract_json(run_claude(prompts.p7_fixcopy(
                    json.dumps(result, ensure_ascii=False), json.dumps(orphan, ensure_ascii=False))))
            except Exception:
                pass
        check = copycheck(script_text(result), refs, teikei)
    result["copycheck"] = check
    result["total_chars"] = count_chars(script_text(result))
    return result


def run_jev(script, character, refs="", world=None):
    """外部採点（任意）。Node・キー・設定が揃わなければ skipped を返して黙って先へ進む。"""
    s = current_settings().get("jev") or {}
    node = shutil.which("node")
    rubric = JEV_DIR / "rubrics" / s.get("rubric", "daihon.json")
    if not node or not rubric.exists() or not (JEV_DIR / "node_modules").exists():
        return {"skipped": "Node または jev/node_modules または採点基準が見つかりません"}
    text = ("【キャラクター設定】\n" + json.dumps(character or {}, ensure_ascii=False)
            + "\n\n【世界設定（時代背景・相関図・プロット）】\n" + json.dumps(world or {}, ensure_ascii=False)
            + "\n\n【参考台本の抜粋（声の見本）】\n" + (refs or "（なし）")
            + "\n\n【台本】\n" + script_text(script))
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text[:40000])
        tmp = f.name
    try:
        proc = subprocess.run([node, str(JEV_DIR / "kenpin_json.mjs"), "--rubric", str(rubric), "--in", tmp],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=180, cwd=str(JEV_DIR))
        if proc.returncode != 0:
            return {"skipped": "Jev実行エラー: " + (proc.stderr or "")[-300:]}
        return {"rows": json.loads(proc.stdout)}
    except Exception as e:
        return {"skipped": f"Jev実行エラー: {e}"}
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def run_audit(job_id, prj, karte, script, materials, changed=None, prev=None):
    """独立検品官（p13）＋冒頭30秒レビュー（p22）＋口調照合（p25）＋外部採点（任意）。引用は本文と機械照合する。
    changed（変わったブロック名の集合）と prev（前回の結果）を渡すと差分再検品：
      検品官は横断項目（重複・定義前使用・構成順）のため毎回全文を見る。
      冒頭レビューは先頭2ブロックが変わった時だけ、口調照合は変わったブロックだけ再実行し、他は前回を引き継ぐ。"""
    checklist = list((karte.get("cross") or {}).get("checklist", []))
    checklist += ext.character_checklist(karte.get("character_sheet"))
    checklist += ext.world_checklist(karte.get("world_sheet"))
    checklist += ext.house_style_checklist(karte.get("house_style"))
    bp = prj.get("blueprint") or {}
    concept = karte.get("concept_sheet") or {}
    # 固有ルールは items に全件文章化済みなので辞書を重ねて渡さない。コンセプトは検品に要る4項目だけ（6軸採点・ギャップの種は不要）
    checklist_pack = {"items": checklist,
                      "character_sheet": karte.get("character_sheet"),
                      "world_sheet": karte.get("world_sheet"),
                      "concept_sheet": {kk: concept.get(kk) for kk in ("exit_product", "promise", "pillars", "front") if concept.get(kk)} or None}
    set_progress(job_id, "検品官が型・キャラ・固有ルールで採点中…")
    raw = extract_json(run_claude(prompts.p13_audit(
        json.dumps(checklist_pack, ensure_ascii=False), json.dumps(script, ensure_ascii=False),
        prompts.blueprint_min(bp), materials)))
    body = normalize(script_text(script))
    rows = []
    for a in raw.get("audit", []):
        quote = normalize(str(a.get("quote", "")))
        verified = len(quote) >= 8 and quote[:40] in body
        result = str(a.get("result", "")).strip()
        if "×" in result and not verified:
            continue
        rows.append({"item": a.get("item", ""), "result": "×" if "×" in result else "○",
                     "where": a.get("where", ""), "quote": str(a.get("quote", ""))[:60] if verified else "",
                     "why": a.get("why", "")})
    audit = {"audit": rows, "fatal": raw.get("fatal", [])}

    blocks_ = script.get("script_blocks", [])
    cur_hash = {b.get("name"): budget.text_hash(b.get("text", "")) for b in blocks_}
    if changed is not None and prev:
        # 「変わったブロック」は、直前の修正前との差分ではなく、前回の検品が見た本文（block_hash）との差分で決める
        # （間に推敲が入っても引き継ぎ漏れが出ない）。前回のハッシュが無い／ブロック構成が違うなら全件やり直す
        ph = prev.get("block_hash") or {}
        if ph and set(ph) == set(cur_hash):
            changed = set(changed) | {n_ for n_ in cur_hash if ph.get(n_) != cur_hash[n_]}
        else:
            changed = None
    head_names = {b.get("name") for b in blocks_[:2]}
    if changed is not None and prev and prev.get("hook") and not (changed & head_names):
        hook = prev["hook"]  # 冒頭が変わっていない → 前回の採点を引き継ぐ
    else:
        set_progress(job_id, "冒頭30秒を採点中…")
        opening = blocks_[0].get("text", "") if blocks_ else script_text(script)
        if len(opening) < 600 and len(blocks_) > 1:
            opening += "\n" + blocks_[1].get("text", "")
        hook = extract_json(run_claude(ext.p22_hook_review(
            opening, json.dumps(karte.get("concept_sheet") or {}, ensure_ascii=False),
            json.dumps(karte.get("character_sheet") or {}, ensure_ascii=False))))

    tone = None
    if current_settings().get("tone_check", True) and prj.get("scripts"):
        if changed is not None and prev and prev.get("tone") is not None and not changed:
            tone = prev["tone"]
        elif changed is not None and prev and prev.get("tone") is not None:
            tone = run_tone(job_id, prj, karte, script, only_blocks=changed, prev=prev["tone"])
        else:
            tone = run_tone(job_id, prj, karte, script)

    jev = None
    if (current_settings().get("jev") or {}).get("enabled"):
        set_progress(job_id, "外部採点（Jev）を実行中…")
        jev = run_jev(script, karte.get("character_sheet"), ref_excerpts(prj, 500), karte.get("world_sheet"))
    items = build_fix_items(prj, audit, hook, tone, jev)
    ng = sum(1 for it in items if it["decision"] != "ignore")
    return {"audit": audit, "hook": hook, "jev": jev, "tone": tone, "ng": ng, "fix_items": items, "block_hash": cur_hash}


def _item_key(source, text):
    return source + ":" + normalize(text)[:60]


def build_fix_items(prj, audit, hook, tone, jev):
    """検品官・冒頭・口調・外部採点の不合格を1つの修正リストにまとめる。
    決定（ai/self/ignore）とメモは prj["fix_decisions"] に項目キーで保持し、再検品後も引き継ぐ。"""
    decisions = prj.get("fix_decisions") or {}
    items = []

    def add(source, label, item, quote, where, why, hint):
        key = _item_key(source, item + quote)
        d = decisions.get(key) or {}
        items.append({"key": key, "source": source, "label": label, "item": item, "quote": quote, "where": where,
                      "why": why, "hint": hint, "decision": d.get("decision", "ai"), "note": d.get("note", "")})

    for a in (audit or {}).get("audit", []):
        if a.get("result") == "×":
            add("audit", "検品官", a.get("item", ""), a.get("quote", ""), a.get("where", ""), a.get("why", ""), "")
    for f in (audit or {}).get("fatal", []):
        add("fatal", "重大", str(f), "", "", "公開したら事故になる問題", "")
    if hook and ext._num(hook.get("score")) < 70:
        probs = hook.get("problems") or []
        fixes = hook.get("fixes") or []
        add("hook", "冒頭30秒", f"冒頭の採点{hook.get('score')}点（70点未満）",
            (probs[0].get("quote", "") if probs else ""), (prj.get("blueprint") or {}).get("blocks", [{}])[0].get("name", "") if (prj.get("blueprint") or {}).get("blocks") else "",
            "／".join(p.get("pattern", "") for p in probs) or hook.get("structure", ""), "／".join(fixes))
    for m in (tone or {}).get("mismatches", []):
        add("tone", "口調・語彙", m.get("kind", ""), m.get("quote", ""), m.get("where", ""),
            f"参考台本の声：「{m.get('reference', '')}」", m.get("fix", ""))
    for r in ((jev or {}).get("rows") or []):
        if r.get("verdict") == "×":
            add("jev", "外部採点", r.get("label", ""), "", "", f"値{r.get('value')}（合格ライン未満）", r.get("hint", ""))
    return items


def apply_decisions(prj, decisions):
    """画面からの決定（key→{decision, note}）を保存し、fix_items に反映する。"""
    store = prj.get("fix_decisions") or {}
    for key, d in (decisions or {}).items():
        store[key] = {"decision": d.get("decision", "ai"), "note": (d.get("note") or "").strip()}
    prj["fix_decisions"] = store
    audit = prj.get("audit") or {}
    for it in audit.get("fix_items", []):
        d = store.get(it["key"])
        if d:
            it["decision"], it["note"] = d["decision"], d["note"]
    audit["ng"] = sum(1 for it in audit.get("fix_items", []) if it["decision"] != "ignore")
    prj["audit"] = audit


def _audit_loop(job_id, prj, k, rounds_override=None, polish=True):
    """検品→落ちた項目を該当ブロックだけ修正→差分再検品。回数上限は settings.audit_rounds。
    位置を特定できない指摘は自動では直さず「自分で直す」項目として残す（全文の書き直しには落とさない）。
    （旧：修正のたびに台本全文を書き直し、再検品も冒頭レビュー・口調照合を全文でやり直し、推敲も全塊やり直していた）"""
    rounds = int(current_settings().get("audit_rounds", 2)) if rounds_override is None else int(rounds_override)
    materials = prj.get("materials", "")
    log = []
    changed, prev = None, None
    for r in range(rounds + 1):
        res = run_audit(job_id, prj, k, prj["script"], materials, changed=changed, prev=prev)
        log.append({"round": r, "ng": res["ng"], "hook_score": res["hook"].get("score"),
                    "fatal": len(res["audit"]["fatal"]), "changed": sorted(changed) if changed is not None else None})
        prj["audit"] = res
        prj["audit_log"] = log
        save_project(prj)
        if res["ng"] == 0 or r == rounds:
            break
        active = [it for it in res.get("fix_items", []) if it["decision"] == "ai"]
        if not active:
            break
        set_progress(job_id, f"不合格{res['ng']}件を該当ブロックだけ修正中…（{r + 1}/{rounds}回目）")
        before = {b.get("name"): b.get("text", "") for b in prj["script"].get("script_blocks", [])}
        out = revise_targets(job_id, prj, k, active, extra="")
        if out.get("note"):
            log[-1]["note"] = out["note"]
        if not out.get("targets"):
            break
        fix_lengths(job_id, prj, k)
        save_project(prj)
        changed = changed_block_names(before, prj["script"].get("script_blocks", []))
        prev = res
    if polish and current_settings().get("auto_polish", True):
        run_polish(job_id, prj, k, only_changed=True)
    prj["original_script_text"] = script_text(prj["script"])
    save_project(prj)
    return {"script": prj["script"], "audit": prj["audit"], "audit_log": log, "polish": prj.get("polish"), "usage": usage_now()}


def parse_extra(extra, blocks):
    """自由記述の修正指示を行ごとに見て、「ブロック名：指示」の形（ブロック名は部分一致）なら対象ブロックに紐づける。
    形に合わない行は全体への指示として返す。"""
    names = [b.get("name") for b in blocks]
    local, global_lines = {}, []
    for line in (extra or "").splitlines():
        line = line.strip().lstrip("・-　 ")
        if not line:
            continue
        m = re.match(r"^(.{1,40}?)\s*[：:]\s*(.+)$", line)
        hit = None
        if m:
            key = m.group(1).strip()
            hit = next((n_ for n_ in names if n_ and (n_ == key or key in n_ or n_ in key)), None)
        if hit:
            local.setdefault(hit, []).append(m.group(2).strip())
        else:
            global_lines.append(line)
    return local, global_lines


def revise_targets(job_id, prj, k, items, extra=""):
    """AIに直させる項目を、該当ブロック単位で書き直す。合格ブロックは触らない。
    位置を特定できない項目は「自分で直す（位置不明）」に落として修正リストに残す。
    自由記述（extra）は「ブロック名：指示」の行なら部分修正に乗せ、形に合わない行がある時だけ全体の書き直しにする。
    返り値 {"targets": 書き直したブロック名の集合, "unlocated": 位置不明の項目, "note": 画面向けの一言}。"""
    script = prj["script"]
    blocks = script.get("script_blocks", [])
    rows = {b.get("name"): b for b in (prj.get("blueprint") or {}).get("blocks", []) if isinstance(b, dict)}
    targets, unlocated, wide = set(), [], False
    lines = ["検品で不合格になった次の項目だけを直してください。それ以外の文は変えないでください。", ""]
    n = 0
    for it in items:
        hits = locate_blocks(blocks, it)
        if not hits:
            unlocated.append(it)
            continue
        n += 1
        targets |= hits
        wide = wide or is_cross_block(it) or len(hits) > 1
        lines.append(f"{n}. 【{it.get('label', '')}：{it.get('item', '')}】 ブロック「{'」「'.join(sorted(hits))}」")
        if it.get("quote"):
            lines.append(f"   該当箇所：「{it['quote']}」")
        if it.get("why"):
            lines.append(f"   問題：{it['why']}")
        if it.get("hint"):
            lines.append(f"   直し方：{it['hint']}")
        if it.get("note"):
            lines.append(f"   オーナーの指示：{it['note']}")
    local, global_lines = parse_extra(extra, blocks)
    for name_, instrs in local.items():
        n += 1
        targets.add(name_)
        lines.append(f"{n}. 【オーナーの指示】 ブロック「{name_}」：" + "／".join(instrs))
    note = ""
    if unlocated:
        store = prj.setdefault("fix_decisions", {})
        for it in unlocated:
            store[it["key"]] = {"decision": "self", "note": ((it.get("note") or "") + " 位置を特定できなかったため自分で直す項目へ").strip()}
            it["decision"] = "self"
        note = f"位置を特定できない指摘{len(unlocated)}件は「自分で直す」に移しました"
    if not targets and not global_lines:
        return {"targets": set(), "unlocated": unlocated, "note": note or "直す対象のブロックを特定できませんでした"}
    rules = k.get("house_style") or []
    if rules:
        lines += ["", "# このチャンネルの固有ルール（必ず守る）"] + ["- " + (r.get("rule") if isinstance(r, dict) else str(r)) for r in rules]
    cap = setting_int("input_cap_chars")
    names_all = [b.get("name") for b in blocks]
    if not global_lines:
        set_progress(job_id, f"該当ブロック{len(targets)}件だけを書き直し中…（他のブロックは触りません）")
        tj = [{"name": b.get("name"), "text": b.get("text", ""),
               "目安字数": int(ext._num((rows.get(b.get("name")) or {}).get("chars")) or 0) or None} for b in blocks if b.get("name") in targets]
        lines += ["", "# 各ブロックの字数は設計図の目安±20%に収める（上の対象ブロックの「目安字数」）"]
        instruction = "\n".join(lines)
        bp_json = json.dumps(prj.get("blueprint") or {}, ensure_ascii=False)
        ctx = json.dumps(neighbor_context(blocks, targets, wide=wide), ensure_ascii=False)
        idx = [names_all.index(t) for t in targets if t in names_all]
        frac = (min(idx) + 0.5) / max(1, len(blocks)) if idx else None

        def build(v):
            return prompts.p6_partial(prompts._blueprint_for_writer(bp_json, focus=targets), json.dumps(tj, ensure_ascii=False), ctx,
                                      instruction, prj.get("materials", ""), v["samples"])
        prompt, _ = shrink_to_cap(build, [["samples", with_voice(prj, k, style_samples_text(k, frac)), "hmt", 800]], cap)
        res = extract_json(run_claude(prompt))
        got = {str(x.get("name", "")).strip(): x.get("text") for x in res.get("script_blocks", []) if isinstance(x, dict) and x.get("text")}
        for b in blocks:
            if b.get("name") in targets:
                hit = match_block_name(got, b.get("name"))
                if hit:
                    b["text"] = got[hit]
        script["total_chars"] = count_chars(script_text(script))
        prj["script"] = _copyfix(job_id, prj, script)
        return {"targets": targets, "unlocated": unlocated, "note": note}
    lines += ["", "# その他の修正（オーナーの自由記述・全体に適用）"] + global_lines
    instruction = "\n".join(lines)
    set_progress(job_id, "台本を修正中…（合格箇所は変えない指示つき・全体の書き直し）")

    def build_full(v):
        return prompts.p6_revise(json.dumps(prj["cross"], ensure_ascii=False), json.dumps(prj.get("blueprint") or {}, ensure_ascii=False),
                                 json.dumps(script, ensure_ascii=False), instruction, prj.get("materials", ""), v["samples"])
    prompt, _ = shrink_to_cap(build_full, [["samples", with_voice(prj, k, style_samples_text(k)), "hmt", 800]], cap)
    fixed = extract_json(run_claude(prompt))
    prj["script"] = _copyfix(job_id, prj, fixed)
    return {"targets": set(names_all), "unlocated": unlocated, "note": (note + "／" if note else "") + "自由記述があったため全体を書き直しました"}


def write_in_blocks(job_id, prj, k, materials):
    """設計図のブロックを約 write_chunk_chars 字（最大 max_blocks_per_chunk ブロック）ずつの塊に分け、順に書く。
    渡すもの：参考台本は今回書く位置に対応する抜粋＋声カード／設計図は対象と前後だけ中身つき（他は要点）／
    直前までの本文は末尾 prev_text_cap 字（それ以前は各ブロックの recap：出した主張・具体例・数字・予告）。
    返ってこなかったブロックは順番で詰めず、軽量プロンプトで単独生成する。字数が外れたブロックも同じ軽量プロンプトで直す。"""
    s = current_settings()
    bp = prj["blueprint"] or {}
    blocks = [b for b in bp.get("blocks", []) if isinstance(b, dict)]
    limit = int(s.get("write_chunk_chars") or 2400)
    max_blocks = int(s.get("max_blocks_per_chunk") or 4)
    chunks, cur, cur_len = [], [], 0
    for b in blocks:
        n = ext._num(b.get("chars")) or 600
        if cur and (cur_len + n > limit or len(cur) >= max_blocks):
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += n
    if cur:
        chunks.append(cur)
    total_chars = sum(ext._num(b.get("chars")) or 600 for b in blocks) or 1
    cross_json = json.dumps(prj["cross"], ensure_ascii=False)
    bp_json = json.dumps(bp, ensure_ascii=False)
    concept_json = json.dumps(concept_with_pack(prj, k), ensure_ascii=False)
    names_all = [b.get("name") for b in blocks]
    cap = int(s.get("input_cap_chars") or 0)
    cap_prev = int(s.get("prev_text_cap") or 2500)
    m = budget.current()
    if m:
        m.note("materials_chars", len(materials or ""))
        if len(materials or "") > 8000:
            m.warn(f"素材が{len(materials):,}字あり、執筆・検品・修正の各呼び出しに全文で入っています（素材は事実なので抜粋しません。長すぎる場合は絞ってください）")
    written, assumptions, kakunin = [], [], []
    done_chars = 0
    for i, ch in enumerate(chunks):
        names = "／".join(b.get("name", "") for b in ch)
        set_progress(job_id, f"執筆中…（{i + 1}/{len(chunks)}回目：{names[:40]}）")
        first, last = (i == 0), (i == len(chunks) - 1)
        chunk_chars = sum(ext._num(b.get("chars")) or 600 for b in ch)
        frac = min(1.0, (done_chars + chunk_chars / 2) / total_chars)
        if first:
            frac = 0.0
        if last:
            frac = 1.0
        voice = with_voice(prj, k, ref_for_writer(prj, frac))
        full_prev = (NL2 + NL2).join("■" + w["name"] + NL2 + w["text"] for w in written if w.get("text"))
        so_far = budget.excerpt(full_prev, cap_prev, "tail")
        covered = NL2.join("・" + w["name"] + "：" + ((w.get("recap") or "").strip() or normalize(w.get("text", ""))[:90] + "…") for w in written)
        remaining = "／".join(b.get("name", "") for c in chunks[i + 1:] for b in c)
        focus = {b.get("name") for b in ch}
        idx = [names_all.index(n_) for n_ in focus if n_ in names_all]
        if idx:
            focus |= {names_all[j] for j in (min(idx) - 1, max(idx) + 1) if 0 <= j < len(names_all)}
        target_json = json.dumps(ch, ensure_ascii=False)

        def build(v):
            return ext.p29_write_blocks(cross_json, bp_json, concept_json, target_json, v["prev"], covered, remaining,
                                        v["voice"], materials, first=first, last=last, focus=focus)
        prompt, _ = shrink_to_cap(build, [["voice", voice, "hmt", 3000], ["prev", so_far, "tail", 1200]], cap)
        res = extract_json(run_claude(prompt))
        got = {}
        for x in res.get("script_blocks", []):
            if isinstance(x, dict) and x.get("text"):
                got[str(x.get("name", "")).strip()] = x
        missing = []
        for b in ch:
            hit = match_block_name(got, b.get("name"))
            if hit:
                written.append({"name": b.get("name"), "text": got[hit].get("text", ""), "recap": str(got[hit].get("recap") or "")})
                got.pop(hit, None)
            else:
                written.append({"name": b.get("name"), "text": "", "recap": ""})
                missing.append(b.get("name"))
        if missing:
            set_progress(job_id, f"返ってこなかったブロック「{'／'.join(missing)}」を単独で生成中…")
        # 返ってこなかったブロックの単独生成＋字数が目安から外れたブロックの調整（同じ軽量プロンプト）
        adjust_lengths(job_id, prj, k, written, only=ch, tolerance=(0.75, 1.3), materials=materials)
        for w in written[-len(ch):]:
            if not w.get("text"):
                kakunin.append(f"ブロック「{w['name']}」の本文が生成されませんでした（空のままです。修正リストか添削で埋めてください）")
        assumptions += res.get("assumptions", [])
        kakunin += res.get("kakunin", [])
        done_chars += chunk_chars
    return {"title": bp.get("title", prj.get("name")), "script_blocks": written,
            "assumptions": assumptions, "kenpin": [], "kakunin": kakunin, "research_prompt": "",
            "write_mode": "blocks", "chunks": len(chunks)}


def match_block_name(got, name):
    """返ってきたブロック名を設計図名に照合する：完全一致 → 番号・記号の接頭辞を除いた一致 → 片方が他方を含む。"""
    if not name:
        return None
    if name in got:
        return name
    strip = lambda x: re.sub(r"^(ブロック|第)?\s*\d+\s*[：:.、．)）]?\s*", "", str(x or "")).strip()
    want = strip(name)
    for g in got:
        if strip(g) == want and want:
            return g
    for g in got:
        if want and (want in g or strip(g) in name) and len(strip(g)) >= 2:
            return g
    return None


def adjust_lengths(job_id, prj, k, written, only=None, tolerance=(0.75, 1.25), materials=""):
    """字数の機械照合。設計図の目安を外れたブロック（と本文が空のブロック）だけを、軽量な字数調整プロンプト（p31）で1回書き直す。
    判定：目安150字以上のブロックで、目安×tolerance を外れ、かつ差が60字より大きいもの（小さなブロックの数十字で呼ばない）。
    不足を埋める（増やす）ブロックがあるときだけ素材を渡す。目安に近づいた時だけ採用。
    （旧：執筆プロンプト全体＝型・設計図・構想・参考台本つきを丸ごと再実行していた）"""
    bp = prj.get("blueprint") or {}
    rows = {b.get("name"): b for b in bp.get("blocks", []) if isinstance(b, dict) and ext._num(b.get("chars"))}
    limit_names = {b.get("name") for b in only} if only is not None else None
    bad, undershoot = [], False
    for w in written:
        if w.get("name") not in rows or (limit_names is not None and w.get("name") not in limit_names):
            continue
        tgt = ext._num(rows[w["name"]].get("chars"))
        got = count_chars(w.get("text", ""))
        if not w.get("text"):
            bad.append(w)
            undershoot = True
            continue
        if tgt < 150:
            continue
        if (got < tgt * tolerance[0] or got > tgt * tolerance[1]) and abs(got - tgt) > 60:
            bad.append(w)
            undershoot = undershoot or got < tgt
    if not bad:
        return 0
    set_progress(job_id, "字数が目安から外れた" + str(len(bad)) + "ブロックを調整中…（" +
                 "／".join(f"{w['name'][:12]}:{count_chars(w['text'])}→{int(ext._num(rows[w['name']]['chars']))}字" for w in bad) + "）")
    names = [w.get("name") for w in written]
    first_idx = min(names.index(w["name"]) for w in bad)
    last_idx = max(names.index(w["name"]) for w in bad)
    prev_tail = budget.excerpt(written[first_idx - 1].get("text", ""), 300, "tail") if first_idx > 0 else ""
    next_head = budget.excerpt(written[last_idx + 1].get("text", ""), 200, "head") if last_idx + 1 < len(written) and written[last_idx + 1].get("text") else ""
    targets = [{"name": w["name"], "text": w.get("text", ""), "current_chars": count_chars(w.get("text", "")),
                "target_chars": int(ext._num(rows[w["name"]]["chars"]))} for w in bad]
    speech = (k.get("character_sheet") or {}).get("speech") or {}
    rules = [r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict) and r.get("rule")]
    try:
        res = extract_json(run_claude(prompts.p31_adjust_length(
            json.dumps([rows[w["name"]] for w in bad], ensure_ascii=False), json.dumps(targets, ensure_ascii=False),
            prev_tail, json.dumps(speech, ensure_ascii=False), json.dumps(rules, ensure_ascii=False),
            materials=(materials or prj.get("materials", "")) if undershoot else "", next_head=next_head,
            voice=voice_card(prj, k))))
    except Exception:
        return 0
    got = {str(x.get("name", "")).strip(): x.get("text", "") for x in res.get("script_blocks", []) if isinstance(x, dict) and x.get("text")}
    fixed = 0
    for w in bad:
        hit = match_block_name(got, w["name"])
        if hit:
            tgt = ext._num(rows[w["name"]]["chars"])
            if not w.get("text") or abs(count_chars(got[hit]) - tgt) < abs(count_chars(w["text"]) - tgt):
                w["text"] = got[hit]
                fixed += 1
    return fixed


def build_write_pack(prj, k, materials):
    """外部AI（ブラウザのGemini等）に貼って書かせるための執筆パック。
    ツールが Claude Code に渡しているものと同じ材料を、ブロックごとの指示文にして書き出す。"""
    bp = prj.get("blueprint") or {}
    blocks = bp.get("blocks", [])
    cross_json = json.dumps(prj["cross"], ensure_ascii=False)
    bp_json = json.dumps(bp, ensure_ascii=False)
    concept_json = json.dumps(concept_with_pack(prj, k), ensure_ascii=False)
    names = [b.get("name", "") for b in blocks]
    parts = ["# 台本一貫スタジオ 執筆パック（外部AI用）",
             f"プロジェクト：{prj['name']}／テーマ：{(prj.get('concept') or {}).get('theme', '')}",
             "",
             "## 使い方",
             "1. 下の「ブロック1」の指示文を丸ごとコピーして、お使いのAI（Gemini等のブラウザ画面）に貼る。",
             "2. 返ってきたJSONの text をそのまま次の指示文の【ここまでに書いた本文の末尾】に貼ってから、ブロック2を貼る（AIのチャットを続ければ自動で覚えているので、その場合は省略可）。",
             "3. 全ブロックを書き終えたら、各ブロックの本文を次の形式で1つにまとめ、ツールの STEP 5「外部AIの出力を取り込む」に貼る。",
             "",
             "```",
             "■ブロック名1",
             "本文…",
             "",
             "■ブロック名2",
             "本文…",
             "```",
             "",
             "ブロック名は設計図と一字一句同じにしてください（照合キーです）。",
             ""]
    total_chars = sum(ext._num(b.get("chars")) or 600 for b in blocks) or 1
    done = 0
    for i, b in enumerate(blocks):
        covered = "／".join(names[:i])
        remaining = "／".join(names[i + 1:])
        n = ext._num(b.get("chars")) or 600
        frac = 0.0 if i == 0 else (1.0 if i == len(blocks) - 1 else min(1.0, (done + n / 2) / total_chars))
        done += n
        voice = with_voice(prj, k, ref_for_writer(prj, frac))
        focus = {b.get("name")} | {names[j] for j in (i - 1, i + 1) if 0 <= j < len(names)}
        prompt = ext.p29_write_blocks(cross_json, bp_json, concept_json, json.dumps([b], ensure_ascii=False),
                                      "【ここまでに書いた本文の末尾（直前のブロック）を貼る（最初のブロックなら不要）】" if i > 0 else "",
                                      covered, remaining, voice, materials, first=(i == 0), last=(i == len(blocks) - 1),
                                      focus=focus)
        parts += [f"---", f"## ブロック{i + 1}：{b.get('name', '')}（目安{b.get('chars', '')}字）", "", "```", prompt, "```", ""]
    return "\n".join(parts)


def parse_external_script(text, blueprint):
    """「■ブロック名\n本文」形式（または JSON）を script_blocks に変換。設計図のブロック名で照合し、順番でも救う。"""
    text = text.strip()
    blocks = []
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
            items = data.get("script_blocks", data) if isinstance(data, dict) else data
            blocks = [{"name": x.get("name", ""), "text": x.get("text", "")} for x in items if isinstance(x, dict)]
        except Exception:
            blocks = []
    if not blocks:
        cur_name, cur = None, []
        for line in text.split("\n"):
            m = re.match(r"^[■◆#]+\s*(.+?)\s*$", line)
            if m and len(m.group(1)) <= 60:
                if cur_name is not None:
                    blocks.append({"name": cur_name, "text": "\n".join(cur).strip()})
                cur_name, cur = m.group(1), []
            else:
                cur.append(line)
        if cur_name is not None:
            blocks.append({"name": cur_name, "text": "\n".join(cur).strip()})
    bp_names = [b.get("name", "") for b in (blueprint or {}).get("blocks", [])]
    if bp_names and len(blocks) == len(bp_names):
        for b, n in zip(blocks, bp_names):
            if b["name"] != n:
                b["name"] = n  # 名前がずれていても順番が同じなら設計図名に揃える
    if not blocks or not any(b["text"] for b in blocks):
        raise RuntimeError("本文を読み取れませんでした。「■ブロック名」の行の下に本文を置く形式で貼ってください")
    return blocks


def job_import_script(job_id, pid, text):
    prj = load_project(pid)
    k = project_karte(prj)
    blocks = parse_external_script(text, prj.get("blueprint"))
    bp = prj.get("blueprint") or {}
    result = {"title": bp.get("title", prj.get("name")), "script_blocks": blocks, "assumptions": [],
              "kenpin": [], "kakunin": [], "research_prompt": "", "write_mode": "external"}
    set_progress(job_id, "取り込んだ台本に機械コピーチェックを実行中…")
    prj.update(script=_copyfix(job_id, prj, result), step=5, review=None, audit=None, audit_log=[], polish=None,
               polish_state=None, fix_decisions={})
    save_project(prj)
    return _audit_loop(job_id, prj, k)


def job_write(job_id, pid, materials):
    prj = load_project(pid)
    k = project_karte(prj)
    prj["materials"] = materials
    if current_settings().get("write_mode", "blocks") == "blocks" and (prj.get("blueprint") or {}).get("blocks"):
        result = write_in_blocks(job_id, prj, k, materials)
    else:
        set_progress(job_id, "台本を執筆中…（2〜4分かかることがあります）")
        scripts_text = with_voice(prj, k, ref_for_writer(prj))
        result = extract_json(run_claude(prompts.p5_write(
            json.dumps(prj["cross"], ensure_ascii=False), json.dumps(prj["blueprint"], ensure_ascii=False),
            json.dumps(concept_with_pack(prj, k), ensure_ascii=False), scripts_text, materials)))
    prj.update(script=_copyfix(job_id, prj, result), step=5, review=None, audit=None, audit_log=[], polish=None,
               polish_state=None, fix_decisions={})
    save_project(prj)
    return _audit_loop(job_id, prj, k)


NL2 = chr(10)


MARKER_RE = re.compile(r"[\[［]要確認[：:]([^\]］]*)[\]］]|（推定）|\(推定\)")


def strip_markers(script):
    """本文に混ざった [要確認：…]・（推定）を抜き、要確認リストに移す。本文には残さない。
    マーカーだけの文（例：「[要確認：…]。」）は文ごと落とす。"""
    found = []
    for b in script.get("script_blocks", []):
        t = b.get("text", "")
        for m in MARKER_RE.finditer(t):
            if m.group(1):
                found.append(f"{b.get('name', '')}：{m.group(1).strip()}")
        t = MARKER_RE.sub("", t)
        kept = []
        for line in t.split("\n"):
            core = line.strip(" \u3000")
            if core and all(ch in "\u3002\u3001" for ch in core):
                continue  # マーカーだけだった行の残骸
            kept.append(line)
        t = "\n".join(kept)
        t = re.sub(r"\u3002{2,}", "\u3002", t).replace("\u3002\u3001", "\u3002").replace("\u3001\u3002", "\u3002")
        t = re.sub(r"\n{3,}", "\n\n", t)
        b["text"] = t.strip()
    if found:
        kk = script.setdefault("kakunin", [])
        for f in found:
            if f not in kk:
                kk.append(f)
    return len(found)


def run_polish(job_id, prj, k, only_changed=False):
    """日本語推敲。ブロックを約 polish_chunk_chars 字ずつの塊に分け、塊ごとに推敲する。
    文脈は前の塊の末尾（推敲済み）・次の塊の冒頭・全ブロックの要点だけ（旧：前後の本文を全文）。
    only_changed=True なら、前回推敲した時から本文が変わったブロックだけを推敲する。
    推敲済みの記録（polish_state のハッシュ）は、推敲が本文を返したブロックだけに付ける（飛ばした塊は次回また対象になる）。
    ブロック名で照合し、欠けたブロックは原文を残す。"""
    script = prj["script"]
    moved = strip_markers(script)
    blocks = script.get("script_blocks", [])
    state = prj.get("polish_state") or {}
    hashes = dict(state.get("hash") or {})
    todo = [b for b in blocks if not only_changed or hashes.get(b.get("name")) != budget.text_hash(b.get("text", ""))]
    notes = []
    prev_changes = (prj.get("polish") or {}).get("changes") or []
    if not todo:
        keep = dict(prj.get("polish") or {"changes": [], "chunks": 0, "blocks": [], "date": date.today().isoformat()})
        keep["note"] = "前回の推敲から本文が変わったブロックは無いので、今回の推敲は省略しました"
        prj["polish"] = keep
        save_project(prj)
        return prj["polish"]
    if only_changed and len(todo) < len(blocks):
        notes.append(f"変わった{len(todo)}ブロックだけを推敲（他は前回のまま）")
    limit = setting_int("polish_chunk_chars")
    ctx_n = setting_int("polish_context_chars")
    chunks, cur, cur_len = [], [], 0
    for b in todo:
        n = count_chars(b.get("text", ""))
        if cur and cur_len + n > limit:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += n
    if cur:
        chunks.append(cur)
    char_json = json.dumps(k.get("character_sheet") or {}, ensure_ascii=False)
    rules_json = json.dumps([r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict)], ensure_ascii=False)
    names_all = [b.get("name") for b in blocks]
    todo_names = {b.get("name") for b in todo}
    summaries = "\n".join("・" + x["name"] + "：" + x["要点"] for x in block_summaries(blocks))
    today = date.today().isoformat()
    new_changes = []
    for i, ch in enumerate(chunks):
        set_progress(job_id, f"日本語を推敲中…（{i + 1}/{len(chunks)}塊・意味を変えず、耳で入る文へ）")
        first_i = names_all.index(ch[0].get("name")) if ch[0].get("name") in names_all else 0
        last_i = names_all.index(ch[-1].get("name")) if ch[-1].get("name") in names_all else len(blocks) - 1
        context = "◆台本タイトル：" + str(script.get("title", "")) + "\n◆全ブロックの要点（順番どおり。主語や対象を補うときの参照）\n" + summaries + "\n"
        if first_i > 0:
            context += "◆前の塊の末尾（推敲済み）\n" + budget.excerpt(blocks[first_i - 1].get("text", ""), ctx_n, "tail") + "\n"
        if last_i + 1 < len(blocks):
            context += "◆次の塊の冒頭\n" + budget.excerpt(blocks[last_i + 1].get("text", ""), max(200, ctx_n * 2 // 3), "head") + "\n"
        try:
            res = extract_json(run_claude(ext.p24_polish(
                json.dumps({"title": script.get("title"), "script_blocks": [{"name": b.get("name"), "text": b.get("text", "")} for b in ch]}, ensure_ascii=False),
                char_json, rules_json, context)))
        except Exception as e:
            notes.append(f"塊{i + 1}は推敲を飛ばしました（{str(e)[:80]}）")
            continue
        by_name = {str(b.get("name", "")).strip(): b.get("text") for b in res.get("script_blocks", []) if isinstance(b, dict) and b.get("text")}
        for b in ch:
            hit = match_block_name(by_name, b.get("name"))
            if hit:
                b["text"] = by_name[hit]
                hashes[b.get("name")] = budget.text_hash(b["text"])  # 返ってきたブロックだけを推敲済みとして記録
        for c in res.get("changes", []):
            if isinstance(c, dict):
                c.setdefault("date", today)
                new_changes.append(c)
        if res.get("untouched_note"):
            notes.append(res["untouched_note"])
    script["total_chars"] = count_chars(script_text(script))
    prj["script"] = script
    prj["polish_state"] = {"hash": hashes, "date": today}
    if moved:
        notes.insert(0, f"本文に混ざっていた要確認マーカー{moved}件を要確認リストへ移しました（本文には残しません）")
    kept = [c for c in prev_changes if isinstance(c, dict) and c.get("where") not in todo_names] if only_changed else []
    prj["polish"] = {"changes": kept + new_changes, "note": "／".join(notes), "chunks": len(chunks),
                     "blocks": [b.get("name") for b in todo], "new_changes": len(new_changes), "date": today}
    save_project(prj)
    return prj["polish"]


def job_polish(job_id, pid):
    """「日本語推敲だけ」。前回の推敲から変わったブロックだけ。何も変わっていなければ全ブロックを推敲し直す（押した人の意図を尊重）。"""
    prj = load_project(pid)
    k = project_karte(prj)
    hashes = (prj.get("polish_state") or {}).get("hash") or {}
    changed = [b for b in prj["script"].get("script_blocks", []) if hashes.get(b.get("name")) != budget.text_hash(b.get("text", ""))]
    run_polish(job_id, prj, k, only_changed=bool(changed))
    if not changed and prj.get("polish") is not None:
        prj["polish"]["note"] = ("前回から本文に変更が無かったため、全ブロックを推敲し直しました" + ("／" + prj["polish"]["note"] if prj["polish"].get("note") else ""))
    prj["original_script_text"] = script_text(prj["script"])
    save_project(prj)
    return {"script": prj["script"], "audit": prj.get("audit"), "audit_log": prj.get("audit_log"), "polish": prj["polish"], "usage": usage_now()}


def ref_for_writer(prj, frac=None):
    """執筆に渡す参考台本（声の見本）。
    ref_mode=budget（既定）：成功例で合計 ref_budget_chars 字を分け合い、今回書く塊の相対位置 frac（0〜1）に対応する
      連続窓を各本から抜粋する（冒頭を書くときは参考台本の冒頭、中間CTAの位置なら参考台本の同じ位置、締めなら締め）。
      frac 無しなら冒頭・中盤・末尾。1本あたり ref_min_chars に足りないときは長い ref_max_count 本だけ使う。
    ref_mode=full：旧方式。全文（ref_full_cap で合計上限。0=無制限）。
    ref_mode=excerpt：各本の冒頭と中盤700字（最軽量）。"""
    s = current_settings()
    refs = [sc for sc in prj.get("scripts", []) if sc.get("kind") != "fail"]
    if not refs:
        return ""
    mode = s.get("ref_mode", "budget")
    if mode == "excerpt":
        return ref_excerpts(prj, 700)
    if mode == "full":
        cap = int(s.get("ref_full_cap", 0))
        out, total = [], 0
        for sc in refs:
            t = sc["text"]
            room = (cap - total) if cap > 0 else len(t)
            if room <= 0:
                break
            piece = t if len(t) <= room else t[:room] + chr(10) + "（以下略）"
            out.append("◆" + sc["title"] + "（全文）" + chr(10) + piece)
            total += len(piece)
        return (chr(10) * 2 + "---" + chr(10) * 2).join(out)
    total = int(s.get("ref_budget_chars") or 6000)
    note = ""
    if len(refs) * int(s.get("ref_min_chars") or 1500) > total and len(refs) > int(s.get("ref_max_count") or 3):
        keep = int(s.get("ref_max_count") or 3)
        chosen = sorted(refs, key=lambda r: -len(r["text"]))[:keep]
        note = f"（参考台本{len(refs)}本のうち長い{keep}本を抜粋）" + chr(10)
        refs = [r for r in refs if r in chosen]
    alloc = budget.split_budget(total, [len(r["text"]) for r in refs])
    out = []
    for r, n in zip(refs, alloc):
        if len(r["text"]) <= n:
            label, piece = "全文", r["text"]
        elif frac is None:
            label, piece = "冒頭・中盤・末尾の抜粋", budget.excerpt(r["text"], n, "hmt")
        else:
            label, piece = f"今回書く位置（全体の{int(round(frac * 100))}%付近）に相当する抜粋", budget.excerpt_at(r["text"], frac, n)
        out.append("◆" + r["title"] + f"（{label}）" + chr(10) + piece)
    return note + (chr(10) * 2 + "---" + chr(10) * 2).join(out)


def ref_for_tone(prj):
    """口調照合に渡す参考台本。各本の冒頭・中盤・末尾を合計 tone_ref_chars 字（ref_mode=full なら全文）。"""
    refs = [sc for sc in prj.get("scripts", []) if sc.get("kind") != "fail"]
    if not refs:
        return ""
    if current_settings().get("ref_mode") == "full":
        return ref_for_writer(prj)
    alloc = budget.split_budget(setting_int("tone_ref_chars"), [len(r["text"]) for r in refs])
    return (chr(10) * 2).join("◆" + r["title"] + "（抜粋）" + chr(10) + budget.excerpt(r["text"], n, "hmt") for r, n in zip(refs, alloc))


def ref_excerpts(prj, limit_each=900):
    """参考台本（成功例）の冒頭と中盤を抜粋。声の見本として口調照合に渡す。"""
    out = []
    for s in prj.get("scripts", []):
        if s.get("kind") == "fail":
            continue
        t = s["text"]
        mid = max(0, len(t) // 2 - limit_each // 2)
        out.append(f"◆{s['title']}（冒頭）\n{t[:limit_each]}\n◆{s['title']}（中盤）\n{t[mid:mid + limit_each]}")
    return "\n\n".join(out)


def run_tone(job_id, prj, k, script, only_blocks=None, prev=None):
    """口調・語彙の照合。参考台本は予算内の抜粋＋声カード。only_blocks を渡すと変わったブロックだけ照合し、
    前回（prev）の指摘のうち引用が本文に残っているものは引き継ぐ。部分照合のとき score・summary・genre_lexicon は
    前回の値を保つ（変更ブロックだけの点数で全体の一致度を上書きしない）。引用できないズレは捨てる。"""
    blocks = script.get("script_blocks", [])
    partial = only_blocks is not None
    target = [b for b in blocks if not partial or b.get("name") in only_blocks]
    set_progress(job_id, "参考台本と口調・語彙を照合中…" + (f"（変更{len(target)}ブロックだけ）" if partial else ""))
    cross = k.get("cross") or prj.get("cross") or {}
    style = {x: cross.get(x) for x in ("style", "teikei", "target") if cross.get(x)}
    note = ("# 今回渡す新作台本は、前回の照合から本文が変わったブロックだけです。渡されたブロックの中だけを照合してください。"
            if partial else "")
    cap = setting_int("input_cap_chars")

    def build(v):
        return ext.p25_tone_check(json.dumps({"script_blocks": [{"name": b.get("name"), "text": b.get("text", "")} for b in target]}, ensure_ascii=False),
                                  v["refs"], json.dumps(style, ensure_ascii=False),
                                  json.dumps(k.get("character_sheet") or {}, ensure_ascii=False), partial_note=note)
    prompt, _ = shrink_to_cap(build, [["refs", with_voice(prj, k, ref_for_tone(prj)), "hmt", 1200]], cap)
    res = extract_json(run_claude(prompt))
    body = normalize(script_text(script))
    ok = [m for m in res.get("mismatches", [])
          if len(normalize(m.get("quote", ""))) >= 8 and normalize(m.get("quote", ""))[:40] in body]
    if partial and prev:
        seen = {normalize(m.get("quote", ""))[:40] for m in ok}
        for m in prev.get("mismatches", []):
            q = normalize(m.get("quote", ""))[:40]
            if q and q in body and q not in seen and (m.get("where") not in only_blocks):
                ok.append(m)
                seen.add(q)
        for key in ("score", "summary", "genre_lexicon"):
            if prev.get(key) not in (None, "", []):
                res[key] = prev[key]
        res["partial"] = True
        res["checked_blocks"] = sorted(only_blocks)
    res["mismatches"] = ok
    return res


def job_revise_selected(job_id, pid, decisions, extra):
    """AIに直させる項目だけを、該当ブロック単位で書き直す。合格ブロックは触らない。
    再検品は差分方式（変わったブロックだけ口調照合、冒頭が変わった時だけ冒頭レビュー、推敲も変わったブロックだけ）。"""
    prj = load_project(pid)
    k = project_karte(prj)
    apply_decisions(prj, decisions)
    save_project(prj)
    items = [it for it in (prj.get("audit") or {}).get("fix_items", []) if it["decision"] == "ai"]
    if not items and not extra.strip():
        raise RuntimeError("AIに直させる項目が選ばれていません（または「その他の修正」が空です）")
    blocks = prj["script"].get("script_blocks", [])
    before = {b.get("name"): b.get("text", "") for b in blocks}
    prev = prj.get("audit")
    out = revise_targets(job_id, prj, k, items, extra=extra)
    if not out.get("targets"):
        apply_decisions(prj, {})
        save_project(prj)
        return {"script": prj["script"], "audit": prj["audit"], "audit_log": prj.get("audit_log"), "polish": prj.get("polish"),
                "changes": [], "note": out.get("note", ""), "usage": usage_now()}
    fix_lengths(job_id, prj, k)
    changes = []
    for b in prj["script"].get("script_blocks", []):
        old = before.get(b.get("name"), "")
        if old != b.get("text", ""):
            changes.append({"name": b.get("name"), "diff": line_diff(old, b.get("text", ""))})
    prj["last_changes"] = changes
    save_project(prj)
    changed = changed_block_names(before, prj["script"].get("script_blocks", []))
    res = run_audit(job_id, prj, k, prj["script"], prj.get("materials", ""), changed=changed, prev=prev)
    prj["audit"] = res
    log = list(prj.get("audit_log") or [])
    log.append({"round": len(log), "ng": res["ng"], "hook_score": res["hook"].get("score"),
                "fatal": len(res["audit"]["fatal"]), "changed": sorted(changed), "note": out.get("note", "")})
    prj["audit_log"] = log
    save_project(prj)
    if current_settings().get("auto_polish", True):
        run_polish(job_id, prj, k, only_changed=True)
    prj["original_script_text"] = script_text(prj["script"])
    save_project(prj)
    return {"script": prj["script"], "audit": prj["audit"], "audit_log": log, "polish": prj.get("polish"), "changes": changes,
            "note": out.get("note", ""), "usage": usage_now()}


def fix_lengths(job_id, prj, k):
    """修正後の字数照合。設計図の目安±25%を外れたブロックだけを、軽量な字数調整で1回書き直す。"""
    script = prj["script"]
    n = adjust_lengths(job_id, prj, k, script.get("script_blocks", []), tolerance=(0.75, 1.25))
    script["total_chars"] = count_chars(script_text(script))
    prj["script"] = script
    return n


def job_audit_only(job_id, pid):
    """「再検品だけ」。検品官・冒頭・口調の採点だけを行い、修正も推敲もしない（結果は修正リストに載る）。
    （旧：修正ラウンド2回＋推敲まで回り、執筆ジョブの半分近いコストになっていた）"""
    prj = load_project(pid)
    return _audit_loop(job_id, prj, project_karte(prj), rounds_override=0, polish=False)


def line_diff(a, b):
    """行単位の差分（-1 削除 / 1 追加 / 0 同じ）。学習プロンプトと画面表示の両方で使う。"""
    al, bl = a.splitlines(), b.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, al, bl, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [{"type": 0, "text": x} for x in al[i1:i2]]
        else:
            out += [{"type": -1, "text": x} for x in al[i1:i2]]
            out += [{"type": 1, "text": x} for x in bl[j1:j2]]
    return out


def job_apply_rules(job_id, pid, edited_text):
    """人が直した範囲はそのまま残し、最後の変更行より後ろに固有ルールを当てる。約 polish_chunk_chars 字ずつ。
    手直し見本は末尾3,000字、直前の本文は末尾1,200字だけ渡す（旧：どちらも全文で、塊ごとに増え続けていた）。"""
    prj = load_project(pid)
    k = project_karte(prj)
    rules = [r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict) and r.get("rule")]
    if not rules:
        raise RuntimeError("固有ルールがまだありません。先に冒頭を手直しして「差分から学習」を押してください")
    original = prj.get("original_script_text") or script_text(prj.get("script"))
    diff = line_diff(original, edited_text)
    # 手直し済み境界＝差分の最後の変更行が、手直し後テキストの何行目か
    edited_lines = edited_text.split("\n")
    pos, last_changed = 0, -1
    for d in diff:
        if d["type"] == 1:
            last_changed = pos
        if d["type"] != -1:
            pos += 1
    boundary = last_changed + 1  # この行から後ろが未添削
    head = edited_lines[:boundary]
    tail = edited_lines[boundary:]
    if not "\n".join(tail).strip():
        return {"edited_text": edited_text, "changes": [], "boundary": boundary, "note": "手直し済みより後ろに本文がありません"}
    # 手直し見本＝オーナーが実際に直した行（diff の追加行）とその前後1行を順に集めたもの（3,000字で打ち切り）
    picked, sample_lines = set(), []
    for i, d in enumerate(diff):
        if d["type"] == 1:
            for j in (i - 1, i, i + 1):
                if 0 <= j < len(diff) and diff[j]["type"] != -1 and j not in picked:
                    picked.add(j)
                    sample_lines.append(diff[j]["text"])
    sample = "\n".join(sample_lines)[:3000] if sample_lines else budget.excerpt("\n".join(head), 3000, "tail")
    rules_json = json.dumps(rules, ensure_ascii=False)
    char_json = json.dumps(k.get("character_sheet") or {}, ensure_ascii=False)
    # 行単位で約 polish_chunk_chars 字の塊に分ける
    limit = setting_int("polish_chunk_chars")
    chunks, cur, cur_len = [], [], 0
    for line in tail:
        if cur and cur_len + len(line) > limit:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append(cur)
    out_lines, changes = list(head), []
    for i, ch in enumerate(chunks):
        set_progress(job_id, f"学習したルールを当てています…（{i + 1}/{len(chunks)}塊・{len(rules)}ルール）")
        before_ctx = budget.excerpt("\n".join(out_lines), 1200, "tail")
        try:
            res = extract_json(run_claude(ext.p30_apply_rules("\n".join(ch), sample, rules_json, char_json, before_ctx)))
            text = res.get("text") or "\n".join(ch)
            changes += res.get("changes", [])
        except Exception as e:
            text = "\n".join(ch)
            changes.append({"before": "", "after": "", "rule": f"塊{i + 1}は適用を飛ばしました（{str(e)[:60]}）"})
        out_lines += text.split("\n")
    new_text = "\n".join(out_lines)
    prj["review"] = {**(prj.get("review") or {}), "edited_text": new_text, "diff": line_diff(original, new_text),
                     "auto_applied": {"date": date.today().isoformat(), "boundary": boundary, "changes": changes}}
    save_project(prj)
    return {"edited_text": new_text, "changes": changes, "boundary": boundary, "diff": prj["review"]["diff"]}


def job_learn(job_id, pid, edited_text, note):
    prj = load_project(pid)
    k = project_karte(prj)
    original = prj.get("original_script_text") or script_text(prj.get("script"))
    diff = line_diff(original, edited_text)
    changed = [d for d in diff if d["type"] != 0]
    prj["review"] = {"edited_text": edited_text, "note": note, "diff": diff,
                     "changed_lines": len(changed), "date": date.today().isoformat()}
    prj["step"] = max(prj.get("step", 1), 6)
    save_project(prj)
    if not changed and not note.strip():
        return {"rules": [], "keep": [], "summary": "差分がありません（手直しなし）", "diff": diff, "added": 0,
                "total_rules": len(k.get("house_style", []))}
    keep_idx = set()
    for i, d in enumerate(diff):
        if d["type"] != 0:
            keep_idx.update((i - 1, i, i + 1))
    compact = [diff[i] for i in sorted(keep_idx) if 0 <= i < len(diff)]
    learned = {"rules": [], "keep": [], "summary": ""}
    if changed:
        set_progress(job_id, f"添削差分 {len(changed)} 行からルールを抽出中…")
        learned = extract_json(run_claude(ext.p23_learn_rules(
            json.dumps(diff, ensure_ascii=False),
            json.dumps(k.get("character_sheet") or {}, ensure_ascii=False),
            json.dumps([r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict)], ensure_ascii=False))))
    if note.strip():
        learned.setdefault("rules", []).append({"rule": note.strip(), "category": "オーナー指示", "before": "",
                                                "after": "", "condition": "常時", "confidence": "high"})
    existing = {normalize(r.get("rule", "")) for r in k.get("house_style", []) if isinstance(r, dict)}
    added = 0
    for r in learned.get("rules", []):
        key = normalize(r.get("rule", ""))
        if not key or key in existing:
            continue
        r["date"] = date.today().isoformat()
        r["project"] = prj["name"]
        k.setdefault("house_style", []).append(r)
        existing.add(key)
        added += 1
    k.setdefault("patterns", []).append({"date": date.today().isoformat(), "project": prj["name"],
                                         "changed_lines": len(changed), "summary": learned.get("summary", ""),
                                         "keep": learned.get("keep", [])})
    save_karte(k)
    learned.update(diff=diff, added=added, total_rules=len(k.get("house_style", [])),
                   house_style=k.get("house_style", []))
    return learned


# ----------------------------------------------------------------
# ルーティング
# ----------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


def _doc_page(title, body):
    return f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{title}｜台本一貫スタジオ</title>
<link rel="stylesheet" href="/static/style.css?v=2"><link rel="stylesheet" href="/static/doc.css?v=2"></head><body>
<header><div class="brand" onclick="location.href='/'">🧬 台本一貫スタジオ</div>
<div class="header-right"><a class="btn small ghost" href="/">← ツールに戻る</a></div></header>
<div class="manual">{body}</div></body></html>"""


@app.get("/start")
def start_page():
    p = resolve_path(BASE / "manual", "はじめに.md")
    if not p.exists():
        return "<p>はじめに.md が見つかりません</p>", 404
    return _doc_page("はじめに", md_to_html(p.read_text(encoding="utf-8")))


@app.get("/guide")
def guide():
    p = resolve_path(BASE / "manual", "使い方.md")
    if not p.exists():
        return "<p>使い方マニュアルが見つかりません（manual/使い方.md）</p>", 404
    return _doc_page("使い方マニュアル", md_to_html(p.read_text(encoding="utf-8")))


@app.get("/manual")
def manual():
    if not MANUAL_PATH.exists():
        return "<p>マニュアルが見つかりません（manual/manual.md）</p>", 404
    return _doc_page("台本の考え方マニュアル", md_to_html(MANUAL_PATH.read_text(encoding="utf-8")))


@app.get("/knowledge")
def knowledge_index():
    body = "<h1>同梱ナレッジ</h1><p>各工程のプロンプトに自動で注入される資料です。ファイルを編集すると次の実行から反映されます。</p><ul>"
    for it in ext.list_knowledge():
        mark = "" if it["exists"] else "（見つかりません）"
        body += f'<li><a href="/knowledge/{it["key"]}">{it["file"]}</a> {mark} <span class="hint">{it["chars"]}字</span></li>'
    return _doc_page("同梱ナレッジ", body + "</ul>")


@app.get("/knowledge/<key>")
def knowledge_view(key):
    text = ext.knowledge(key)
    if not text:
        return "<p>見つかりません</p>", 404
    return _doc_page(ext.KNOWLEDGE_FILES.get(key, key), md_to_html(text))


@app.get("/api/state")
def api_state():
    jev_ready = bool(shutil.which("node")) and (JEV_DIR / "node_modules").exists()
    return jsonify({"settings": load_settings(), "projects": list_projects(), "karte": list_karte(),
                    "knowledge": ext.list_knowledge(), "jev_ready": jev_ready})


@app.post("/api/settings")
def api_settings():
    s = load_settings()
    data = request.json or {}
    if isinstance(data.get("jev"), dict):
        s["jev"] = {**(s.get("jev") or {}), **data.pop("jev")}
    for key in _ZERO_MEANS_DEFAULT + ("audit_rounds", "timeout_sec", "ref_full_cap"):
        if key in data:
            try:
                data[key] = int(float(data[key]))
            except (TypeError, ValueError):
                data.pop(key)
    s.update(data)
    save_settings(s)
    return jsonify(load_settings())


@app.post("/api/ping")
def api_ping():
    t0 = time.time()
    try:
        reply = run_claude("接続テストです。「接続OK」とだけ返してください。", force_real=True)
        return jsonify({"ok": True, "reply": reply.strip()[:100], "seconds": round(time.time() - t0, 1)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/api/project")
def api_project_create():
    data = request.json or {}
    name = (data.get("name") or "").strip() or f"プロジェクト{date.today().isoformat()}"
    prj = {"id": uuid.uuid4().hex[:10], "name": name, "created": date.today().isoformat(), "step": 1,
           "scripts": [], "analyses": None, "cross": None, "concept": None, "blueprint": None,
           "script": None, "audit": None, "review": None, "karte_name": safe_name(data.get("karte") or ""),
           "intent": (data.get("intent") or "").strip()}
    if prj["karte_name"] and prj["karte_name"] != "no-name":
        k = load_karte(prj["karte_name"])
        if k and k.get("cross"):
            prj["cross"] = k["cross"]
            prj["step"] = 2
    else:
        prj["karte_name"] = ""
    save_project(prj)
    return jsonify(prj)


@app.get("/api/project/<pid>")
def api_project_get(pid):
    prj = load_project(pid)
    if not prj:
        return jsonify({"error": "not found"}), 404
    k = project_karte(prj)
    if not prj.get("diagnosis") and k.get("diagnosis"):
        prj["diagnosis"] = k["diagnosis"]
    a = prj.get("audit")
    if a and "fix_items" not in a:
        # 修正リスト導入前の検品結果を、そのままリストに組み直す
        a["fix_items"] = build_fix_items(prj, a.get("audit"), a.get("hook"), a.get("tone"), a.get("jev"))
        a["ng"] = sum(1 for it in a["fix_items"] if it["decision"] != "ignore")
        save_project(prj)
    prj["usage_total"] = usage_total(prj)
    prj["karte"] = {"summary": karte_summary(k), "concept_sheet": k.get("concept_sheet"),
                    "character_sheet": k.get("character_sheet"), "world_sheet": k.get("world_sheet"),
                    "house_style": k.get("house_style", []),
                    "success_scripts": k.get("success_scripts", [])}
    return jsonify(prj)


@app.post("/api/project/<pid>/scripts")
def api_project_scripts(pid):
    prj = load_project(pid)
    data = request.json or {}
    scripts = []
    for s in data.get("scripts", [])[:6]:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        scripts.append({"title": (s.get("title") or "").strip() or f"台本{len(scripts) + 1}",
                        "text": text, "chars": count_chars(text),
                        "kind": "fail" if s.get("kind") == "fail" else "success"})
    if not scripts:
        return jsonify({"error": "台本が1本もありません"}), 400
    prj["scripts"] = scripts
    if "intent" in data:
        prj["intent"] = (data.get("intent") or "").strip()
    if data.get("karte"):
        prj["karte_name"] = safe_name(data["karte"])
    save_project(prj)
    return jsonify({"ok": True})


@app.post("/api/read_folder")
def api_read_folder():
    path = Path((request.json or {}).get("path", "").strip().strip('"'))
    if not path.exists():
        return jsonify({"error": f"フォルダが見つかりません: {path}"}), 400
    files = sorted(list(path.glob("*.txt")) + list(path.glob("*.md")))[:6]
    if not files:
        return jsonify({"error": "フォルダ内に .txt / .md ファイルがありません"}), 400
    return jsonify({"scripts": [{"title": f.stem, "text": f.read_text(encoding="utf-8", errors="replace")} for f in files]})


@app.post("/api/upload")
def api_upload():
    out = []
    for f in request.files.getlist("files")[:6]:
        try:
            out.append({"title": Path(f.filename).stem, "text": f.read().decode("utf-8", errors="replace")})
        except Exception:
            continue
    if not out:
        return jsonify({"error": "ファイルを読み込めませんでした"}), 400
    return jsonify({"scripts": out})


@app.post("/api/project/<pid>/use_karte")
def api_use_karte(pid):
    prj = load_project(pid)
    k = load_karte(Path((request.json or {}).get("file", "")).stem)
    if not k or not k.get("cross"):
        return jsonify({"error": "カルテが見つかりません（または未分析）"}), 404
    prj.update(cross=k["cross"], karte_name=k["name"], step=2)
    save_project(prj)
    return jsonify({"ok": True, "cross": prj["cross"], "karte": karte_summary(k)})


@app.post("/api/project/<pid>/analyze")
def api_analyze(pid):
    return jsonify({"job": start_job(job_analyze, pid)})


@app.post("/api/project/<pid>/diagnose")
def api_diagnose(pid):
    return jsonify({"job": start_job(job_diagnose, pid)})


@app.post("/api/project/<pid>/setup_all")
def api_setup_all(pid):
    return jsonify({"job": start_job(job_setup_all, pid, (request.json or {}).get("memos", {}))})


@app.post("/api/project/<pid>/intent")
def api_intent(pid):
    prj = load_project(pid)
    prj["intent"] = ((request.json or {}).get("intent") or "").strip()
    save_project(prj)
    return jsonify({"ok": True})


@app.post("/api/project/<pid>/reconsider")
def api_reconsider(pid):
    theme = ((request.json or {}).get("theme") or "").strip()
    if not theme:
        return jsonify({"error": "テーマが空です"}), 400
    return jsonify({"job": start_job(job_reconsider, pid, theme)})


@app.post("/api/project/<pid>/concept")
def api_concept(pid):
    return jsonify({"job": start_job(job_concept, pid, (request.json or {}).get("memo", ""))})


@app.post("/api/project/<pid>/character")
def api_character(pid):
    return jsonify({"job": start_job(job_character, pid, (request.json or {}).get("memo", ""))})


@app.post("/api/project/<pid>/world")
def api_world(pid):
    return jsonify({"job": start_job(job_world, pid, (request.json or {}).get("memo", ""))})


@app.post("/api/project/<pid>/channel_save")
def api_channel_save(pid):
    """コンセプト・キャラ設定書・固有ルールの手直しを保存。"""
    prj = load_project(pid)
    k = project_karte(prj)
    data = request.json or {}
    for key in ("concept_sheet", "character_sheet", "world_sheet"):
        if key in data:
            sheet = data[key]
            if isinstance(sheet, dict):
                sheet.pop("usage", None)
            k[key] = sheet
    if isinstance(data.get("house_style"), list):
        k["house_style"] = data["house_style"]
    save_karte(k)
    prj["karte_name"] = k["name"]
    prj["step"] = max(prj.get("step", 1), 3)
    save_project(prj)
    return jsonify({"ok": True, "karte": karte_summary(k)})


@app.post("/api/project/<pid>/propose")
def api_propose(pid):
    return jsonify({"job": start_job(job_propose, pid, (request.json or {}).get("memo", ""))})


@app.post("/api/project/<pid>/blueprint")
def api_blueprint(pid):
    data = request.json or {}
    return jsonify({"job": start_job(job_blueprint, pid, data.get("concept", {}), data.get("revise", ""))})


@app.post("/api/project/<pid>/write_pack")
def api_write_pack(pid):
    prj = load_project(pid)
    if not (prj.get("blueprint") or {}).get("blocks"):
        return jsonify({"error": "設計図がまだありません"}), 400
    materials = (request.json or {}).get("materials", prj.get("materials", ""))
    prj["materials"] = materials
    save_project(prj)
    k = project_karte(prj)
    pack = build_write_pack(prj, k, materials)
    out = PROJECTS_DIR / prj["id"] / "執筆パック.md"
    out.write_text(pack, encoding="utf-8-sig")
    return jsonify({"ok": True, "pack": pack, "file": str(out), "blocks": len(prj["blueprint"]["blocks"])})


@app.post("/api/project/<pid>/import_script")
def api_import_script(pid):
    text = (request.json or {}).get("text", "")
    if not text.strip():
        return jsonify({"error": "貼り付けが空です"}), 400
    return jsonify({"job": start_job(job_import_script, pid, text)})


@app.post("/api/project/<pid>/write")
def api_write(pid):
    return jsonify({"job": start_job(job_write, pid, (request.json or {}).get("materials", ""))})


@app.post("/api/project/<pid>/revise")
def api_revise(pid):
    instruction = (request.json or {}).get("instruction", "")
    if not instruction.strip():
        return jsonify({"error": "修正指示が空です"}), 400
    # 自由記述も「ブロック名：指示」の行は部分修正に乗せる（revise_selected と同じ経路）
    return jsonify({"job": start_job(job_revise_selected, pid, {}, instruction)})


@app.post("/api/project/<pid>/polish")
def api_polish(pid):
    return jsonify({"job": start_job(job_polish, pid)})


@app.post("/api/project/<pid>/fix_decisions")
def api_fix_decisions(pid):
    prj = load_project(pid)
    apply_decisions(prj, (request.json or {}).get("decisions") or {})
    save_project(prj)
    return jsonify({"ok": True, "audit": prj.get("audit")})


@app.post("/api/project/<pid>/revise_selected")
def api_revise_selected(pid):
    data = request.json or {}
    return jsonify({"job": start_job(job_revise_selected, pid, data.get("decisions") or {}, data.get("extra") or "")})


@app.post("/api/project/<pid>/audit")
def api_audit(pid):
    return jsonify({"job": start_job(job_audit_only, pid)})


@app.post("/api/project/<pid>/apply_rules")
def api_apply_rules(pid):
    edited = (request.json or {}).get("edited_text", "")
    if not edited.strip():
        return jsonify({"error": "手直し後の台本が空です"}), 400
    return jsonify({"job": start_job(job_apply_rules, pid, edited)})


@app.post("/api/project/<pid>/learn")
def api_learn(pid):
    data = request.json or {}
    edited = data.get("edited_text", "")
    if not edited.strip():
        return jsonify({"error": "手直し後の台本が空です"}), 400
    return jsonify({"job": start_job(job_learn, pid, edited, data.get("note", ""))})


@app.post("/api/project/<pid>/finish")
def api_finish(pid):
    prj = load_project(pid)
    data = request.json or {}
    k = project_karte(prj)
    script = prj.get("script") or {}
    edited = (prj.get("review") or {}).get("edited_text")
    final_text = edited or script_text(script)
    title = script.get("title", prj["name"])
    entry = {"date": date.today().isoformat(), "project": prj["name"], "title": title,
             "hook_score": ((prj.get("audit") or {}).get("hook") or {}).get("score"),
             "rounds": len(prj.get("audit_log") or []), "feedback": data.get("feedback", "")}
    k.setdefault("history", []).append(entry)
    if data.get("mark_success", True):
        k.setdefault("success_scripts", []).append({"date": entry["date"], "title": title,
                                                    "chars": count_chars(final_text), "project_id": prj["id"]})
        samples = k.setdefault("style_samples", [])
        samples.append(final_text)
    save_karte(k)
    prj["step"] = 7
    save_project(prj)
    lines = [f"# {title}", ""]
    if edited:
        lines.append(final_text)
    else:
        for b in script.get("script_blocks", []):
            lines += [f"■{b.get('name', '')}", b.get("text", ""), ""]
    out_path = PROJECTS_DIR / prj["id"] / "台本.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8-sig")
    return jsonify({"ok": True, "karte": k["name"], "file": str(out_path)})


@app.get("/api/karte/<name>")
def api_karte_get(name):
    k = load_karte(name)
    return jsonify(k) if k else (jsonify({"error": "not found"}), 404)


@app.get("/api/job/<job_id>")
def api_job(job_id):
    with job_lock:
        j = jobs.get(job_id)
    return jsonify(j) if j else (jsonify({"error": "job not found"}), 404)


normalize_filenames()

if __name__ == "__main__":
    PROJECTS_DIR.mkdir(exist_ok=True)
    KARTE_DIR.mkdir(exist_ok=True)
    print(f"台本一貫スタジオ: http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
