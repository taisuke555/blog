# -*- coding: utf-8 -*-
"""各工程の入力が「仕様は全文・参照は予算内」になっているか、1回の入力が上限以内かを機械で確認する自己検査。

使い方: python check_inputs.py [プロジェクトID]
  省略時は直近のプロジェクト。エンジンはモックに固定し、実際に組み立てられるプロンプトを覗いて照合する。

見るもの（README「予算主義」）
  仕様＝全文で入っているか：設計図（対象ブロックの語る中身）・コンセプト・キャラ・世界設定・固有ルール・素材・検品チェックリスト
  参照＝予算内か：参考台本の抜粋（声カードつき）・直前ブロックの末尾・推敲の前後文脈
  上限：各呼び出しの入力が settings.input_cap_chars 以内か。超過していれば、どの工程で何字かを出す
  ナレッジ：冒頭レビュー・推敲の各ナレッジが空でなく注入されているか（ファイル名の正規化問題の検出）
"""
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.environ["PYTHONUTF8"] = "1"

import app  # noqa: E402
import budget  # noqa: E402
import prompts  # noqa: E402
import prompts_ext as ext  # noqa: E402

SETTINGS = {**app.load_settings(), "engine": "mock"}
app.load_settings = lambda: json.loads(json.dumps(SETTINGS))
app.set_progress = lambda *a: None
captured = []
_orig = app.run_claude


def _spy(prompt, force_real=False):
    captured.append(prompt)
    return _orig(prompt, force_real)


app.run_claude = _spy


