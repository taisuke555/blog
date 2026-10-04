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
import uuid
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, render_template, request

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
                    "write_mode": "blocks", "write_chunk_chars": 0, "ref_mode": "full", "ref_full_cap": 0, "prev_text_cap": 0,
                    "jev": {"enabled": False, "rubric": "daihon.json"}}


# ----------------------------------------------------------------
# 基盤
# ----------------------------------------------------------------

def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return s


def save_settings(s):
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def run_claude(prompt, force_real=False):
    settings = load_settings()
    engine = settings.get("engine", "claude")
    if engine == "mock" and not force_real:
        r = ext.mock_response_ext(prompt)
        return r if r is not None else prompts.mock_response(prompt)
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
    data = json.loads(proc.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude応答エラー: {str(data.get('result'))[:500]}")
    return data.get("result", "")


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
                                "karte": prj.get("karte_name", "")})
                except Exception:
                    continue
    return out


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|]+', "_", name or "").strip() or "no-name"


def karte_path(name):
    return KARTE_DIR / f"{safe_name(name)}.json"


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


def style_samples_text(karte):
    samples = karte.get("style_samples") or []
    return "\n\n---\n\n".join(samples) if samples else ""


# ----------------------------------------------------------------
# ジョブ
# ----------------------------------------------------------------

def start_job(fn, *args):
    job_id = uuid.uuid4().hex[:12]
    with job_lock:
        jobs[job_id] = {"status": "running", "progress": "開始しています…", "result": None, "error": None}

    def runner():
        try:
            result = fn(job_id, *args)
            with job_lock:
                jobs[job_id].update(status="done", result=result)
        except Exception as e:
            with job_lock:
                jobs[job_id].update(status="error", error=str(e))

    threading.Thread(target=runner, daemon=True).start()
    return job_id


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
    if (load_settings().get("jev") or {}).get("enabled"):
        set_progress(job_id, "設計図の外部採点（Jev）を実行中…")
        out["jev"] = run_jev_rubric("blueprint.json",
                                    "【ユーザーの入力】\n" + json.dumps({"intent": prj.get("intent"), **(prj.get("concept") or {})}, ensure_ascii=False)
                                    + "\n\n【チャンネル設計】\n" + json.dumps(pack, ensure_ascii=False)
                                    + "\n\n【設計図】\n" + json.dumps(prj["blueprint"], ensure_ascii=False))
        if out["jev"].get("rows"):
            out["ng"] += sum(1 for r in out["jev"]["rows"] if r.get("verdict") == "×")
    return out


def run_jev_rubric(rubric_name, text):
    s = load_settings().get("jev") or {}
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
    if fix and load_settings().get("bp_autofix", True):
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
    refs = [s["text"] for s in prj["scripts"] if s.get("kind") != "fail"]
    teikei = (prj.get("cross") or {}).get("teikei", [])
    set_progress(job_id, "機械コピーチェック（連続20文字一致）を実行中…")
    check = copycheck(script_text(result), refs, teikei)
    if check["violations"]:
        set_progress(job_id, f"コピー疑い{len(check['violations'])}件を自動で書き直し中…")
        result = extract_json(run_claude(prompts.p7_fixcopy(
            json.dumps(result, ensure_ascii=False), json.dumps(check["violations"], ensure_ascii=False))))
        check = copycheck(script_text(result), refs, teikei)
    result["copycheck"] = check
    result["total_chars"] = count_chars(script_text(result))
    return result


def run_jev(script, character, refs="", world=None):
    """外部採点（任意）。Node・キー・設定が揃わなければ skipped を返して黙って先へ進む。"""
    s = load_settings().get("jev") or {}
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


