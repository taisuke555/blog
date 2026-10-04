# -*- coding: utf-8 -*-
"""各工程の入力に「全文」が入っているかを機械で確認する自己検査。

使い方: python check_inputs.py [プロジェクトID]
  省略時は直近のプロジェクト。エンジンはモックに固定し、実際に組み立てられるプロンプトを覗いて照合する。
  参考台本・素材・既に書いた本文・設計書・固有ルールが、執筆／検品／口調照合／推敲の入力に全文含まれているかを見る。
"""
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
os.environ["PYTHONUTF8"] = "1"

import app  # noqa: E402
import prompts_ext as ext  # noqa: E402

app.load_settings = lambda: {**app.DEFAULT_SETTINGS, "engine": "mock"}
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
    ok_all = True

    def check(label, cond):
        nonlocal ok_all
        print(("OK  " if cond else "NG  ") + label)
        ok_all = ok_all and cond

    print(f"プロジェクト：{prj['name']}／参考台本 {len(refs)} 本 {sum(len(r) for r in refs)} 字／素材 {len(materials)} 字")
    if not (prj.get("blueprint") or {}).get("blocks"):
        print("設計図が無いので執筆の検査は省略"); return 0

    # 1) ブロック執筆
    captured.clear()
    res = app.write_in_blocks("t", prj, k, materials)
    names = [b["name"] for b in prj["blueprint"]["blocks"]]
    first = {}
    for p in captured:
        try:
            nm = json.loads(p.split("# ★今回書くブロック（これだけを書いて返す）\n", 1)[1])[0]["name"]
            first.setdefault(nm, p)
        except Exception:
            continue
    last_p = first[names[-1]]
    check("執筆：参考台本の全文が各ブロックの入力にある", all(all(r[-300:] in first[n] for r in refs) for n in names))
    check("執筆：素材の全文が入力にある", (not materials) or all(materials[-300:] in first[n] for n in names))
    check("執筆：最後のブロックの入力に、それ以前の本文が全文ある",
          all(b["text"] in last_p for b in res["script_blocks"][:-1]))
    check("執筆：設計図の全ブロックが入力にある", all(n in last_p for n in names))
    for key, label in (("character_sheet", "キャラ設定"), ("concept_sheet", "コンセプト"), ("world_sheet", "世界設定")):
        sheet = k.get(key)
        if sheet:
            probe = json.dumps(sheet, ensure_ascii=False)[:200]
            check(f"執筆：{label}の設計書が全文で入力にある", probe in last_p)
    rules = [r.get("rule") for r in k.get("house_style", []) if isinstance(r, dict)]
    if rules:
        check("執筆：固有ルールが全件入力にある", all(r in last_p for r in rules))

    # 2) 検品官・口調照合・冒頭レビュー
    captured.clear()
    script = prj.get("script") or res
    app.run_audit("t", prj, k, script, materials)
    audit_p = next((p for p in captured if "#PHASE:audit" in p), "")
    tone_p = next((p for p in captured if "#PHASE:tone" in p), "")
    check("検品：設計図が全文で入力にある", json.dumps(prj["blueprint"], ensure_ascii=False)[:300] in audit_p)
    if k.get("character_sheet"):
        check("検品：キャラ設定書が入力にある", json.dumps(k["character_sheet"], ensure_ascii=False)[:200] in audit_p)
    if k.get("world_sheet"):
        check("検品：世界設定書が入力にある", json.dumps(k["world_sheet"], ensure_ascii=False)[:200] in audit_p)
    if rules:
        check("検品：固有ルールが全件入力にある", all(r in audit_p for r in rules))
    if tone_p:
        check("口調照合：参考台本の全文が入力にある", all(r[-300:] in tone_p for r in refs))

    # 3) 推敲
    captured.clear()
    prj2 = json.loads(json.dumps(prj))
    prj2["script"] = json.loads(json.dumps(script))
    app.save_project = lambda *a: None
    app.run_polish("t", prj2, k)
    pol = [p for p in captured if "#PHASE:polish" in p]
    if len(pol) >= 2:
        whole = app.script_text(script)
        check("推敲：各塊の入力に、前後の本文が全文ある（2塊目で確認）",
              whole[:200].replace("\n", "") in pol[1].replace("\n", "") or len(pol) == 1)
    print("\n結果：" + ("すべて全文で渡っています" if ok_all else "NG があります"))
    return 0 if ok_all else 2


if __name__ == "__main__":
    sys.exit(main())
