# -*- coding: utf-8 -*-
"""台本一貫スタジオ 追加プロンプト。

台本DNAスタジオの prompts.py（型抽出・設計図・執筆・検品）はそのまま使い、
ここに「チャンネル設計（コンセプト）」「キャラクター設定」「冒頭30秒レビュー」
「添削差分からのルール抽出」「検品結果→修正指示の組み立て」を足す。
ナレッジは knowledge/ フォルダの md をそのまま注入する（クライアントPC内で完結）。
"""
import json
from pathlib import Path

from prompts import COMMON_RULES, WRITE_RULES, wrap_untrusted, _cross_for_writer, _blueprint_for_writer

BASE = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE / "knowledge"

KNOWLEDGE_FILES = {
    "analysis": "01_分析フレーム.md",
    "blueprint": "02_設計図と執筆.md",
    "gap": "03_不足検出と調査.md",
    "hook": "04_冒頭30秒レビュー.md",
    "body": "05_本編構成.md",
    "concept_canon": "06_コンセプト正本.md",
    "channel_lens": "07_チャンネル6軸.md",
    "channel_sheet": "08_チャンネルコンセプト雛形.md",
    "character": "09_キャラクター設定.md",
    "rubric_howto": "10_採点質問の書き方.md",
    "polish": "11_日本語推敲基準.md",
    "spoken": "12_話し言葉仕上げ.md",
    "honyaku": "13_翻訳調の検出.md",
    "honyaku_table": "14_翻訳調の診断表.md",
    "honyaku_recipes": "15_具体化レシピ.md",
}


def _j(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1)


def knowledge(key, limit=None):
    """knowledge/ の md を読む。無ければ空文字（ツールは動き続ける）。"""
    path = KNOWLEDGE_DIR / KNOWLEDGE_FILES.get(key, "")
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit] if limit else text