def run_audit(job_id, prj, karte, script, materials):
    """独立検品官（p13）＋冒頭30秒レビュー（p22）＋外部採点（任意）。引用は本文と機械照合する。"""
    checklist = list((karte.get("cross") or {}).get("checklist", []))
    checklist += ext.character_checklist(karte.get("character_sheet"))
    checklist += ext.world_checklist(karte.get("world_sheet"))
    checklist += ext.house_style_checklist(karte.get("house_style"))
    bp = prj.get("blueprint") or {}
    checklist_pack = {"items": checklist,
                      "character_sheet": karte.get("character_sheet"),
                      "world_sheet": karte.get("world_sheet"),
                      "house_style": karte.get("house_style", []),
                      "concept_sheet": karte.get("concept_sheet")}
    set_progress(job_id, "検品官が型・キャラ・固有ルールで採点中…")
    raw = extract_json(run_claude(prompts.p13_audit(
        json.dumps(checklist_pack, ensure_ascii=False), json.dumps(script, ensure_ascii=False),
        json.dumps(bp, ensure_ascii=False), materials)))
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

    set_progress(job_id, "冒頭30秒を採点中…")
    blocks_ = script.get("script_blocks", [])
    opening = blocks_[0].get("text", "") if blocks_ else script_text(script)
    if len(opening) < 600 and len(blocks_) > 1:
        opening += "\n" + blocks_[1].get("text", "")
    hook = extract_json(run_claude(ext.p22_hook_review(
        opening, json.dumps(karte.get("concept_sheet") or {}, ensure_ascii=False),
        json.dumps(karte.get("character_sheet") or {}, ensure_ascii=False))))

    tone = None
    if load_settings().get("tone_check", True) and prj.get("scripts"):
        tone = run_tone(job_id, prj, karte, script)

    jev = None
    if (load_settings().get("jev") or {}).get("enabled"):
        set_progress(job_id, "外部採点（Jev）を実行中…")
        jev = run_jev(script, karte.get("character_sheet"), ref_excerpts(prj, 500), karte.get("world_sheet"))
    items = build_fix_items(prj, audit, hook, tone, jev)
    ng = sum(1 for it in items if it["decision"] != "ignore")
    return {"audit": audit, "hook": hook, "jev": jev, "tone": tone, "ng": ng, "fix_items": items}


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


def _audit_loop(job_id, prj, k, rounds_override=None):
    """検品→落ちた項目だけ修正→再検品。回数上限は settings.audit_rounds。"""
    rounds = int(load_settings().get("audit_rounds", 2)) if rounds_override is None else int(rounds_override)
    materials = prj.get("materials", "")
    log = []
    for r in range(rounds + 1):
        res = run_audit(job_id, prj, k, prj["script"], materials)
        log.append({"round": r, "ng": res["ng"], "hook_score": res["hook"].get("score"),
                    "fatal": len(res["audit"]["fatal"])})
        prj["audit"] = res
        prj["audit_log"] = log
        save_project(prj)
        if res["ng"] == 0 or r == rounds:
            break
        active = [it for it in res.get("fix_items", []) if it["decision"] == "ai"]
        instruction = ext.build_fix_instruction(res["audit"], res["hook"],
                                                (res["jev"] or {}).get("rows"), k.get("house_style"), res.get("tone")) if active else ""
        if not instruction:
            break
        set_progress(job_id, f"不合格{res['ng']}件だけを修正中…（{r + 1}/{rounds}回目）")
        fixed = extract_json(run_claude(prompts.p6_revise(
            json.dumps(prj["cross"], ensure_ascii=False), json.dumps(prj.get("blueprint") or {}, ensure_ascii=False),
            json.dumps(prj["script"], ensure_ascii=False), instruction, materials, style_samples_text(k))))
        prj["script"] = _copyfix(job_id, prj, fixed)
        fix_lengths(job_id, prj, k)
        save_project(prj)
    if load_settings().get("auto_polish", True):
        run_polish(job_id, prj, k)
    prj["original_script_text"] = script_text(prj["script"])
    save_project(prj)
    return {"script": prj["script"], "audit": prj["audit"], "audit_log": log, "polish": prj.get("polish")}