def main():
    pid = sys.argv[1] if len(sys.argv) > 1 else None
    files = sorted(glob.glob(os.path.join(BASE, "projects", "*", "project.json")), key=os.path.getmtime)
    if not files:
        print("プロジェクトがありません"); return 1
    path = next((f for f in files if pid and pid in f), files[-1])
    prj = json.load(open(path, encoding="utf-8"))
    k = app.project_karte(prj)
    refs = [s["text"] for s in prj["scripts"] if s.get("kind") != "fail"]
    materials = prj.get("materials", "")
    cap = int(SETTINGS.get("input_cap_chars") or 0)
    ok_all = True
    maxes = {}

    def check(label, cond, detail=""):
        nonlocal ok_all
        print(("OK  " if cond else "NG  ") + label + (f"  ({detail})" if detail and not cond else ""))
        ok_all = ok_all and bool(cond)

    def note_sizes(prompts_):
        for p in prompts_:
            ph = budget.phase_of(p)
            maxes[ph] = max(maxes.get(ph, 0), len(p))

    print(f"プロジェクト：{prj['name']}／参考台本 {len(refs)} 本 {sum(len(r) for r in refs):,} 字／素材 {len(materials):,} 字／"
          f"入力上限 {cap:,} 字／参考台本予算 {SETTINGS.get('ref_budget_chars'):,} 字")
    if not (prj.get("blueprint") or {}).get("blocks"):
        print("設計図が無いので執筆以降の検査は省略"); return 0
    rules = [r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict) and r.get("rule")]
    sheets = {"character_sheet": "キャラ設定", "concept_sheet": "コンセプト", "world_sheet": "世界設定"}

    # 1) ブロック執筆
    captured.clear()
    res = app.write_in_blocks("t", prj, k, materials)
    writes = [p for p in captured if p.startswith("#PHASE:write-blocks")]
    note_sizes(captured)
    blocks_bp = prj["blueprint"]["blocks"]
    print(f"\n[執筆] 呼び出し {len(writes)} 回（設計図 {len(blocks_bp)} ブロック）")
    check("執筆：各呼び出しの入力が上限以内", all(len(p) <= cap for p in writes),
          "最大 %s 字" % format(max(len(p) for p in writes), ","))
    check("執筆：参考台本の抜粋と声カードが入っている", all("◆" in p and ("このチャンネルの声" in p or not k.get("cross")) for p in writes))
    check("執筆：参考台本を全文では渡していない（予算主義）", all(not all(r[-300:] in p for r in refs) for p in writes) if sum(len(r) for r in refs) > int(SETTINGS.get("ref_budget_chars") or 0) else True)
    targets_ok = True
    for p in writes:
        try:
            tj = json.loads(p.split(prompts.H_WRITE_TARGET + "\n", 1)[1])
        except Exception:
            targets_ok = False
            continue
        for b in tj:
            if b.get("content") and str(b["content"])[:40] not in p.split(prompts.H_WRITE_TARGET, 1)[0] + p:
                targets_ok = False
    check("執筆：対象ブロックの語る中身が全文で入っている", targets_ok)
    check("執筆：素材が全文で入っている", (not materials) or all(materials[-300:] in p for p in writes))
    for key, label in sheets.items():
        sheet = k.get(key)
        if sheet:
            probe = json.dumps(sheet, ensure_ascii=False)[:200]
            check(f"執筆：{label}の設計書が全文で入っている", all(probe in p for p in writes))
    if rules:
        check("執筆：固有ルールが全件入っている", all(all(r in p for r in rules) for p in writes))
    if len(writes) > 1:
        prev_ok = True
        for i in range(1, len(writes)):
            prev_text = res["script_blocks"][max(0, len([b for w in writes[:i] for b in json.loads(w.split(prompts.H_WRITE_TARGET + "\n", 1)[1])]) - 1)]["text"]
            if prev_text and prev_text[-120:].replace("\n", "") not in writes[i].replace("\n", ""):
                prev_ok = False
        check("執筆：直前ブロックの末尾が次の呼び出しに入っている", prev_ok)

    # 2) 検品官・冒頭レビュー・口調照合
    captured.clear()
    script = prj.get("script") or res
    app.run_audit("t", prj, k, script, materials)
    note_sizes(captured)
    audit_p = next((p for p in captured if p.startswith("#PHASE:audit")), "")
    tone_p = next((p for p in captured if p.startswith("#PHASE:tone")), "")
    hook_p = next((p for p in captured if p.startswith("#PHASE:hook_review")), "")
    print(f"\n[検品] 検品官 {len(audit_p):,} 字／冒頭レビュー {len(hook_p):,} 字／口調照合 {len(tone_p):,} 字")
    check("検品：入力が上限以内", all(len(p) <= cap for p in captured))
    check("検品：設計図の各ブロックの語る中身が入っている", all(str(b.get("content", ""))[:30] in audit_p for b in blocks_bp if b.get("content")))
    for key in ("character_sheet", "world_sheet"):
        if k.get(key):
            check(f"検品：{sheets[key]}書が入っている", json.dumps(k[key], ensure_ascii=False)[:200] in audit_p)
    if rules:
        check("検品：固有ルールが全件チェック項目になっている", all(r in audit_p for r in rules))
    check("検品：素材が全文で入っている", (not materials) or materials[-300:] in audit_p)
    check("検品：台本の全ブロックが全文で入っている（横断項目のため）", all(b.get("text", "")[-80:] in audit_p for b in script.get("script_blocks", []) if b.get("text")))
    check("口調照合の入力が参考台本予算の範囲（tone_ref_chars×1.5以内＋台本）", (not tone_p) or len(tone_p) <= int(SETTINGS.get("tone_ref_chars") or 0) * 1.5 + len(json.dumps(script, ensure_ascii=False)) + 6000)
    check("検品：冒頭レビューのナレッジが注入されている（空でない）", bool(hook_p) and bool(ext.knowledge("hook")) and ext.knowledge("hook")[:60] in hook_p,
          "knowledge/04_冒頭30秒レビュー.md が読めていない可能性")
    if tone_p:
        check("口調照合：参考台本は抜粋（全文ではない）", not all(r[-300:] in tone_p for r in refs) if sum(len(r) for r in refs) > int(SETTINGS.get("tone_ref_chars") or 0) else True)

    # 3) 推敲
    captured.clear()
    prj2 = json.loads(json.dumps(prj))
    prj2["script"] = json.loads(json.dumps(script))
    prj2.pop("polish_state", None)
    app.save_project = lambda *a: None
    app.run_polish("t", prj2, k)
    note_sizes(captured)
    pol = [p for p in captured if p.startswith("#PHASE:polish")]
    print(f"\n[推敲] 呼び出し {len(pol)} 回")
    check("推敲：入力が上限以内", all(len(p) <= cap for p in pol))
    for key, fname in (("polish", "11"), ("spoken", "12"), ("honyaku", "13"), ("honyaku_table", "14"), ("honyaku_recipes", "15")):
        text = ext.knowledge(key)
        check(f"推敲：ナレッジ {ext.KNOWLEDGE_FILES[key]} が注入されている", bool(text) and all(text[:60] in p for p in pol),
              "ファイルが読めていない（NFD/NFC のファイル名問題）か空")
    if len(pol) >= 2:
        check("推敲：2塊目以降に前の塊の末尾が入っている", all("前の塊の末尾" in p for p in pol[1:]))
        whole = app.script_text(script)
        check("推敲：前後の本文を全文では渡していない（予算主義）", not any(whole[:400].replace("\n", "") in p.replace("\n", "") and whole[-400:].replace("\n", "") in p.replace("\n", "") for p in pol))

    print("\n[1回の最大入力（字）]")
    for ph, n in sorted(maxes.items(), key=lambda x: -x[1]):
        print(f"  {ph:16s} {n:>8,} 字（概算 {budget.fmt_tokens(budget.est_tokens('あ' * n))} トークン）" + ("  ← 上限超過" if cap and n > cap else ""))
    print("\n結果：" + ("仕様は全文・参照は予算内で渡っています" if ok_all else "NG があります"))
    return 0 if ok_all else 2


if __name__ == "__main__":
    sys.exit(main())