def list_knowledge():
    out = []
    for key, fname in KNOWLEDGE_FILES.items():
        p = KNOWLEDGE_DIR / fname
        out.append({"key": key, "file": fname, "exists": p.exists(),
                    "chars": len(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else 0})
    return out


# ---------------------------------------------------------------
# 診断（参考台本から：ジャンル・必要な設計・用意する素材・質問と提案）
# ---------------------------------------------------------------

DIAG_SCHEMA = {
    "genre": "ジャンル（例：老後資金の解説／スカッと系実話／歴史再現／占い／ビジネス心理学 等）",
    "format": "形式（一人語り解説／会話劇／ナレーション付き物語／対談 等）",
    "audience": "想定視聴者を1行で",
    "needs": [{"item": "concept/character/world/materials/research のいずれか",
               "label": "画面表示名（チャンネル設計／キャラクター設定／世界設定／素材／調査）",
               "required": "must/recommended/optional",
               "why": "このジャンル・形式で必要な理由（1行）",
               "proposal": "ユーザーに聞かずにこちらで作る場合の案（1〜3文。作れないなら空文字）",
               "question": "作る前にユーザーに確認したいこと（1問。無ければ空文字）"}],
    "materials_to_prepare": [{"what": "用意する素材（例：本人の実績数字・実例3件・当時の写真）",
                              "why": "無いと台本のどこが弱くなるか", "how": "どこから持ってくるか（本人／調査／既存動画）"}],
    "suggested_memos": {"concept": "コンセプト設計のメモ欄に入れる下書き（2〜4文）",
                        "character": "キャラクター設定のメモ欄に入れる下書き（2〜4文）",
                        "world": "世界設定のメモ欄に入れる下書き（2〜4文。不要な形式なら空文字）"},
    "research_prompt": "このジャンルで事前に調べておくと強くなる内容の、ディープリサーチ用プロンプト（不要なら空文字）",
    "notes": ["その他の気づき（参考台本の偏り・本数不足・失敗例の扱いなど）"],
}


def intent_block(intent):
    """ユーザーが最初に入れた「作りたいネタ・企画の方向性」。全設計プロンプトの先頭に置く。"""
    if not intent or not str(intent).strip():
        return ""
    return f"""
# ユーザーが作りたいネタ・企画の方向性（最優先で反映する）
{wrap_untrusted(intent)}
"""


def p27_diagnose(cross_json, analyses_brief_json, count, intent=""):
    return f"""#PHASE:diagnose
あなたはYouTube台本制作の進行役です。参考台本の分析結果を読み、「このチャンネルはどんなジャンル・形式で、
台本を書くために何を用意すべきか」を診断し、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
# 診断の規律
- needs は concept / character / world / materials / research の5種を必ず全部評価し、required を付ける。
  - 一人語りの解説なら world は optional。会話劇・物語・歴史再現・複数人物が出るなら world は must。
  - 本人の実績・体験談が型の中心にあるなら materials は must。
  - 数字・制度・史実を扱うジャンルなら research は must。
- proposal は「聞かなくても型と分析から妥当に作れる案」。question は「本人しか答えられないこと」だけにする。
  推測で埋められることを質問にしない。逆に、本人の商品・実績・実在人物の事実は質問にする。
- suggested_memos は、そのまま各メモ欄に貼って設計ボタンを押せる文にする。
- 参考台本が{count}本しかない場合の精度の限界は notes に書く。
- ユーザーのネタ・方向性があれば、needs の proposal と suggested_memos はそのネタを前提に書く
  （コンセプトの柱にネタを含める。世界設定のプロットはそのネタの1本目として組む）。
- 文章はすべて日本語。飾りの英語は使わない。
{intent_block(intent)}
# 出力スキーマ
{_j(DIAG_SCHEMA)}

# 横断分析（型・自由度・ターゲット）
{cross_json}

# 各台本の要約（形式・話者・フック・CTA）
{analyses_brief_json}
"""


# ---------------------------------------------------------------
# チャンネル設計（コンセプト）
# ---------------------------------------------------------------

CONCEPT_SCHEMA = {
    "channel_name_idea": "チャンネル名の案（既にある場合はそのまま）",
    "exit_product": "出口（最終的に何を売る／何につなげるか）",
    "audience": {"who": "集める層を1行で", "pain": "その層が抱える痛み", "wish": "その層が本当に欲しい結果"},
    "premise": "視聴者に持っていてほしい前提認識（この認識があると出口が自然に売れる）",
    "promise": "チャンネルが約束すること（入口の期待）",
    "pillars": ["柱となる動画テーマ（3〜5本）"],
    "front": "一番前に出す1つ（サムネ・タイトル・冒頭で必ず打ち出す要素）",
    "worldview": "世界観・語り口・タブー（このチャンネルでは言わないこと）",
    "gap_hooks": ["視聴者を引き込むギャップ・意外性の種（3〜5個）"],
    "score": [{"axis": "①集客/②売れる層/③商品接続/④期待/⑤回収/⑥前出し", "mark": "◎/○/△/×", "why": "理由1行"}],
    "fatal": ["致命傷（あれば。無ければ空配列）"],
    "next_actions": ["次にやること（1〜3個）"],
}


def p20_concept(cross_json, memo, existing="", intent=""):
    """参考台本の型（横断分析）＋オーナーのメモから、チャンネルコンセプト設計書を作る。"""
    ex = f"\n# 既存のコンセプト設計（あれば、これを土台に更新する）\n{wrap_untrusted(existing)}\n" if existing else ""
    return f"""#PHASE:concept
あなたはYouTubeチャンネルの企画設計者です。下のナレッジ（出口逆算のコンセプト設計・チャンネル6軸）に従い、
参考台本の型分析とオーナーのメモから、このチャンネルのコンセプト設計書を作り、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
- 出口（何を売る／何につなげる）から逆算する。出口が不明なら「再生数・登録者」を仮の出口として設計し、fatal にその旨を書く。
- 参考台本の型と矛盾する設計にしない（型は「この層に既に効いている」証拠として扱う）。
- 6軸の採点は甘くしない。△と×には理由を必ず書く。
- ユーザーのネタ・方向性があれば、pillars の1つ目にそのネタを置き、front と gap_hooks もそのネタから作る。
- 文章はすべて日本語。飾りの英語は使わない。
{intent_block(intent)}
# ナレッジ：コンセプト設計の正本（抜粋）
{knowledge("concept_canon")}

# ナレッジ：チャンネル6軸
{knowledge("channel_lens")}

# ナレッジ：設計書の雛形
{knowledge("channel_sheet")}

# 出力スキーマ
{_j(CONCEPT_SCHEMA)}

# 参考台本の横断分析（型・自由度・ターゲット）
{cross_json}

# オーナーのメモ（商品・目的・やりたいこと）
{wrap_untrusted(memo) or "（メモなし）"}
{ex}"""


# ---------------------------------------------------------------
# キャラクター設定
# ---------------------------------------------------------------

CHARACTER_SCHEMA = {
    "name": "キャラクター名（案）",
    "role": "役割（語り手／解説者／相談役 等）",
    "age_gender": "年齢・性別の設定",
    "background": "背景・来歴（なぜこの話をする資格があるか。実話の捏造はせず「設定」として書く）",
    "personality": "性格（3〜5語）",
    "values": "価値観・譲れないもの",
    "gap": "ギャップ（受ける要素。表の顔と裏の顔、弱点、意外な好み等）",
    "boiling_point": "沸点（何に強く反応するか。喜怒哀楽のどこを前に出すか）",
    "speech": {"first_person": "一人称", "second_person": "視聴者の呼び方", "ending": "文末の癖",
               "phrases": ["口癖・決まり文句（3〜5個）"], "rhythm": "話のリズム（短文か長文か・間の取り方）"},
    "never_say": ["このキャラが絶対に言わないこと・言い回し（5個前後）"],
    "sample_lines": ["このキャラらしいセリフの見本（3個・各40字以内）"],
    "appearance": {"face": "顔型", "eyebrow": "眉", "eyes": "目", "hair": "髪型", "body": "体型",
                   "top": "上衣", "prop": "小道具", "color": "主色"},
    "fixed_string": "画像生成用の固定文（外見8項目を1行に）",
}


def p21_character(cross_json, concept_json, memo, existing="", intent=""):
    """コンセプトと型から、台本執筆と検品の両方で使うキャラクター設定書を作る。"""
    ex = f"\n# 既存のキャラクター設定（あれば、これを土台に更新する）\n{wrap_untrusted(existing)}\n" if existing else ""
    return f"""#PHASE:character
あなたはYouTube動画のキャラクター設計者です。下のナレッジ（キャラクター設定14項目）に従い、
チャンネルコンセプトと参考台本の型に合うキャラクターを設計し、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
- 台本執筆と検品の両方で使う。「言わないこと」「口癖」「沸点」は検品項目になるので具体的に書く。
- 参考台本の話者の特徴（人称・文末・口癖）を型として尊重しつつ、同一人物の複製にはしない。
- 受けるキャラの条件：視聴者が「なぜこの人はここまで熱くなるのか」と不思議になるほど沸点がはっきりしていること。
- 外見はオーナーのメモに指定があればそれに従う。無ければコンセプトの世界観から決める。
- ユーザーのネタ・方向性があれば、そのネタを語るのに説得力のある背景・沸点にする。
- 文章はすべて日本語。飾りの英語は使わない。
{intent_block(intent)}
# ナレッジ：キャラクター設定
{knowledge("character")}

# 出力スキーマ
{_j(CHARACTER_SCHEMA)}

# チャンネルコンセプト設計
{concept_json}

# 参考台本の横断分析（話者の特徴・ターゲット）
{cross_json}

# オーナーのメモ（キャラの希望・実在人物の情報など）
{wrap_untrusted(memo) or "（メモなし）"}
{ex}"""


# ---------------------------------------------------------------
# 世界設定（時代背景・状況の経緯・登場人物と相関図・プロット）
# ---------------------------------------------------------------

WORLD_SCHEMA = {
    "era": "時代・時期（現代なら年代と季節感まで）",
    "setting": "舞台（場所・組織・生活環境）",
    "background": "その状況に至った背景・経緯（何が起きて今こうなっているか。3〜6文）",
    "current_situation": "物語が始まる時点の状況（主人公が抱えている問題）",
    "world_rules": ["この世界で成り立つ前提・ルール（制度・お金の流れ・人間関係の常識など。3〜6個）"],
    "cast": [{"name": "登場人物名（語り手を含む）", "role": "役割", "age": "年齢", "position": "立場・肩書",
              "personality": "性格（3語）", "values": "価値観・考え方", "wants": "この人が欲しいもの",
              "fears": "恐れていること",
              "speech": {"first_person": "一人称", "ending": "文末", "phrases": ["口癖（2〜3個）"]},
              "never_say": ["言わないこと（2〜3個）"]}],
    "relations": [{"a": "人物A", "b": "人物B", "relation": "関係（親子／上司部下／元同僚 等）",
                   "feeling_a_to_b": "AがBに抱く感情", "feeling_b_to_a": "BがAに抱く感情",
                   "address": "AはBをどう呼ぶか／BはAをどう呼ぶか", "tension": "二人の間の火種（あれば）"}],
    "plot": {"premise": "一行のあらすじ",
             "beats": [{"no": 1, "beat": "出来事（起承転結の順）", "purpose": "この場面の目的",
                        "who": "関わる人物", "emotion": "視聴者の感情", "reveal": "ここで明かす情報（無ければ空）"}],
             "forbidden_turns": ["やってはいけない展開（設定と矛盾する・キャラが取らない行動）"]},
    "consistency_points": ["台本で必ず守る整合点（時系列・人物の呼び方・知っている情報の範囲など。5〜10個）"],
}


def p26_world(cross_json, concept_json, character_json, memo, existing="", intent=""):
    """時代背景・経緯・登場人物・相関図・プロットを1枚にまとめる。検品の照合元になる。"""
    ex = f"\n# 既存の世界設定（あれば、これを土台に更新する）\n{wrap_untrusted(existing)}\n" if existing else ""
    return f"""#PHASE:world
あなたは物語と解説動画の設定監修者です。チャンネルコンセプト・主人公のキャラクター設定・参考台本の型・
オーナーのメモから、時代背景、その状況に至った経緯、登場人物（複数）、相関図、プロットの流れを
1枚の世界設定書にまとめ、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
- cast の1人目は主人公（語り手）。キャラクター設定書と矛盾させない（一人称・文末・口癖・言わないことを引き継ぐ）。
- 相関図（relations）は cast の全員について、主人公との関係を最低1行ずつ書く。呼び方（address）は検品で照合するので具体的に。
- background は「なぜ今この状況か」が一本の筋で分かるように。時系列の矛盾を作らない。
- plot.beats は参考台本の型（ブロック構成・感情曲線）に沿った順番にする。解説動画なら「場面」でなく「話の流れ」でよい。
- consistency_points は台本の検品項目になる。「誰が何を知っているか」「誰が誰を何と呼ぶか」「起きた順番」を必ず含める。
- 実在の人物・実話が含まれる場合、素材にない事実を創作せず、不明な点は「（設定）」と明記する。
- ユーザーのネタ・方向性があれば、plot はそのネタの1本目として組む。
- 文章はすべて日本語。飾りの英語は使わない。
{intent_block(intent)}
# ナレッジ：キャラクター設定
{knowledge("character")}

# 出力スキーマ
{_j(WORLD_SCHEMA)}

# チャンネルコンセプト
{concept_json}

# 主人公のキャラクター設定
{character_json}

# 参考台本の横断分析（型・感情曲線・ターゲット）
{cross_json}

# オーナーのメモ（時代・舞台・登場人物・出来事の希望）
{wrap_untrusted(memo) or "（メモなし）"}
{ex}"""


def world_checklist(world):
    """世界設定書から整合性の検品項目を作る。"""
    if not world:
        return []
    items = []
    if world.get("era"):
        items.append(f"時代設定（{world['era']}）と矛盾する事物・制度・言葉が本文に無いか")
    if world.get("background"):
        items.append("その状況に至った背景・経緯（設定書）と矛盾する説明を本文でしていないか")
    for rule in (world.get("world_rules") or []):
        items.append(f"世界の前提「{rule}」に反する描写が無いか")
    for c in (world.get("cast") or [])[1:]:
        sp = c.get("speech") or {}
        bits = []
        if sp.get("first_person"):
            bits.append(f"一人称「{sp['first_person']}」")
        if sp.get("ending"):
            bits.append(f"文末「{sp['ending']}」")
        if bits:
            items.append(f"{c.get('name', '')}のセリフが{'・'.join(bits)}で書かれているか（他の人物の口調と混ざっていないか）")
        if c.get("never_say"):
            items.append(f"{c.get('name', '')}が言わないこと（{'／'.join(c['never_say'])}）を言っていないか")
        if c.get("values"):
            items.append(f"{c.get('name', '')}の価値観（{c['values']}）と矛盾する言動が無いか")
    for r in (world.get("relations") or []):
        if r.get("address"):
            items.append(f"{r.get('a', '')}と{r.get('b', '')}の呼び方（{r['address']}）が設定どおりか")
        if r.get("relation"):
            items.append(f"{r.get('a', '')}と{r.get('b', '')}の関係（{r['relation']}）と矛盾する描写が無いか")
    beats = ((world.get("plot") or {}).get("beats") or [])
    if len(beats) >= 2:
        order = "→".join(str(b.get("beat", "")) for b in beats)
        items.append(f"出来事の順番がプロット（{order}）どおりで、前後が入れ替わっていないか")
    for f in ((world.get("plot") or {}).get("forbidden_turns") or []):
        items.append(f"やってはいけない展開「{f}」が本文に無いか")
    for p in (world.get("consistency_points") or []):
        items.append(f"整合点「{p}」が守られているか")
    return items


# ---------------------------------------------------------------
# 設計図の検品（ネタ・ターゲット・コンセプト・キャラ・世界設定が反映されているか）
# ---------------------------------------------------------------

BP_AUDIT_SCHEMA = {
    "audit": [{"item": "検品項目", "result": "○/×",
               "quote": "設計図からの逐語引用（ブロック名や content の一部・20〜60字）",
               "why": "判定理由（×のときは何が欠けているか具体的に）",
               "fix": "×のときの修正指示（設計図のどのブロックをどう直すか。1行）"}],
    "reflection_score": "入力の反映度（0〜100の数値）",
    "summary": "1〜2文の総評",
}


def p28_blueprint_audit(blueprint_json, intent, concept_user_json, pack_json):
    return f"""#PHASE:bp_audit
あなたは台本設計図の検品官です。設計図を書いたのはあなたではありません。
ユーザーの入力（ネタ・ターゲット・目的・要望）とチャンネル設計（コンセプト・キャラ・世界設定）が
設計図に反映されているかを項目ごとに判定し、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
# 検品の規律
- 全項目に設計図からの逐語引用を付ける。引用できない項目を○にしない。
- 迷ったら×。fix は設計図を直す人がそのまま実行できる1行にする。
- チャンネル設計が空の項目（コンセプト無し等）は「未設計のため判定不可」として×にし、fix に「STEP 3 で作成」と書く。

# 検品項目（この順で全部判定する）
1. ネタ・企画の方向性が、タイトルと冒頭ブロックの中身に具体的に出ているか（一般論に薄まっていないか）
2. ターゲット指定（年齢・立場・状況）が、フックと実例の想定人物に反映されているか
3. 目的（再生数／リスト取り／教育）に合ったCTAの位置と内容になっているか
4. ユーザーの要望（memo）が設計図のどこかに反映されているか（要望が空なら○）
5. コンセプトの出口・前に出す1つが、タイトル・冒頭・終盤オファーに一貫して出ているか
6. コンセプトの柱・ギャップの種のいずれかが本編の論点に使われているか
7. キャラクター設定の一人称・文末・沸点・言わないことが、ブロックの constraints か content に反映されているか
8. 世界設定がある場合、プロットの出来事の順番と登場人物・呼び方が設計図のブロック順と一致しているか（無ければ○）
9. 参考台本の型（ブロック構成・感情曲線）を守った上で、上記が反映されているか（型を壊して反映していないか）
10. gaps と user_questions が、ユーザーが実際に答えられる粒度になっているか

{intent_block(intent)}
# ユーザーの入力（テーマ・ターゲット・長さ・目的・要望）
{concept_user_json}

# チャンネル設計（コンセプト・キャラ・世界設定・固有ルール）
{pack_json}

# 出力スキーマ
{_j(BP_AUDIT_SCHEMA)}

# 設計図
{blueprint_json}
"""


def build_bp_fix(audit):
    fixes = [a.get("fix", "") for a in (audit or {}).get("audit", []) if "×" in str(a.get("result", "")) and a.get("fix")]
    fixes = [f for f in fixes if "STEP 3" not in f]
    if not fixes:
        return ""
    return "設計図の検品で不合格になった点だけを直してください。合格している部分は変えないでください。\n" + "\n".join(f"- {f}" for f in fixes)


# ---------------------------------------------------------------
# 冒頭30秒レビュー
# ---------------------------------------------------------------

HOOK_SCHEMA = {
    "score": "100点満点の数値",
    "grade": "S/A/B/C/D",
    "structure": "冒頭の構造（3層：何で止め、何を約束し、何で引っ張るか）",
    "u4": [{"item": "有用性/緊急性/独自性/具体性", "score": "5点満点の数値", "why": "理由1行・本文の引用つき"}],
    "problems": [{"pattern": "検知した失敗パターン名", "quote": "該当箇所の逐語引用（20〜60字）"}],
    "fixes": ["改善提案（3〜5個・具体的な書き換え案を含む）"],
    "retention_forecast": "視聴維持率の予測（1行）",
}


def p22_hook_review(opening_text, concept_json, character_json):
    return f"""#PHASE:hook_review
あなたはYouTube台本の冒頭30秒を診断する専門家です。下のナレッジ（冒頭30秒レビュー基準）に従い、
冒頭部分を採点し、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
- 採点は甘くしない。80点以上は「サムネ・タイトルと一致し、最初の一文で止め、30秒以内に約束が立っている」場合のみ。
- problems の quote は本文の逐語引用（要約禁止）。引用できない問題は書かない。
- fixes はキャラクターの口調・沸点に合う形で書く。

# ナレッジ：冒頭30秒レビュー基準
{knowledge("hook")}

# 出力スキーマ
{_j(HOOK_SCHEMA)}

# チャンネルコンセプト（サムネ・タイトルとの接続確認用）
{concept_json}

# キャラクター設定
{character_json}

# 冒頭部分（最初の約600字）
{opening_text}
"""


# ---------------------------------------------------------------
# キャラクター整合の検品項目（p13_audit のチェックリストに合流させる）
# ---------------------------------------------------------------

def character_checklist(character):
    """キャラ設定書から検品項目を作る。p13_audit の checklist に足す。"""
    if not character:
        return []
    sp = character.get("speech") or {}
    items = []
    if sp.get("first_person"):
        items.append(f"話者の一人称が「{sp['first_person']}」で統一されているか（他の一人称が混ざっていないか）")
    if sp.get("second_person"):
        items.append(f"視聴者の呼び方が「{sp['second_person']}」で統一されているか")
    if sp.get("ending"):
        items.append(f"文末の癖「{sp['ending']}」が台本全体で保たれているか（途中で敬体・常体が切り替わっていないか）")
    never = character.get("never_say") or []
    if never:
        items.append("キャラが絶対に言わないこと（" + "／".join(never) + "）に該当する言い回しが本文に無いか")
    if character.get("boiling_point"):
        items.append(f"キャラの沸点（{character['boiling_point']}）が少なくとも1か所で前に出ているか（淡々と読むだけになっていないか）")
    if character.get("values"):
        items.append(f"キャラの価値観（{character['values']}）と矛盾する主張を本文でしていないか")
    if character.get("background"):
        items.append("キャラの背景設定と矛盾する経歴・体験を本文で語っていないか")
    return items


def house_style_checklist(rules):
    """カルテの固有ルール（添削から学習したもの）を検品項目に変換する。"""
    out = []
    for r in (rules or []):
        text = r.get("rule") if isinstance(r, dict) else str(r)
        if text:
            out.append(f"固有ルール：{text}（違反箇所が無いか）")
    return out


# ---------------------------------------------------------------
# 検品結果 → 修正指示
# ---------------------------------------------------------------

def build_fix_instruction(audit_result, hook_result=None, jev_result=None, house_rules=None, tone_result=None):
    """落ちた項目だけを修正指示に組む。3点セット（どこを／何を参考に／何を材料に）。"""
    lines = ["検品で不合格になった項目だけを直してください。合格している箇所は変えないでください。", ""]
    n = 0
    for a in (audit_result or {}).get("audit", []):
        if "×" in str(a.get("result", "")):
            n += 1
            lines.append(f"{n}. 【{a.get('item', '')}】")
            lines.append(f"   どこを：{a.get('where', '')}「{a.get('quote', '')}」")
            lines.append(f"   何が問題か：{a.get('why', '')}")
    for f in (audit_result or {}).get("fatal", []):
        n += 1
        lines.append(f"{n}. 【重大】{f}")
    if hook_result and _num(hook_result.get("score")) < 70:
        n += 1
        lines.append(f"{n}. 【冒頭30秒】採点{hook_result.get('score')}点。次の改善案を反映：")
        for fx in hook_result.get("fixes", []):
            lines.append(f"   ・{fx}")
        for p in hook_result.get("problems", []):
            lines.append(f"   問題：{p.get('pattern', '')}「{p.get('quote', '')}」")
    for m in (tone_result or {}).get("mismatches", []):
        n += 1
        lines.append(f"{n}. 【口調・語彙のズレ：{m.get('kind', '')}】{m.get('where', '')}「{m.get('quote', '')}」")
        lines.append(f"   参考台本の声：「{m.get('reference', '')}」／直し方：{m.get('fix', '')}（表現のコピーはしない）")
    for j in (jev_result or []):
        if j.get("verdict") in ("×", "要修正"):
            n += 1
            lines.append(f"{n}. 【外部採点：{j.get('label', '')}】値{j.get('value')}（合格ライン未満）。{j.get('hint', '')}")
    if n == 0:
        return ""
    if house_rules:
        lines += ["", "# このチャンネルの固有ルール（添削から学習済み。必ず守る）"]
        for r in house_rules:
            lines.append("- " + (r.get("rule") if isinstance(r, dict) else str(r)))
    return "\n".join(lines)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------
# 添削差分 → ルール抽出（学習）
# ---------------------------------------------------------------

LEARN_SCHEMA = {
    "rules": [{"rule": "次回から守る書き方ルール（1文・判定できる形）",
               "category": "語尾/リズム/言い回し/キャラ口調/構成/事実/その他",
               "before": "添削前の表現（逐語・20〜60字）", "after": "添削後の表現（逐語・20〜60字）",
               "condition": "適用条件（どんな場面で）", "confidence": "high/mid/low"}],
    "keep": ["今回の初稿で良かった点（次回も維持する。1〜3個）"],
    "summary": "この添削の傾向を1〜2文で",
}


def p23_learn_rules(diff_json, character_json, existing_rules_json):
    return f"""#PHASE:learn
あなたは台本の添削パターンを分析する編集者です。AI初稿と人間の手直し後の差分を読み、
「次回の初稿から守るべきルール」を抽出して、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
- ルールは**判定できる形**で書く（「もっと自然に」は不可。「〜という言い回しは〜に言い換える」「1文の読点は1個まで」は可）。
- 1回の添削から抽出するルールは3〜8個。細かすぎる固有名詞の修正や誤字は含めない。
- 既存ルールと同じ主旨のものは出さない（重複を避ける）。既存ルールを強める証拠なら before/after だけ新しくして同じ rule を返してよい。
- 差分が「内容の追加・削除」だけでルールが立たない場合は rules を空配列にして summary に理由を書く。
- confidence：同種の修正が2か所以上あれば high、1か所なら mid、推測なら low。
- 文章はすべて日本語。

# 出力スキーマ
{_j(LEARN_SCHEMA)}

# キャラクター設定（口調のルールはこれと整合させる）
{character_json}

# 既存の固有ルール
{existing_rules_json}

# 添削差分（type: -1=削除された初稿の行, 1=追加された手直しの行, 0=変更なし。変更なし行は前後文脈）
{diff_json}
"""


# ---------------------------------------------------------------
# ブロック単位の執筆（長い台本を一気に書かせない）
# ---------------------------------------------------------------

WRITE_BLOCKS_SCHEMA = {
    "script_blocks": [{"name": "ブロック名（設計図と一字一句同じ）", "text": "本文（話し言葉そのまま）"}],
    "assumptions": ["このブロック群で採った判断（無ければ空配列・各1行）"],
    "kakunin": ["素材に無く書けなかった事実の一覧（[要確認：〜]は本文に書かず、ここに書く）"],
}


def p29_write_blocks(cross_json, blueprint_json, concept_json, target_blocks_json, written_so_far,
                     covered_names, remaining_names, voice_samples, materials="", first=False, last=False):
    mat = (f"\n# 提供素材（あなたの裏取り資料。視聴者に読み上げる原稿ではない。"
           f"注意書き・出典表記・検証メモを本文へ持ち込まないこと）\n{materials}\n") if materials else ""
    prev = (f"\n# ここまでに書いた本文（全文。重複・矛盾を避けるために読む。書き直さない・出力しない）\n{written_so_far}\n"
            if written_so_far else "")
    pos = "台本の冒頭部分" if first else ("台本の締め部分" if last else "台本の途中")
    return f"""#PHASE:write-blocks
あなたはYouTube台本のライターです。台本を**ブロック単位で順に**書いています。今回は{pos}にあたる
「★今回書くブロック」だけを書き、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
{WRITE_RULES}
# 分割執筆の規律（最重要）
- 出力するのは「★今回書くブロック」だけ。前のブロックは出力しない。後のブロックも書かない。
- 各ブロックの name は設計図と一字一句同じにする。
- 各ブロックの文字数は設計図の目安±20%に**必ず**合わせる。今回の分だけに集中し、削って短くしない。
- 直前の本文と自然につなげる（同じ話を繰り返さない、直前で予告したものを受ける）。
- 「これまでに語った内容」にある話題を、新情報なしに繰り返さない。
- 「この後に来るブロック」の内容を先取りして書かない（予告の一文は可）。
- [要確認：〜]や（推定）のマーカーは本文に書かない。書けない事実は kakunin に書き、本文では触れない。
- 参考台本は全文を読み、リズム（一文の長さの緩急・間の取り方）、話題の転換のしかた、CTAの温度を写す。
  ただし文の使い回し（連続20文字以上の一致）は禁止。写すのは「言い方の型」であって「文」ではない。
{mat}
# 参考台本（声・リズム・話題の転換・CTAの温度の参照。表現のコピーは禁止＝連続20文字以上の一致NG）
{voice_samples}

# 出力スキーマ
{_j(WRITE_BLOCKS_SCHEMA)}

# 横断分析（型）
{_cross_for_writer(cross_json)}

# 設計図（全体の骨組み。今回書くのは★の部分だけ）
{_blueprint_for_writer(blueprint_json)}

# 作りたい台本の構想（コンセプト・キャラ・世界設定・固有ルールを含む）
{concept_json}

# これまでに語った内容（ブロック名と要点）
{covered_names or "（まだ無い。これが最初のブロック）"}

# この後に来るブロック（書かない）
{remaining_names or "（これが最後）"}
{prev}
# ★今回書くブロック（これだけを書いて返す）
{target_blocks_json}
"""


# ---------------------------------------------------------------
# 学習したルールを残りに当てる（人が直した部分の添削パターンを、それ以降へ自動適用）
# ---------------------------------------------------------------

APPLY_SCHEMA = {
    "text": "ルールを当てた後の本文（渡された範囲の全文。改行はそのまま）",
    "changes": [{"before": "修正前（逐語・60字以内）", "after": "修正後（逐語・60字以内）", "rule": "当てたルール（1行）"}],
}


def p30_apply_rules(target_text, edited_sample, rules_json, character_json, before_context=""):
    ctx = ("\n# 直前の本文（読むだけ。出力しない）\n" + before_context + "\n") if before_context else ""
    return f"""#PHASE:apply-rules
あなたは台本の添削者です。オーナーが台本の冒頭を自分で手直しし、その添削パターンが「固有ルール」として抽出されました。
今度は**同じ手つきで、まだ手直しされていない範囲**にルールを当ててください。指定スキーマのJSONだけを返します。
{COMMON_RULES}
# 添削の規律
- 直すのは固有ルールに該当する箇所だけ。内容・事実・数字・構成・キャラの声は変えない。新しい事実を足さない。
- オーナーの手直し見本（下）と同じ温度・同じ言い回しの方向で直す。見本の文をコピーはしない。
- ルールに該当しない文は一字も変えない。迷ったら変えない。
- 渡された範囲の本文を**全文**返す（直していない行もそのまま含める）。改行位置は保つ。
- changes には実際に直した箇所だけを逐語で書く。0件なら空配列。

# 固有ルール（今回の添削から学習したものを含む）
{rules_json}

# オーナーの手直し見本（冒頭部分。この手つきを真似る）
{edited_sample}

# キャラクター設定（声を保つ参照）
{character_json}

# 出力スキーマ
{_j(APPLY_SCHEMA)}
{ctx}
# ルールを当てる範囲（この全文を返す）
{target_text}
"""


# ---------------------------------------------------------------
# 日本語推敲（意味を変えずに、耳で聞いて一度で入る日本語へ）
# ---------------------------------------------------------------

POLISH_SCHEMA = {
    "script_blocks": [{"name": "ブロック名（渡されたものと一字一句同じ）", "text": "推敲後の本文"}],
    "changes": [{"where": "ブロック名", "before": "修正前（逐語・60字以内）", "after": "修正後（逐語・60字以内）",
                 "reason": "理由（主述のねじれ/助詞/直訳調/指示語/読点/耳で区別しにくい 等）"}],
    "untouched_note": "直さなかった判断があれば1行（無ければ空文字）",
}


def p24_polish(script_json, character_json, house_rules_json, context=""):
    ctx = ("\n# 前後の文脈（読むだけ。出力しない・直さない。主語や対象を補うときの参照に使う）\n" + context + "\n"
           if context else "")
    return f"""#PHASE:polish
あなたは日本語台本の推敲者です。内容・構成・事実・キャラクターの声を一切変えずに、
「耳で聞いて一度で意味が取れる日本語」へ整え、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
# 推敲の規律
- 事実・数字・因果・否定の範囲・断定の強さを変えない。新しい事実は足さない、削らない。
  ただし**本文の他の箇所で既に言っている主語・対象・比較相手・数字を補って、初めて聞く人に通じる文にするのは推敲の範囲**。
  「どの領域が空いていて」→ 本文がジャンルの話なら「どのジャンルにまだ競合が少なくて」、
  「どこが止められているのか」→ 本文が収益化の話なら「どんな作り方だと収益化を止められるのか」のように、
  文脈から特定できる語で補う。特定できないときは補わず、changes の reason に「要確認：対象が本文にない」と書く。
  台本内で定義していない造語（具体値・導線 等）は視聴者の言葉に置き換える。
- キャラクターの一人称・文末・口癖・意図的な口語は保つ。方言や体言止めを一律に直さない。
  ただし**「キャラの声」を理由に断片の連発を残さない**。キャラ設定の rhythm に説明部の文長（例：2節までの複文で40〜80字）が
  書かれていれば、説明部はその長さの文に整える。「〜3か所。AIに何と打つか。どの数値を入れるか。」のような
  述語のない断片が3つ以上続く箇所は、キャラの口癖・セリフ見本と一致する箇所を除き、必ず1文につなぎ直す。
- 直すのは、主述のねじれ／助詞と動詞の不一致／直訳調・抽象名詞主語／指す先が不明な指示語／
  1文に読点3個以上／耳で区別しにくい並列／不要な名詞化・接続詞／**体言止めや断片の連発（述語のない短い文が3つ以上続く）を1文につなぎ直す**
  に限る。すでに自然な箇所は触らない。
- 渡されたブロックを全部返す（直していないブロックも原文のまま返す）。name は一字一句そのまま。文脈として渡したブロックは返さない。
- changes には実際に直した箇所だけを逐語で書く（要約禁止）。直した数が0なら空配列。
- 台本内の検査ラベルや思考メモを本文に入れない。[要確認：…]・（推定）のマーカーは本文に書かない。
  マーカーが抜かれて文が欠けている箇所は、断定を避けた自然な文（「公式の案内で締め切りを確認してください」等）に整える。
- **比喩で言い換えられた事実は、素材どおりの具体語に戻す**（「入口が狭くなる」→「条件が厳しくなる」、
  「型が小さい」→「やっている人が少ない」、「混んでいる場所」→「同じ作りの動画が多いジャンル」）。
- **行為者を消さない**。「公開されている」「止められた」「調べると」には、誰が（YouTubeが・私が）を補う。
- **言語名で市場や国を指さない**。市場・視聴者・競合の話なら「日本語では」→「日本市場では」、
  「英語で1,550万人」→「海外で1,550万人」。言語そのものの話（字幕・翻訳）のときだけ言語名を使う。

# ナレッジ：日本語推敲基準
{knowledge("polish")}

# ナレッジ：話し言葉仕上げ
{knowledge("spoken")}

# ナレッジ：翻訳調・AI臭の検出（抽象名詞を主語にした比喩動詞、比喩の連鎖、行為者の消失）
{knowledge("honyaku")}

# ナレッジ：翻訳調の診断表（直す表現と、自然なので残す表現の判別）
{knowledge("honyaku_table")}

# ナレッジ：具体化レシピ（事実を足さずに具体的な日本語へ戻す）
{knowledge("honyaku_recipes")}

# キャラクター設定（声を保つための参照）
{character_json}

# このチャンネルの固有ルール（添削から学習済み。守る）
{house_rules_json}

# 出力スキーマ
{_j(POLISH_SCHEMA)}
{ctx}
# 推敲する台本（このブロックだけを直して返す）
{script_json}
"""


# ---------------------------------------------------------------
# 口調・語彙の照合（参考台本との声のズレ・ジャンル特有の言い回し）
# ---------------------------------------------------------------

TONE_SCHEMA = {
    "genre_lexicon": ["参考台本に共通するジャンル特有の言い回し・語彙（5〜15個・逐語）"],
    "mismatches": [{"kind": "人称/文末/口癖/語彙/温度/リズム", "quote": "新作台本の該当箇所（逐語・20〜60字）",
                    "where": "ブロック名", "reference": "参考台本ではどう言うか（逐語・60字以内）",
                    "fix": "直し方（1行）"}],
    "score": "参考台本との声の一致度（0〜100の数値）",
    "summary": "1〜2文の総評",
}


def p25_tone_check(script_json, ref_excerpts, cross_style_json, character_json):
    return f"""#PHASE:tone
あなたは台本の「声」を照合する検品官です。新作台本を参考台本の抜粋と比べ、口調・言葉のニュアンス・
ジャンル特有の言い回しのズレを洗い出し、指定スキーマのJSONだけを返してください。
{COMMON_RULES}
# 照合の規律
- 見るのは**言い方**であって内容ではない。題材やエピソードの違いは指摘しない。
- 人称・文末・口癖・語彙の温度（丁寧／砕けた）・一文の長さとリズム・ジャンル特有の言い回しを比べる。
- mismatches の quote は新作台本からの逐語引用（要約禁止）。引用できないズレは書かない。
- 参考台本の**表現をそのままコピーさせる指示は禁止**（型は真似る・表現は真似ない）。fix は「言い方の方向」を示す。
- キャラクター設定が参考台本と意図的に違う点（設定書にある）はズレとして扱わない。
- ズレが無ければ mismatches を空配列にし、score を高くする。無理に見つけない。

# 横断分析が記録した話者の特徴（人称・文末・口癖・定型句）
{cross_style_json}

# キャラクター設定
{character_json}

# 出力スキーマ
{_j(TONE_SCHEMA)}

# 参考台本の抜粋（声の見本。各本の冒頭と中盤）
{ref_excerpts}

# 新作台本
{script_json}
"""


# ---------------------------------------------------------------
# 注入用テキスト（設計図・執筆・修正に毎回渡す）
# ---------------------------------------------------------------

def channel_pack(karte):
    """カルテからコンセプト・キャラ・固有ルールを1つのJSONに畳んで、concept に同梱できる形にする。"""
    if not karte:
        return {}
    pack = {}
    if karte.get("concept_sheet"):
        pack["channel_concept"] = karte["concept_sheet"]
    if karte.get("character_sheet"):
        pack["character"] = karte["character_sheet"]
    if karte.get("world_sheet"):
        pack["world"] = karte["world_sheet"]
    rules = karte.get("house_style") or []
    if rules:
        pack["house_rules"] = [r.get("rule") if isinstance(r, dict) else str(r) for r in rules]
    return pack


def mock_response_ext(prompt):
    if "#PHASE:concept" in prompt:
        return json.dumps({"channel_name_idea": "モックチャンネル", "exit_product": "無料相談", "audience": {"who": "50代会社員", "pain": "老後資金の不安", "wish": "安心して退職したい"}, "premise": "固定費の見直しが最初", "promise": "退職前3年でやることが分かる", "pillars": ["固定費", "年金", "住まい"], "front": "3年で300万円", "worldview": "淡々と、でも本気", "gap_hooks": ["節約より先に見るべき場所"], "score": [{"axis": "①集客", "mark": "○", "why": "モック"}], "fatal": [], "next_actions": ["参考台本を増やす"]}, ensure_ascii=False)
    if "#PHASE:character" in prompt:
        return json.dumps({"name": "モック先生", "role": "解説者", "age_gender": "50代男性", "background": "元銀行員", "personality": "穏やか・現実的", "values": "数字で語る", "gap": "実は浪費家だった", "boiling_point": "根拠のない不安商法に怒る", "speech": {"first_person": "私", "second_person": "あなた", "ending": "です・ます", "phrases": ["結論から言います"], "rhythm": "短文"}, "never_say": ["絶対に儲かる"], "sample_lines": ["結論から言います。まだ間に合います。"], "appearance": {"face": "面長", "eyebrow": "太め", "eyes": "細め", "hair": "白髪短髪", "body": "中肉", "top": "紺ジャケット", "prop": "電卓", "color": "紺"}, "fixed_string": "面長・太め眉・細め目・白髪短髪・中肉・紺ジャケット・電卓・紺"}, ensure_ascii=False)
    if "#PHASE:diagnose" in prompt:
        return json.dumps({"genre": "老後資金の解説", "format": "一人語り解説", "audience": "50代会社員", "needs": [
            {"item": "concept", "label": "チャンネル設計", "required": "must", "why": "出口が決まっていない", "proposal": "出口を無料相談に置く案", "question": "最終的に売る商品は何ですか"},
            {"item": "character", "label": "キャラクター設定", "required": "must", "why": "話者の人格が型の中心", "proposal": "元銀行員の穏やかな解説者", "question": ""},
            {"item": "world", "label": "世界設定", "required": "optional", "why": "一人語りなので省略可", "proposal": "", "question": ""},
            {"item": "materials", "label": "素材", "required": "must", "why": "実例が型の中心", "proposal": "", "question": "実際の相談事例を3件ください"},
            {"item": "research", "label": "調査", "required": "recommended", "why": "年金制度の数字", "proposal": "", "question": ""}],
            "materials_to_prepare": [{"what": "相談事例3件", "why": "実例ブロックが空になる", "how": "本人"}],
            "suggested_memos": {"concept": "出口は無料相談。50代会社員向け。", "character": "元銀行員の55歳男性。穏やか。", "world": ""},
            "research_prompt": "2026年の年金制度の変更点を調べてください", "notes": ["台本1本のため型は仮説"]}, ensure_ascii=False)
    if "#PHASE:bp_audit" in prompt:
        return json.dumps({"audit": [{"item": "ネタの反映", "result": "○", "quote": "モック", "why": "モック", "fix": ""}, {"item": "ターゲットの反映", "result": "×", "quote": "モック", "why": "年齢が無い", "fix": "フックに45歳以上の会社員という言葉を入れる"}], "reflection_score": 70, "summary": "モック"}, ensure_ascii=False)
    if "#PHASE:world" in prompt:
        return json.dumps({"era": "2026年・春", "setting": "地方都市の会社員家庭", "background": "父の退職を機に家計を見直すことになった", "current_situation": "退職まで3年、貯蓄が足りない", "world_rules": ["固定費は毎月自動で引き落とされる"], "cast": [{"name": "モック先生", "role": "語り手", "age": "55", "position": "元銀行員", "personality": "穏やか", "values": "数字で語る", "wants": "安心", "fears": "不安商法", "speech": {"first_person": "私", "ending": "です・ます", "phrases": ["結論から言います"]}, "never_say": ["絶対に儲かる"]}, {"name": "妻・和子", "role": "相談相手", "age": "53", "position": "パート", "personality": "現実的", "values": "家族第一", "wants": "旅行", "fears": "老後の孤立", "speech": {"first_person": "わたし", "ending": "〜よね", "phrases": ["それでね"]}, "never_say": ["お金の話は嫌"]}], "relations": [{"a": "モック先生", "b": "妻・和子", "relation": "夫婦", "feeling_a_to_b": "頼りにしている", "feeling_b_to_a": "心配", "address": "先生は妻を「和子」、妻は「あなた」", "tension": "家計の主導権"}], "plot": {"premise": "退職前3年で固定費を見直す夫婦の話", "beats": [{"no": 1, "beat": "退職通知が届く", "purpose": "問題提示", "who": "二人", "emotion": "不安", "reveal": ""}, {"no": 2, "beat": "固定費の一覧を作る", "purpose": "解決の入口", "who": "二人", "emotion": "期待", "reveal": "年間36万円の無駄"}], "forbidden_turns": ["宝くじで解決"]}, "consistency_points": ["退職まで3年という時間は変えない", "妻は先生を「あなた」と呼ぶ"]}, ensure_ascii=False)
    if "#PHASE:hook_review" in prompt:
        return json.dumps({"score": 72, "grade": "B", "structure": "モック", "u4": [{"item": "具体性", "score": 3, "why": "モック"}], "problems": [], "fixes": ["数字を冒頭に"], "retention_forecast": "モック"}, ensure_ascii=False)
    if "#PHASE:write-blocks" in prompt:
        try:
            tj = json.loads(prompt.split("# ★今回書くブロック（これだけを書いて返す）\n", 1)[1])
        except Exception:
            tj = []
        return json.dumps({"script_blocks": [{"name": b.get("name"), "text": f"（モック本文：{b.get('name')}）こんにちは。今日も「老後のお金の教科書」を開いていきましょう。" * 2} for b in tj],
                           "assumptions": [], "kakunin": []}, ensure_ascii=False)
    if "#PHASE:apply-rules" in prompt:
        tgt = prompt.split("# ルールを当てる範囲（この全文を返す）\n", 1)[1]
        return json.dumps({"text": tgt.replace("ということです。", "です。", 1), "changes": [{"before": "ということです。", "after": "です。", "rule": "モック"}]}, ensure_ascii=False)
    if "#PHASE:polish" in prompt:
        try:
            src = json.loads(prompt.split("# 台本\n", 1)[1])
            blocks = src.get("script_blocks", [])
        except Exception:
            blocks = []
        return json.dumps({"script_blocks": blocks, "changes": [{"where": blocks[0]["name"] if blocks else "", "before": "モック前", "after": "モック後", "reason": "モック"}], "untouched_note": ""}, ensure_ascii=False)
    if "#PHASE:tone" in prompt:
        return json.dumps({"genre_lexicon": ["老後のお金", "結論から言います"], "mismatches": [], "score": 88, "summary": "モック：声は一致"}, ensure_ascii=False)
    if "#PHASE:learn" in prompt:
        return json.dumps({"rules": [{"rule": "「〜ということです」で締める文を連続させない", "category": "語尾", "before": "〜ということです。〜ということです。", "after": "〜です。〜になります。", "condition": "まとめの段落", "confidence": "mid"}], "keep": ["結論先出し"], "summary": "モック"}, ensure_ascii=False)
    return None