def write_in_blocks(job_id, prj, k, materials):
    """設計図のブロックを約 write_chunk_chars 字ずつの塊に分け、直前までの本文を文脈にして順に書く。"""
    bp = prj["blueprint"] or {}
    blocks = bp.get("blocks", [])
    limit = int(load_settings().get("write_chunk_chars", 0))  # 0 = 1ブロックずつ
    chunks, cur, cur_len = [], [], 0
    for b in blocks:
        n = ext._num(b.get("chars")) or 600
        if cur and (limit <= 0 or cur_len + n > limit):
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += n
    if cur:
        chunks.append(cur)
    voice = ref_for_writer(prj)
    cross_json = json.dumps(prj["cross"], ensure_ascii=False)
    bp_json = json.dumps(bp, ensure_ascii=False)
    concept_json = json.dumps(concept_with_pack(prj, k), ensure_ascii=False)
    written, assumptions, kakunin = [], [], []
    for i, ch in enumerate(chunks):
        names = "／".join(b.get("name", "") for b in ch)
        set_progress(job_id, f"執筆中…（{i + 1}/{len(chunks)}塊：{names[:40]}）")
        full_prev = (NL2 + NL2).join("■" + w["name"] + NL2 + w["text"] for w in written)
        cap_prev = int(load_settings().get("prev_text_cap", 0))
        so_far = full_prev if (cap_prev <= 0 or len(full_prev) <= cap_prev) else "（前半略）" + full_prev[-cap_prev:]
        covered = NL2.join("・" + w["name"] + "：" + normalize(w["text"])[:90] + "…" for w in written)
        remaining = "／".join(b.get("name", "") for c in chunks[i + 1:] for b in c)
        res = extract_json(run_claude(ext.p29_write_blocks(
            cross_json, bp_json, concept_json, json.dumps(ch, ensure_ascii=False), so_far, covered, remaining,
            voice, materials, first=(i == 0), last=(i == len(chunks) - 1))))
        got = {b.get("name"): b.get("text", "") for b in res.get("script_blocks", []) if b.get("text")}
        for b in ch:
            text = got.get(b.get("name"))
            if not text:
                # 名前がずれて返ってきた場合は順番で救う
                leftovers = [t for n_, t in got.items() if n_ not in [x.get("name") for x in ch]]
                text = leftovers.pop(0) if leftovers else f"[要確認：ブロック「{b.get('name')}」の本文が生成されませんでした]"
            written.append({"name": b.get("name"), "text": text})
        # 字数の機械照合：設計図の目安±25%を外れたら、その塊だけ1回書き直す
        bad = []
        for b in ch:
            tgt = ext._num(b.get("chars"))
            got_len = count_chars(written[-len(ch) + ch.index(b)]["text"]) if False else count_chars(next(w["text"] for w in reversed(written) if w["name"] == b.get("name")))
            if tgt and (got_len < tgt * 0.75 or got_len > tgt * 1.3):
                bad.append((b.get("name"), tgt, got_len))
        if bad and not res.get("_retried"):
            set_progress(job_id, "字数が目安から外れたブロックを書き直し中…（" + "／".join(f"{n}:{g}字→目安{int(t)}字" for n, t, g in bad) + "）")
            note = "前回の出力は字数が目安から外れました（" + "／".join(f"「{n}」{g}字、目安{int(t)}字" for n, t, g in bad) + "）。目安±20%に必ず収めて書き直してください。"
            res2 = extract_json(run_claude(ext.p29_write_blocks(
                cross_json, bp_json, concept_json, json.dumps(ch, ensure_ascii=False), so_far, covered, remaining,
                voice, (materials + NL2 + NL2 + "# 追加指示" + NL2 + note) if materials else "# 追加指示" + NL2 + note,
                first=(i == 0), last=(i == len(chunks) - 1))))
            got2 = {x.get("name"): x.get("text", "") for x in res2.get("script_blocks", []) if x.get("text")}
            for w in written[-len(ch):]:
                if w["name"] in got2 and got2[w["name"]]:
                    new_len = count_chars(got2[w["name"]])
                    old_len = count_chars(w["text"])
                    tgt = next((ext._num(b.get("chars")) for b in ch if b.get("name") == w["name"]), 0)
                    if tgt and abs(new_len - tgt) < abs(old_len - tgt):
                        w["text"] = got2[w["name"]]
        assumptions += res.get("assumptions", [])
        kakunin += res.get("kakunin", [])
    return {"title": bp.get("title", prj.get("name")), "script_blocks": written,
            "assumptions": assumptions, "kenpin": [], "kakunin": kakunin, "research_prompt": "",
            "write_mode": "blocks", "chunks": len(chunks)}


def build_write_pack(prj, k, materials):
    """外部AI（ブラウザのGemini等）に貼って書かせるための執筆パック。
    ツールが Claude Code に渡しているものと同じ材料を、ブロックごとの指示文にして書き出す。"""
    bp = prj.get("blueprint") or {}
    blocks = bp.get("blocks", [])
    voice = ref_for_writer(prj)
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
    for i, b in enumerate(blocks):
        covered = "／".join(names[:i])
        remaining = "／".join(names[i + 1:])
        prompt = ext.p29_write_blocks(cross_json, bp_json, concept_json, json.dumps([b], ensure_ascii=False),
                                      "【ここまでに書いた本文の末尾を貼る（最初のブロックなら不要）】" if i > 0 else "",
                                      covered, remaining, voice, materials, first=(i == 0), last=(i == len(blocks) - 1))
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
    prj.update(script=_copyfix(job_id, prj, result), step=5, review=None)
    save_project(prj)
    return _audit_loop(job_id, prj, k)


def job_write(job_id, pid, materials):
    prj = load_project(pid)
    k = project_karte(prj)
    prj["materials"] = materials
    if load_settings().get("write_mode", "blocks") == "blocks" and (prj.get("blueprint") or {}).get("blocks"):
        result = write_in_blocks(job_id, prj, k, materials)
    else:
        set_progress(job_id, "台本を執筆中…（2〜4分かかることがあります）")
        scripts_text = "\n\n---\n\n".join(f"◆{s['title']}\n{s['text']}" for s in prj["scripts"] if s.get("kind") != "fail")
        result = extract_json(run_claude(prompts.p5_write(
            json.dumps(prj["cross"], ensure_ascii=False), json.dumps(prj["blueprint"], ensure_ascii=False),
            json.dumps(concept_with_pack(prj, k), ensure_ascii=False), scripts_text, materials)))
    prj.update(script=_copyfix(job_id, prj, result), step=5, review=None)
    save_project(prj)
    return _audit_loop(job_id, prj, k)


def job_revise(job_id, pid, instruction):
    prj = load_project(pid)
    k = project_karte(prj)
    rules = k.get("house_style") or []
    if rules:
        instruction += "\n\n# このチャンネルの固有ルール（必ず守る）\n" + "\n".join(
            "- " + (r.get("rule") if isinstance(r, dict) else str(r)) for r in rules)
    set_progress(job_id, "修正指示を反映してリライト中…")
    fixed = extract_json(run_claude(prompts.p6_revise(
        json.dumps(prj["cross"], ensure_ascii=False), json.dumps(prj.get("blueprint") or {}, ensure_ascii=False),
        json.dumps(prj["script"], ensure_ascii=False), instruction, prj.get("materials", ""), style_samples_text(k))))
    prj["script"] = _copyfix(job_id, prj, fixed)
    fix_lengths(job_id, prj, k)
    save_project(prj)
    return _audit_loop(job_id, prj, k)


POLISH_CHUNK_CHARS = 1800
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


def run_polish(job_id, prj, k):
    """日本語推敲。長い台本を1回で渡すと後半が甘くなるので、ブロックを約1,800字ずつの塊に分け、
    前後の塊を文脈として渡しながら塊ごとに推敲する。ブロック名で照合し、欠けたブロックは原文を残す。"""
    script = prj["script"]
    moved = strip_markers(script)
    blocks = script.get("script_blocks", [])
    chunks, cur, cur_len = [], [], 0
    for b in blocks:
        n = count_chars(b.get("text", ""))
        if cur and cur_len + n > POLISH_CHUNK_CHARS:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += n
    if cur:
        chunks.append(cur)
    char_json = json.dumps(k.get("character_sheet") or {}, ensure_ascii=False)
    rules_json = json.dumps([r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict)], ensure_ascii=False)
    all_changes, notes = [], []
    for i, ch in enumerate(chunks):
        set_progress(job_id, f"日本語を推敲中…（{i + 1}/{len(chunks)}塊・意味を変えず、耳で入る文へ）")
        prev = "\n\n".join(b.get("text", "") for c in chunks[:i] for b in c)
        nxt = "\n\n".join(b.get("text", "") for c in chunks[i + 1:] for b in c)
        context = ""
        if prev:
            context += "◆ここより前の本文（全文・推敲済み）\n" + prev + "\n"
        if nxt:
            context += "◆ここより後の本文（全文・未推敲）\n" + nxt + "\n"
        if i > 0:
            context = "◆台本タイトル：" + str(script.get("title", "")) + "\n" + context
        try:
            res = extract_json(run_claude(ext.p24_polish(
                json.dumps({"title": script.get("title"), "script_blocks": ch}, ensure_ascii=False),
                char_json, rules_json, context)))
        except Exception as e:
            notes.append(f"塊{i + 1}は推敲を飛ばしました（{str(e)[:80]}）")
            continue
        by_name = {b.get("name"): b.get("text") for b in res.get("script_blocks", []) if b.get("text")}
        for b in ch:
            if b.get("name") in by_name:
                b["text"] = by_name[b["name"]]
        all_changes += res.get("changes", [])
        if res.get("untouched_note"):
            notes.append(res["untouched_note"])
    script["total_chars"] = count_chars(script_text(script))
    prj["script"] = script
    if moved:
        notes.insert(0, f"本文に混ざっていた要確認マーカー{moved}件を要確認リストへ移しました（本文には残しません）")
    prj["polish"] = {"changes": all_changes, "note": "／".join(notes), "chunks": len(chunks),
                     "date": date.today().isoformat()}
    save_project(prj)
    return prj["polish"]


def job_polish(job_id, pid):
    prj = load_project(pid)
    k = project_karte(prj)
    run_polish(job_id, prj, k)
    prj["original_script_text"] = script_text(prj["script"])
    save_project(prj)
    return {"script": prj["script"], "audit": prj.get("audit"), "audit_log": prj.get("audit_log"), "polish": prj["polish"]}


def ref_for_writer(prj):
    """執筆に渡す参考台本。既定は全文（リズム・話題の転換・CTAの温度は全文からしか写せない）。
    settings.ref_mode が "excerpt" なら冒頭・中盤の抜粋に落とす。ref_full_cap は 0（既定）で無制限。"""
    s = load_settings()
    if s.get("ref_mode", "full") != "full":
        return ref_excerpts(prj, 700)
    cap = int(s.get("ref_full_cap", 0))
    out, total = [], 0
    for sc in prj.get("scripts", []):
        if sc.get("kind") == "fail":
            continue
        t = sc["text"]
        room = (cap - total) if cap > 0 else len(t)
        if room <= 0:
            break
        piece = t if len(t) <= room else t[:room] + chr(10) + "（以下略）"
        out.append("◆" + sc["title"] + "（全文）" + chr(10) + piece)
        total += len(piece)
    return (chr(10) * 2 + "---" + chr(10) * 2).join(out)


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


def run_tone(job_id, prj, k, script):
    """口調・語彙の照合。引用は本文と機械照合し、引用できないズレは捨てる。"""
    set_progress(job_id, "参考台本と口調・語彙を照合中…")
    cross = k.get("cross") or prj.get("cross") or {}
    style = {x: cross.get(x) for x in ("style", "teikei", "target") if cross.get(x)}
    res = extract_json(run_claude(ext.p25_tone_check(
        json.dumps({"script_blocks": script.get("script_blocks", [])}, ensure_ascii=False),
        ref_for_writer(prj), json.dumps(style, ensure_ascii=False),
        json.dumps(k.get("character_sheet") or {}, ensure_ascii=False))))
    body = normalize(script_text(script))
    res["mismatches"] = [m for m in res.get("mismatches", [])
                         if len(normalize(m.get("quote", ""))) >= 8 and normalize(m.get("quote", ""))[:40] in body]
    return res


def job_revise_selected(job_id, pid, decisions, extra):
    """AIに直させる項目だけを、該当ブロック単位で書き直す。合格ブロックは触らない。"""
    prj = load_project(pid)
    k = project_karte(prj)
    apply_decisions(prj, decisions)
    save_project(prj)
    items = [it for it in (prj.get("audit") or {}).get("fix_items", []) if it["decision"] == "ai"]
    if not items and not extra.strip():
        raise RuntimeError("AIに直させる項目が選ばれていません（または「その他の修正」が空です）")
    script = prj["script"]
    blocks = script.get("script_blocks", [])
    names = [b.get("name") for b in blocks]
    # 対象ブロックの特定：where がブロック名に一致 → その名前。無ければ引用を含むブロック。どちらも無ければ全ブロック対象
    targets = set()
    lines = ["検品で不合格になった次の項目だけを直してください。それ以外の文は変えないでください。", ""]
    for i, it in enumerate(items, 1):
        hit = None
        if it.get("where") and it["where"] in names:
            hit = it["where"]
        elif it.get("quote"):
            q = normalize(it["quote"])[:20]
            for b in blocks:
                if q and q in normalize(b.get("text", "")):
                    hit = b.get("name")
                    break
        if hit:
            targets.add(hit)
        lines.append(f"{i}. 【{it['label']}：{it['item']}】" + (f" ブロック「{hit}」" if hit else ""))
        if it.get("quote"):
            lines.append(f"   該当箇所：「{it['quote']}」")
        if it.get("why"):
            lines.append(f"   問題：{it['why']}")
        if it.get("hint"):
            lines.append(f"   直し方：{it['hint']}")
        if it.get("note"):
            lines.append(f"   オーナーの指示：{it['note']}")
    if extra.strip():
        lines += ["", "# その他の修正（オーナーの自由記述）", extra.strip()]
    rules = k.get("house_style") or []
    if rules:
        lines += ["", "# このチャンネルの固有ルール（必ず守る）"] + ["- " + (r.get("rule") if isinstance(r, dict) else str(r)) for r in rules]
    instruction = "\n".join(lines)
    before = {b.get("name"): b.get("text", "") for b in blocks}
    partial = bool(targets) and len(targets) < len(blocks) and not extra.strip()
    if partial:
        set_progress(job_id, f"該当ブロック{len(targets)}件だけを書き直し中…（他のブロックは触りません）")
        tj = [b for b in blocks if b.get("name") in targets]
        cj = [{"name": b.get("name"), "text": b.get("text", "")} for b in blocks if b.get("name") not in targets]
        res = extract_json(run_claude(prompts.p6_partial(
            json.dumps(prj.get("blueprint") or {}, ensure_ascii=False), json.dumps(tj, ensure_ascii=False),
            json.dumps(cj, ensure_ascii=False), instruction, prj.get("materials", ""), style_samples_text(k))))
        new = {b.get("name"): b.get("text") for b in res.get("script_blocks", []) if b.get("text")}
        for b in blocks:
            if b.get("name") in targets and b.get("name") in new:
                b["text"] = new[b["name"]]
        script["total_chars"] = count_chars(script_text(script))
        prj["script"] = _copyfix(job_id, prj, script)
    else:
        set_progress(job_id, "台本を修正中…（合格箇所は変えない指示つき）")
        fixed = extract_json(run_claude(prompts.p6_revise(
            json.dumps(prj["cross"], ensure_ascii=False), json.dumps(prj.get("blueprint") or {}, ensure_ascii=False),
            json.dumps(script, ensure_ascii=False), instruction, prj.get("materials", ""), style_samples_text(k))))
        prj["script"] = _copyfix(job_id, prj, fixed)
    fix_lengths(job_id, prj, k)
    changes = []
    for b in prj["script"].get("script_blocks", []):
        old = before.get(b.get("name"), "")
        if old != b.get("text", ""):
            changes.append({"name": b.get("name"), "diff": line_diff(old, b.get("text", ""))})
    prj["last_changes"] = changes
    save_project(prj)
    out = _audit_loop(job_id, prj, k, rounds_override=0)
    out["changes"] = changes
    return out


def fix_lengths(job_id, prj, k):
    """修正後の字数照合。設計図の目安±25%を外れたブロックだけを、字数指定つきで1回書き直す。"""
    bp = prj.get("blueprint") or {}
    targets = {b.get("name"): ext._num(b.get("chars")) for b in bp.get("blocks", []) if ext._num(b.get("chars"))}
    script = prj["script"]
    blocks = script.get("script_blocks", [])
    bad = [b for b in blocks if targets.get(b.get("name")) and
           (count_chars(b.get("text", "")) < targets[b["name"]] * 0.75 or count_chars(b.get("text", "")) > targets[b["name"]] * 1.25)]
    if not bad:
        return 0
    set_progress(job_id, "字数が目安から外れた" + str(len(bad)) + "ブロックを調整中…（" +
                 "／".join(b["name"][:12] + ":" + str(count_chars(b["text"])) + "→" + str(int(targets[b["name"]])) + "字" for b in bad) + "）")
    instruction = ("次のブロックを、設計図の目安字数±20%に収まるように書き直してください。内容・事実・構成・キャラの声は変えず、"
                   "超過なら重複と言い換えを削り、不足なら新しい具体例や手順の分解で埋めてください。" + NL2 +
                   NL2.join("- 「" + b["name"] + "」：現在" + str(count_chars(b["text"])) + "字 → 目安" + str(int(targets[b["name"]])) + "字" for b in bad))
    cj = [{"name": b.get("name"), "text": b.get("text", "")} for b in blocks if b not in bad]
    try:
        res = extract_json(run_claude(prompts.p6_partial(
            json.dumps(bp, ensure_ascii=False), json.dumps(bad, ensure_ascii=False), json.dumps(cj, ensure_ascii=False),
            instruction, prj.get("materials", ""), style_samples_text(k))))
    except Exception:
        return 0
    got = {x.get("name"): x.get("text", "") for x in res.get("script_blocks", []) if x.get("text")}
    fixed = 0
    for b in blocks:
        n = b.get("name")
        if n in got and n in targets:
            if abs(count_chars(got[n]) - targets[n]) < abs(count_chars(b["text"]) - targets[n]):
                b["text"] = got[n]
                fixed += 1
    script["total_chars"] = count_chars(script_text(script))
    prj["script"] = script
    return fixed


def job_audit_only(job_id, pid):
    prj = load_project(pid)
    return _audit_loop(job_id, prj, project_karte(prj))


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


APPLY_CHUNK_CHARS = 1800


def job_apply_rules(job_id, pid, edited_text):
    """人が直した範囲はそのまま残し、最後の変更行より後ろに固有ルールを当てる。約1,800字ずつ。"""
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
    sample = "\n".join(head)
    rules_json = json.dumps(rules, ensure_ascii=False)
    char_json = json.dumps(k.get("character_sheet") or {}, ensure_ascii=False)
    # 行単位で約1,800字の塊に分ける
    chunks, cur, cur_len = [], [], 0
    for line in tail:
        if cur and cur_len + len(line) > APPLY_CHUNK_CHARS:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append(cur)
    out_lines, changes = list(head), []
    for i, ch in enumerate(chunks):
        set_progress(job_id, f"学習したルールを当てています…（{i + 1}/{len(chunks)}塊・{len(rules)}ルール）")
        before_ctx = "\n".join(out_lines)
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
<link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/doc.css"></head><body>
<header><div class="brand" onclick="location.href='/'">🧬 台本一貫スタジオ</div>
<div class="header-right"><a class="btn small ghost" href="/">← ツールに戻る</a></div></header>
<div class="manual">{body}</div></body></html>"""


@app.get("/start")
def start_page():
    p = BASE / "manual" / "はじめに.md"
    if not p.exists():
        return "<p>はじめに.md が見つかりません</p>", 404
    return _doc_page("はじめに", md_to_html(p.read_text(encoding="utf-8")))


@app.get("/guide")
def guide():
    p = BASE / "manual" / "使い方.md"
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
    s.update(data)
    save_settings(s)
    return jsonify(s)


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
            k[key] = data[key]
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
    return jsonify({"job": start_job(job_revise, pid, instruction)})


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


if __name__ == "__main__":
    PROJECTS_DIR.mkdir(exist_ok=True)
    KARTE_DIR.mkdir(exist_ok=True)
    print(f"台本一貫スタジオ: http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
