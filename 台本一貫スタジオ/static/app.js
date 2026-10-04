/* 台本一貫スタジオ — フロントエンド（7ステップ） */
"use strict";

const $ = (id) => document.getElementById(id);
let PROJECT = null;
let KARTE = [];
let STATE = null;

/* ---------------- 基盤 ---------------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `エラー (${res.status})`);
  return data;
}

function toast(msg, ms = 3000) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  setTimeout(() => t.classList.add("hidden"), ms);
}

function overlay(show, text) {
  $("overlay").classList.toggle("hidden", !show);
  if (text) $("overlay-text").textContent = text;
}

async function runJob(startPath, body, label) {
  overlay(true, label || "処理中…");
  try {
    const { job } = await api(startPath, { method: "POST", body: body || {} });
    while (true) {
      await new Promise((r) => setTimeout(r, 1500));
      const j = await api(`/api/job/${job}`);
      if (j.status === "running") { $("overlay-text").textContent = j.progress || "処理中…"; continue; }
      overlay(false);
      if (j.status === "error") throw new Error(j.error);
      return j.result;
    }
  } catch (e) {
    overlay(false);
    toast("⚠ " + e.message, 7000);
    throw e;
  }
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SUB_LABELS = { who: "誰", pain: "痛み", wish: "欲しい結果", first_person: "一人称", second_person: "視聴者の呼び方", ending: "文末", phrases: "口癖", rhythm: "リズム", face: "顔型", eyebrow: "眉", eyes: "目", hair: "髪型", body: "体型", top: "上衣", prop: "小道具", color: "主色" };
const val = (v) => Array.isArray(v) ? `<ul>${v.map((x) => `<li>${esc(typeof x === "object" ? JSON.stringify(x) : x)}</li>`).join("")}</ul>`
  : (v && typeof v === "object") ? Object.entries(v).map(([k, x]) => `<div><b>${esc(SUB_LABELS[k] || k)}</b>：${esc(Array.isArray(x) ? x.join("／") : x)}</div>`).join("")
  : esc(v);

/* ---------------- 画面切替 ---------------- */

function showHome() {
  $("view-home").classList.remove("hidden");
  $("view-wizard").classList.add("hidden");
  loadHome();
}

function showStage(n) {
  $("view-home").classList.add("hidden");
  $("view-wizard").classList.remove("hidden");
  for (let i = 1; i <= 7; i++) $(`stage-${i}`).classList.toggle("hidden", i !== n);
  document.querySelectorAll(".step").forEach((el) => {
    const s = Number(el.dataset.step);
    el.classList.toggle("active", s === n);
    el.classList.toggle("done", s < n);
    el.onclick = () => { if (s < n || canJump(s)) showStage(s); };
  });
  window.scrollTo(0, 0);
}

function canJump(s) {
  if (!PROJECT) return false;
  if (s <= 2) return !!PROJECT.cross;
  if (s === 3) return !!PROJECT.cross;
  if (s === 4) return !!PROJECT.cross;
  if (s === 5) return !!(PROJECT.script || PROJECT.blueprint);
  if (s === 6) return !!PROJECT.script;
  return false;
}

/* ---------------- ホーム ---------------- */

async function loadHome() {
  STATE = await api("/api/state");
  KARTE = STATE.karte;
  $("engine-badge").textContent = STATE.settings.engine === "mock" ? "エンジン: モック(テスト)" : "エンジン: Claude Code";
  const pl = $("project-list");
  $("first-run").classList.toggle("hidden", STATE.projects.length > 0);
  pl.innerHTML = STATE.projects.length ? "" : '<p class="hint">まだありません。「新しく台本を作る」から始めましょう。</p>';
  STATE.projects.forEach((p) => {
    const d = document.createElement("div");
    d.className = "item";
    d.innerHTML = `<div><b>${esc(p.name)}</b><div class="meta">${esc(p.created)}・台本${p.scripts}本・STEP${p.step}${p.karte ? "・📒" + esc(p.karte) : ""}</div></div><span>開く →</span>`;
    d.onclick = () => openProject(p.id);
    pl.appendChild(d);
  });
  const kl = $("karte-list");
  kl.innerHTML = KARTE.length ? "" : '<p class="hint">まだありません。1回分析すると自動で貯まります。</p>';
  KARTE.forEach((k) => {
    const d = document.createElement("div");
    d.className = "item";
    const marks = [k.has_cross ? "型" : "", k.has_concept ? "コンセプト" : "", k.has_character ? "キャラ" : "", k.has_world ? "世界設定" : ""].filter(Boolean).join("・");
    d.innerHTML = `<div><b>📒 ${esc(k.name)}</b><div class="meta">${marks || "未分析"}／固有ルール${k.rules}件／成功台本${k.success}本／執筆${k.history}回／更新${esc(k.updated)}</div></div><span>この型で新作 →</span>`;
    d.onclick = () => newProject(k.name);
    kl.appendChild(d);
  });
}

async function newProject(karteName = "") {
  const name = prompt("プロジェクト名（例：老後チャンネル・固定費回）", "") || "";
  let intent = "";
  if (karteName) intent = prompt("今回作りたいネタ・企画の方向性（空欄でもOK）", "") || "";
  PROJECT = await api("/api/project", { method: "POST", body: { name, karte: karteName, intent } });
  await openProject(PROJECT.id);
}

async function openProject(pid) {
  PROJECT = await api(`/api/project/${pid}`);
  renderScriptCards(PROJECT.scripts.length ? PROJECT.scripts : [{ title: "", text: "" }]);
  fillKarteSelect();
  if (PROJECT.karte_name) $("karte-select").value = PROJECT.karte_name + ".json";
  $("intent").value = PROJECT.intent || "";
  if (PROJECT.cross) renderCross(PROJECT.cross);
  renderDiag(PROJECT.diagnosis);
  renderChannel();
  if (PROJECT.concept) fillConcept(PROJECT.concept);
  if (PROJECT.blueprint) renderBlueprint(PROJECT.blueprint);
  if (PROJECT.materials) $("materials").value = PROJECT.materials;
  if (PROJECT.script) renderResult({ script: PROJECT.script, audit: PROJECT.audit, audit_log: PROJECT.audit_log, polish: PROJECT.polish });
  if (PROJECT.intent && !(PROJECT.concept && PROJECT.concept.theme)) $("c-theme").value = PROJECT.intent;
  const st = PROJECT.step || 1;
  const k = PROJECT.karte || {};
  const designed = !!(k.concept_sheet && k.character_sheet);
  if (st >= 7) showStage(7), renderFinish(null);
  else if (st >= 6 && PROJECT.script) { prepareReview(); showStage(6); }
  else if (st >= 5 && PROJECT.script) showStage(5);
  else if (st >= 4 && PROJECT.blueprint) showStage(4);
  else if (st >= 3 && PROJECT.cross) showStage(3);
  else if (st >= 2 && PROJECT.cross && designed && PROJECT.intent) showStage(4);
  else if (st >= 2 && PROJECT.cross) showStage(designed ? 3 : 2);
  else showStage(1);
}

/* ---------------- STEP 1 ---------------- */

function renderScriptCards(scripts) {
  $("script-cards").innerHTML = "";
  scripts.forEach((s) => addCard(s.title, s.text, s.kind));
}

function addCard(title = "", text = "", kind = "success") {
  const box = $("script-cards");
  if (box.children.length >= 6) { toast("台本は6本までです（多すぎると型がぼやけます）"); return; }
  const card = document.createElement("div");
  card.className = "script-card" + (kind === "fail" ? " fail" : "");
  card.innerHTML = `
    <div class="card-head">
      <span>📜</span>
      <input type="text" class="c-title" placeholder="タイトル（わかる範囲でOK・空欄可）" value="${esc(title)}">
      <span class="kind"><select class="c-kind"><option value="success">成功例</option><option value="fail">失敗例</option></select></span>
      <button class="del" title="削除">✕</button>
    </div>
    <textarea class="c-text" placeholder="ここに台本（または文字起こし）を貼り付け">${esc(text)}</textarea>
    <div class="chars">0字</div>`;
  const ta = card.querySelector(".c-text");
  const update = () => { card.querySelector(".chars").textContent = `${ta.value.replace(/\s/g, "").length}字`; };
  ta.addEventListener("input", update);
  update();
  const sel = card.querySelector(".c-kind");
  sel.value = kind === "fail" ? "fail" : "success";
  sel.onchange = () => card.classList.toggle("fail", sel.value === "fail");
  card.querySelector(".del").onclick = () => card.remove();
  box.appendChild(card);
}

function collectScripts() {
  return [...document.querySelectorAll(".script-card")]
    .map((c, i) => ({
      title: c.querySelector(".c-title").value.trim() || `台本${i + 1}`,
      text: c.querySelector(".c-text").value.trim(),
      kind: c.querySelector(".c-kind").value,
    }))
    .filter((s) => s.text);
}

function fillKarteSelect() {
  const sel = $("karte-select");
  sel.innerHTML = '<option value="">（新しいチャンネルとして分析する）</option>';
  KARTE.forEach((k) => {
    const o = document.createElement("option");
    o.value = k.file;
    o.textContent = `${k.name}（分析${k.scripts_analyzed}本・ルール${k.rules}件・成功${k.success}本）`;
    sel.appendChild(o);
  });
}

async function onAnalyze() {
  const karteFile = $("karte-select").value;
  const scripts = collectScripts();
  const intent = $("intent").value.trim();
  PROJECT.intent = intent;
  if (scripts.length) {
    await api(`/api/project/${PROJECT.id}/scripts`, { method: "POST", body: { scripts, intent, karte: karteFile ? karteFile.replace(/\.json$/, "") : "" } });
  } else {
    await api(`/api/project/${PROJECT.id}/intent`, { method: "POST", body: { intent } });
  }
  if (intent && !$("c-theme").value.trim()) $("c-theme").value = intent;
  if (karteFile) {
    const r = await api(`/api/project/${PROJECT.id}/use_karte`, { method: "POST", body: { file: karteFile } });
    PROJECT.cross = r.cross;
    PROJECT.karte_name = r.karte.name;
    toast("カルテを読み込みました。分析をスキップします");
    renderCross(PROJECT.cross);
    await refreshProject();
    showStage(2);
    return;
  }
  if (!scripts.length) { toast("台本を1本以上入れてください"); return; }
  const result = await runJob(`/api/project/${PROJECT.id}/analyze`, {}, "分析を開始しています…");
  PROJECT.cross = result.cross;
  PROJECT.karte_name = result.karte.name;
  renderCross(result.cross);
  await refreshProject();
  showStage(2);
}

async function refreshProject() {
  PROJECT = await api(`/api/project/${PROJECT.id}`);
  renderChannel();
}

/* ---------------- STEP 2 ---------------- */

const MODE_LABEL = {
  clone: ["🧪 クローンモード（台本1本）", "warn"],
  hypothesis: ["🔬 仮説モード（台本2本）", "warn"],
  extraction: ["🧬 型抽出モード（台本3本以上）", ""],
};

function renderCross(cross) {
  const [label, cls] = MODE_LABEL[cross.mode] || ["分析結果", ""];
  const mb = $("mode-banner");
  mb.className = "banner " + cls;
  mb.textContent = `${label} — ${cross.mode_note || ""}`;
  $("cross-target").textContent = cross.target || "";
  const kt = $("kata-table");
  kt.innerHTML = "<tr><th>使う</th><th>項目</th><th>型（守るルール）</th><th>根拠</th><th>強さ</th></tr>";
  (cross.kata || []).forEach((k) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><input type="checkbox" ${k.disabled ? "" : "checked"}></td>
      <td><b>${esc(k.item)}</b></td><td>${esc(k.rule)}</td><td>${esc(k.evidence)}</td>
      <td><span class="tag ${k.strength === "weak" ? "weak" : "strong"}">${k.strength === "weak" ? "弱" : "強"}</span></td>`;
    tr.classList.toggle("off", !!k.disabled);
    tr.querySelector("input").addEventListener("change", (e) => { tr.classList.toggle("off", !e.target.checked); k.disabled = !e.target.checked; });
    kt.appendChild(tr);
  });
  const jt = $("jiyudo-table");
  jt.innerHTML = "<tr><th>項目</th><th>台本ごとの違い</th><th>新作での扱い</th></tr>";
  (cross.jiyudo || []).forEach((j) => jt.insertAdjacentHTML("beforeend", `<tr><td><b>${esc(j.item)}</b></td><td>${esc(j.variation)}</td><td>${esc(j.handling)}</td></tr>`));
  $("cross-notes").innerHTML = (cross.notes || []).map((n) => `<li>${esc(n)}</li>`).join("") || "<li>なし</li>";
}

/* ---------------- STEP 2: 診断 ---------------- */

const REQ_LABEL = { must: ["必須", "ng"], recommended: ["推奨", "ok"], optional: ["任意", ""] };

function renderDiag(d) {
  const box = $("diag-view");
  if (!d) { box.innerHTML = ""; return; }
  const needs = d.needs || [];
  box.innerHTML = `
    <div class="banner info">ジャンル：<b>${esc(d.genre)}</b>／形式：${esc(d.format)}／視聴者：${esc(d.audience)}</div>
    <table class="tbl"><tr><th>必要なもの</th><th>要否</th><th>理由</th><th>こちらの提案</th><th>確認したいこと</th></tr>
    ${needs.map((n) => { const [l, c] = REQ_LABEL[n.required] || ["", ""]; return `<tr><td><b>${esc(n.label)}</b></td><td class="${c}">${l}</td><td>${esc(n.why)}</td><td>${esc(n.proposal)}</td><td>${n.question ? "🙋 " + esc(n.question) : ""}</td></tr>`; }).join("")}</table>
    ${(d.materials_to_prepare || []).length ? `<h4>用意する素材</h4><ul>${d.materials_to_prepare.map((m) => `<li><b>${esc(m.what)}</b> — ${esc(m.why)}（${esc(m.how)}）</li>`).join("")}</ul>` : ""}
    ${d.research_prompt ? `<h4>事前調査のプロンプト</h4><textarea rows="4" readonly style="width:100%">${esc(d.research_prompt)}</textarea>` : ""}
    ${(d.notes || []).length ? `<p class="hint">${d.notes.map(esc).join("／")}</p>` : ""}
    <div class="diag-answers">
      <h4>質問への回答（分かる範囲で。空欄なら提案どおり作ります）</h4>
      ${needs.filter((n) => n.question).map((n) => `<label class="lbl">${esc(n.question)}<input type="text" class="diag-ans" data-item="${esc(n.item)}"></label>`).join("") || '<p class="hint">確認事項はありません</p>'}
    </div>
    <div class="actions">
      <button class="btn" id="btn-diag-fill">📝 提案をSTEP 3のメモ欄に入れる（自分で調整して作る）</button>
      <button class="btn primary" id="btn-diag-setup">⑥ 提案どおり一括で作る → チャンネル設計へ</button>
    </div>`;
  const memos = () => {
    const m = { ...(d.suggested_memos || {}) };
    document.querySelectorAll(".diag-ans").forEach((inp) => {
      const v = inp.value.trim();
      if (!v) return;
      const key = inp.dataset.item === "world" ? "world" : inp.dataset.item === "character" ? "character" : "concept";
      m[key] = (m[key] || "") + "\n回答：" + v;
    });
    const worldNeed = needs.find((n) => n.item === "world");
    if (worldNeed && worldNeed.required === "optional" && !(d.suggested_memos || {}).world) m.world = "";
    return m;
  };
  $("btn-diag-fill").onclick = () => {
    const m = memos();
    $("concept-memo").value = m.concept || "";
    $("character-memo").value = m.character || "";
    $("world-memo").value = m.world || "";
    toast("メモ欄に入れました。STEP 3 で各ボタンを押してください");
    showStage(3);
  };
  $("btn-diag-setup").onclick = async () => {
    const m = memos();
    $("concept-memo").value = m.concept || "";
    $("character-memo").value = m.character || "";
    $("world-memo").value = m.world || "";
    await runJob(`/api/project/${PROJECT.id}/setup_all`, { memos: m }, "コンセプト→キャラクター→世界設定を作成中…");
    await refreshProject();
    toast("チャンネル設計を作りました。内容を確認してください");
    showStage(3);
  };
}

async function onDiagnose() {
  const d = await runJob(`/api/project/${PROJECT.id}/diagnose`, {}, "診断中…");
  PROJECT.diagnosis = d;
  renderDiag(d);
}

/* ---------------- STEP 3 ---------------- */

const CONCEPT_LABELS = { channel_name_idea: "チャンネル名", exit_product: "出口", audience: "集める層", premise: "前提認識", promise: "約束", pillars: "柱", front: "前に出す1つ", worldview: "世界観", gap_hooks: "ギャップの種", fatal: "致命傷", next_actions: "次の一手" };
const CHAR_LABELS = { name: "名前", role: "役割", age_gender: "年齢・性別", background: "背景", personality: "性格", values: "価値観", gap: "ギャップ", boiling_point: "沸点", speech: "話し方", never_say: "言わないこと", sample_lines: "セリフ見本", appearance: "外見", fixed_string: "画像用固定文" };

function renderSheet(el, sheet, labels) {
  if (!sheet) { el.innerHTML = '<p class="hint">まだありません。メモを書いて設計ボタンを押してください。</p>'; return; }
  let html = "";
  for (const [k, label] of Object.entries(labels)) {
    if (sheet[k] === undefined || sheet[k] === null || (Array.isArray(sheet[k]) && !sheet[k].length)) continue;
    html += `<div class="row"><b>${label}</b><div>${val(sheet[k])}</div></div>`;
  }
  if (sheet.score) html += `<div class="row"><b>6軸採点</b><div>${sheet.score.map((s) => `<span class="axis">${esc(s.axis)} ${esc(s.mark)}</span>`).join("")}<div class="hint">${sheet.score.map((s) => `${esc(s.axis)}：${esc(s.why)}`).join("／")}</div></div></div>`;
  el.innerHTML = html;
}

function renderWorld(el, w) {
  if (!w) { el.innerHTML = '<p class="hint">まだありません。一人語りの解説なら省略できます。</p>'; return; }
  let html = "";
  const row = (l, v) => v ? `<div class="row"><b>${l}</b><div>${val(v)}</div></div>` : "";
  html += row("時代", w.era) + row("舞台", w.setting) + row("経緯", w.background) + row("今の状況", w.current_situation) + row("世界の前提", w.world_rules);
  if ((w.cast || []).length) html += `<div class="row"><b>登場人物</b><div>${w.cast.map((c) => `<div><b>${esc(c.name)}</b>（${esc(c.role)}・${esc(c.age)}・${esc(c.position)}）性格：${esc(c.personality)}／価値観：${esc(c.values)}／欲しいもの：${esc(c.wants)}／恐れ：${esc(c.fears)}<br><span class="hint">一人称「${esc((c.speech || {}).first_person)}」文末「${esc((c.speech || {}).ending)}」口癖：${esc(((c.speech || {}).phrases || []).join("／"))}／言わないこと：${esc((c.never_say || []).join("／"))}</span></div>`).join("")}</div></div>`;
  if ((w.relations || []).length) html += `<div class="row"><b>相関図</b><div><table class="tbl"><tr><th>A</th><th>B</th><th>関係</th><th>A→B</th><th>B→A</th><th>呼び方</th><th>火種</th></tr>${w.relations.map((r) => `<tr><td>${esc(r.a)}</td><td>${esc(r.b)}</td><td>${esc(r.relation)}</td><td>${esc(r.feeling_a_to_b)}</td><td>${esc(r.feeling_b_to_a)}</td><td>${esc(r.address)}</td><td>${esc(r.tension)}</td></tr>`).join("")}</table></div></div>`;
  const p = w.plot || {};
  if (p.premise || (p.beats || []).length) html += `<div class="row"><b>プロット</b><div>${esc(p.premise || "")}<table class="tbl"><tr><th>#</th><th>出来事</th><th>目的</th><th>人物</th><th>感情</th><th>明かす情報</th></tr>${(p.beats || []).map((b) => `<tr><td>${esc(b.no)}</td><td>${esc(b.beat)}</td><td>${esc(b.purpose)}</td><td>${esc(b.who)}</td><td>${esc(b.emotion)}</td><td>${esc(b.reveal)}</td></tr>`).join("")}</table>${(p.forbidden_turns || []).length ? `<div class="hint">やってはいけない展開：${p.forbidden_turns.map(esc).join("／")}</div>` : ""}</div></div>`;
  html += row("整合点（検品項目）", w.consistency_points);
  el.innerHTML = html;
}

function renderChannel() {
  const k = PROJECT.karte || {};
  const designed = !!(k.concept_sheet && k.character_sheet);
  $("undesigned-banner").classList.toggle("hidden", designed);
  $("stage3-ready").classList.toggle("hidden", !designed);
  $("stage3-banner").classList.toggle("hidden", designed);
  $("stage3-intent").textContent = PROJECT.intent ? `🎯 今回のネタ・方向性：${PROJECT.intent}（設計に反映済みの前提として渡されています）` : "";
  if (!$("concept-memo").value && k.concept_memo) $("concept-memo").value = k.concept_memo;
  renderSheet($("concept-view"), k.concept_sheet, CONCEPT_LABELS);
  renderSheet($("character-view"), k.character_sheet, CHAR_LABELS);
  renderWorld($("world-view"), k.world_sheet);
  $("world-json").value = k.world_sheet ? JSON.stringify(k.world_sheet, null, 2) : "";
  $("concept-json").value = k.concept_sheet ? JSON.stringify(k.concept_sheet, null, 2) : "";
  $("character-json").value = k.character_sheet ? JSON.stringify(k.character_sheet, null, 2) : "";
  renderRules(k.house_style || []);
}

function renderRules(rules) {
  $("rules-count").textContent = `${rules.length}件`;
  const box = $("rules-view");
  box.innerHTML = rules.length ? "" : '<p class="hint">まだありません。STEP 6 で添削すると貯まります。</p>';
  rules.forEach((r, i) => {
    const d = document.createElement("div");
    d.className = "rule";
    d.innerHTML = `<span class="cat">${esc(r.category || "")}</span><div>${esc(r.rule)}${r.before ? `<div class="ex">「${esc(r.before)}」→「${esc(r.after)}」</div>` : ""}</div><button class="del" title="このルールを削除">✕</button>`;
    d.querySelector(".del").onclick = async () => {
      rules.splice(i, 1);
      await api(`/api/project/${PROJECT.id}/channel_save`, { method: "POST", body: { house_style: rules } });
      renderRules(rules);
    };
    box.appendChild(d);
  });
}

async function onConcept() {
  const sheet = await runJob(`/api/project/${PROJECT.id}/concept`, { memo: $("concept-memo").value.trim() }, "コンセプトを設計中…");
  PROJECT.karte.concept_sheet = sheet;
  renderChannel();
}

async function onCharacter() {
  const sheet = await runJob(`/api/project/${PROJECT.id}/character`, { memo: $("character-memo").value.trim() }, "キャラクターを設計中…");
  PROJECT.karte.character_sheet = sheet;
  renderChannel();
}

async function onWorld() {
  const sheet = await runJob(`/api/project/${PROJECT.id}/world`, { memo: $("world-memo").value.trim() }, "世界設定を作成中…");
  PROJECT.karte.world_sheet = sheet;
  renderChannel();
}

async function onChannelSave() {
  const body = {};
  try {
    if ($("concept-json").value.trim()) body.concept_sheet = JSON.parse($("concept-json").value);
    if ($("character-json").value.trim()) body.character_sheet = JSON.parse($("character-json").value);
    if ($("world-json").value.trim()) body.world_sheet = JSON.parse($("world-json").value);
  } catch (e) { toast("JSONの形式が崩れています: " + e.message, 6000); return; }
  await api(`/api/project/${PROJECT.id}/channel_save`, { method: "POST", body });
  await refreshProject();
  toast("💾 保存しました");
}

/* ---------------- STEP 4 ---------------- */

function collectConcept() {
  return {
    theme: $("c-theme").value.trim(),
    target: $("c-target").value.trim() || "コンセプトの集める層と同じ",
    length: $("c-length").value.trim() || "参考台本と同じ",
    goal: $("c-goal").value,
    memo: $("c-memo").value.trim(),
  };
}

function fillConcept(c) {
  $("c-theme").value = c.theme || "";
  $("c-target").value = c.target && !/と同じ$/.test(c.target) ? c.target : "";
  $("c-length").value = c.length && !/と同じ$/.test(c.length) ? c.length : "";
  $("c-goal").value = c.goal || "参考台本と同じ";
  $("c-memo").value = c.memo || "";
}

async function onPropose() {
  const result = await runJob(`/api/project/${PROJECT.id}/propose`, { memo: $("c-memo").value.trim() }, "企画を考えています…");
  const box = $("proposals");
  box.innerHTML = "<p class='hint'>クリックするとテーマ欄に入ります：</p>";
  (result.proposals || []).forEach((p) => {
    const d = document.createElement("div");
    d.className = "prop";
    d.innerHTML = `<b>${esc(p.title)}</b><div class="why">${esc(p.theme)} — ${esc(p.reason)}</div>`;
    d.onclick = () => { $("c-theme").value = p.theme + "（タイトル案：" + p.title + "）"; toast("テーマに設定しました"); };
    box.appendChild(d);
  });
}

async function onReconsider() {
  const theme = $("c-theme").value.trim();
  if (!theme) { toast("テーマを入れてください"); return; }
  await runJob(`/api/project/${PROJECT.id}/reconsider`, { theme }, "このテーマで柱とプロットを更新中…");
  PROJECT.intent = theme;
  await refreshProject();
  toast("コンセプトの柱と世界設定のプロットを更新しました。STEP 3 で確認できます");
}

async function onBlueprint(revise = "") {
  const concept = collectConcept();
  if (!concept.theme) { toast("テーマを入れてください（おまかせ提案も使えます）"); return; }
  const bp = await runJob(`/api/project/${PROJECT.id}/blueprint`, { concept, revise }, "設計図を作成中…");
  PROJECT.blueprint = bp;
  PROJECT.concept = concept;
  renderBlueprint(bp);
}

function renderBpAudit(a) {
  const box = $("bp-audit-box");
  if (!a) { box.innerHTML = ""; return; }
  const rows = a.audit || [];
  const ng = rows.filter((r) => r.result === "×").length;
  let html = `<div class="banner ${ng ? "warn" : ""}">${ng ? `⚠ 設計図の検品：不合格${ng}件（反映度${esc(a.reflection_score)}点）。修正指示欄に書くか、右の直し方を参考に「設計図を修正」してください` : `✅ 設計図の検品合格（反映度${esc(a.reflection_score)}点）`}<div class="hint">${esc(a.summary || "")}</div></div>`;
  html += `<details><summary>検品の内訳（${rows.length}項目）</summary><table class="tbl"><tr><th>項目</th><th>判定</th><th>根拠</th><th>直し方</th></tr>${rows.map((r) => `<tr><td>${esc(r.item)}</td><td class="${r.result === "○" ? "ok" : "ng"}">${esc(r.result)}</td><td>${esc(r.why)}${r.quote ? `<div class="hint">「${esc(r.quote)}」</div>` : ""}</td><td>${esc(r.fix)}</td></tr>`).join("")}</table>`;
  if (a.jev) html += a.jev.skipped ? `<p class="hint">外部採点：スキップ（${esc(a.jev.skipped)}）</p>` : `<h4>外部採点（Jev）</h4><table class="tbl">${(a.jev.rows || []).map((r) => `<tr><td>${esc(r.label)}</td><td class="${r.verdict === "×" ? "ng" : r.verdict === "？" ? "" : "ok"}">${esc(r.verdict)}</td><td>${esc(r.value)}（自信${esc(r.confidence)}）${r.hint ? "／" + esc(r.hint) : ""}</td></tr>`).join("")}</table>`;
  box.innerHTML = html + "</details>";
}

function renderBlueprint(bp) {
  $("bp-area").classList.remove("hidden");
  $("ext-write-box").classList.remove("hidden");
  renderBpAudit(bp.bp_audit || PROJECT.bp_audit);
  $("btn-write").classList.remove("hidden");
  $("bp-title").textContent = "📐 " + (bp.title || "");
  $("bp-meta").textContent = `目標 ${bp.total_chars || "?"}字（約${bp.minutes || "?"}分）／感情曲線：${bp.emotion_curve || ""}`;
  const t = $("bp-table");
  t.innerHTML = "<tr><th>#</th><th>ブロック</th><th>目的</th><th>内容</th><th>文字数</th><th>感情</th><th>使う型</th></tr>";
  (bp.blocks || []).forEach((b) => t.insertAdjacentHTML("beforeend", `<tr><td>${b.no}</td><td><b>${esc(b.name)}</b></td><td>${esc(b.purpose)}</td><td>${esc(b.content)}</td><td>${esc(b.chars)}字</td><td>${esc(b.emotion)}</td><td>${esc(b.kata)}</td></tr>`));
  const gaps = bp.gaps || [];
  $("bp-gaps").innerHTML = gaps.length
    ? gaps.map((g) => `<p>${g.kind === "user" ? "🙋 <b>あなたしか知らない情報</b>" : "🌐 <b>調べれば分かる情報</b>"}：${esc(g.info)} — ${esc(g.how)}</p>`).join("")
    : "<p class='hint'>特にありません。このまま執筆できます。</p>";
  const qs = bp.user_questions || [];
  $("bp-questions").innerHTML = qs.length ? `<div class="banner info">🙋 <b>教えてください：</b><ul style="margin:6px 0 0">${qs.map((q) => `<li>${esc(q)}</li>`).join("")}</ul><span class="hint">答えられるものは下の貼り付け欄に書いてください。</span></div>` : "";
  $("bp-research").innerHTML = bp.research_prompt
    ? `<div class="research-block"><p>🌐 <b>ディープリサーチ用プロンプト</b> — コピーしてお使いのAIの調査機能に貼り、結果を下の欄に貼り付けてから執筆すると精度が上がります</p>
        <textarea rows="5" readonly style="width:100%">${esc(bp.research_prompt)}</textarea>
        <button class="btn small" id="btn-copy-research">📋 リサーチプロンプトをコピー</button></div>` : "";
  const cb = $("btn-copy-research");
  if (cb) cb.onclick = () => { navigator.clipboard.writeText(bp.research_prompt); toast("📋 コピーしました"); };
}

async function onWritePack() {
  try {
    const r = await api(`/api/project/${PROJECT.id}/write_pack`, { method: "POST", body: { materials: $("materials").value.trim() } });
    await navigator.clipboard.writeText(r.pack).catch(() => {});
    const blob = new Blob([r.pack], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "執筆パック.md"; a.click();
    toast(`📋 執筆パック（${r.blocks}ブロック分）をクリップボードにコピーし、ファイルも保存しました。外部AIに貼って書かせ、結果は STEP 5 の「外部AIの出力を取り込む」へ`, 9000);
    PROJECT.step = Math.max(PROJECT.step || 1, 4);
  } catch (e) { toast("⚠ " + e.message, 6000); }
}

async function onImportScript() {
  const text = $("import-text").value;
  if (!text.trim()) { toast("外部AIの出力を貼ってください"); return; }
  const result = await runJob(`/api/project/${PROJECT.id}/import_script`, { text }, "取り込んで検品中…");
  $("import-text").value = "";
  applyResult(result);
}

async function onWrite() {
  const result = await runJob(`/api/project/${PROJECT.id}/write`, { materials: $("materials").value.trim() }, "執筆を開始しています…");
  applyResult(result);
  showStage(5);
}

/* ---------------- STEP 5 ---------------- */

const scriptFullText = (s) => (s.script_blocks || []).map((b) => b.text).join("\n\n");

function applyResult(result) {
  PROJECT.script = result.script;
  PROJECT.audit = result.audit;
  PROJECT.audit_log = result.audit_log;
  PROJECT.polish = result.polish;
  PROJECT.original_script_text = scriptFullText(result.script);
  PROJECT.review = null;
  renderResult(result);
}

function renderPolish(p) {
  const box = $("polish-box");
  if (!p) { box.innerHTML = '<p class="hint">未実施（設定で自動推敲を切っているか、まだ執筆していません）</p>'; return; }
  const ch = p.changes || [];
  box.innerHTML = (ch.length ? `<p class="hint">${ch.length}か所を直しました${p.note ? "／" + esc(p.note) : ""}</p>` : `<p class="hint">直す箇所はありませんでした${p.note ? "／" + esc(p.note) : ""}</p>`) +
    ch.map((c) => `<div class="rule"><span class="cat">${esc(c.reason)}</span><div><span class="hint">${esc(c.where)}</span><div class="ex">「${esc(c.before)}」→「${esc(c.after)}」</div></div></div>`).join("");
}

function renderTone(t) {
  const box = $("tone-box");
  if (!t) { box.innerHTML = '<p class="hint">未実施（設定で口調照合を切っているか、参考台本がありません）</p>'; return; }
  const ms = t.mismatches || [];
  const sc = Number(t.score) || 0;
  box.innerHTML = `<div class="hook-score ${sc < 70 ? "low" : ""}">${sc}点</div><p>${esc(t.summary || "")}</p>` +
    ((t.genre_lexicon || []).length ? `<p class="hint">ジャンル特有の言い回し：${t.genre_lexicon.map(esc).join("／")}</p>` : "") +
    (ms.length ? ms.map((m) => `<div class="rule"><span class="cat">${esc(m.kind)}</span><div><span class="hint">${esc(m.where)}</span>「${esc(m.quote)}」<div class="ex">参考台本の声：「${esc(m.reference)}」／${esc(m.fix)}</div></div></div>`).join("") : '<p class="hint">ズレはありませんでした</p>');
}

const DECISION_LABEL = { ai: "AIに直させる", self: "自分で直す", ignore: "無視" };

function renderFixList(items) {
  const box = $("fix-list");
  items = items || [];
  const active = items.filter((i) => i.decision !== "ignore").length;
  $("fix-count").textContent = `${active}件（無視${items.length - active}）`;
  if (!items.length) { box.innerHTML = '<p class="hint">不合格の項目はありません。</p>'; return; }
  box.innerHTML = items.map((it) => `
    <div class="fix-item ${it.decision}" data-key="${esc(it.key)}">
      <div class="fix-head"><span class="cat">${esc(it.label)}</span><b>${esc(it.item)}</b>${it.where ? `<span class="hint">（${esc(it.where)}）</span>` : ""}</div>
      ${it.quote ? `<div class="ex">「${esc(it.quote)}」</div>` : ""}
      <div class="hint">${esc(it.why)}${it.hint ? "／直し方：" + esc(it.hint) : ""}</div>
      <div class="fix-ctl">
        ${["ai", "self", "ignore"].map((d) => `<label><input type="radio" name="d-${esc(it.key)}" value="${d}" ${it.decision === d ? "checked" : ""}> ${DECISION_LABEL[d]}</label>`).join("")}
        <input type="text" class="fix-note" placeholder="一言メモ（任意。例：数字は3万円に）" value="${esc(it.note || "")}">
      </div>
    </div>`).join("");
  box.querySelectorAll(".fix-item").forEach((el) => {
    el.querySelectorAll("input[type=radio]").forEach((r) => r.onchange = () => { el.className = "fix-item " + r.value; });
  });
}

function collectDecisions() {
  const out = {};
  document.querySelectorAll("#fix-list .fix-item").forEach((el) => {
    const key = el.dataset.key;
    const r = el.querySelector("input[type=radio]:checked");
    out[key] = { decision: r ? r.value : "ai", note: el.querySelector(".fix-note").value };
  });
  return out;
}

function renderChanges(changes) {
  const box = $("fix-changes");
  if (!changes || !changes.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<details open><summary>今回AIが直したブロック（${changes.length}件）の差分</summary>` +
    changes.map((c) => `<h4>${esc(c.name)}</h4><div class="diff">${c.diff.filter((d) => d.type !== 0 || d.text.trim()).map((d) => `<div class="l ${d.type < 0 ? "del" : d.type > 0 ? "ins" : "eq"}">${d.type < 0 ? "−" : d.type > 0 ? "＋" : "　"} ${esc(d.text)}</div>`).join("")}</div>`).join("") + "</details>";
}

function renderResult({ script: s, audit, audit_log, polish, changes }) {
  renderPolish(polish);
  renderFixList((audit || {}).fix_items);
  renderChanges(changes);
  renderTone((audit || {}).tone);
  $("s-title").textContent = s.title || "";
  $("s-chars").textContent = `${s.total_chars || scriptFullText(s).replace(/\s/g, "").length}字`;
  $("s-blocks").innerHTML = (s.script_blocks || []).map((b) => `<div class="block"><span class="bname">${esc(b.name)}</span><p>${esc(b.text)}</p></div>`).join("");
  const a = audit || {};
  const rows = (a.audit || {}).audit || [];
  const ngs = rows.filter((r) => r.result === "×").length + ((a.audit || {}).fatal || []).length;
  const log = audit_log || [];
  const sum = $("audit-summary");
  sum.className = "banner " + (a.ng === 0 ? "" : "warn");
  sum.innerHTML = a.ng === 0
    ? `✅ 自動検品に合格しました（修正${Math.max(0, log.length - 1)}回）`
    : `⚠ 不合格が${a.ng ?? ngs}件残っています（修正${Math.max(0, log.length - 1)}回実施）。修正指示を書くか、STEP 6 で手直ししてください`;
  if (log.length) sum.innerHTML += `<div class="audit-log">${log.map((l) => `${l.round + 1}回目：不合格${l.ng}件・冒頭${l.hook_score ?? "-"}点`).join(" → ")}</div>`;
  const kt = $("audit-table");
  kt.innerHTML = "<tr><th>検品項目</th><th>判定</th><th>根拠</th></tr>";
  rows.forEach((r) => kt.insertAdjacentHTML("beforeend", `<tr><td>${esc(r.item)}</td><td class="${r.result === "○" ? "ok" : "ng"}">${esc(r.result)}</td><td>${esc(r.why)}${r.quote ? `<div class="hint">「${esc(r.quote)}」</div>` : ""}</td></tr>`));
  const fatal = (a.audit || {}).fatal || [];
  $("fatal-box").innerHTML = fatal.length ? `<div class="banner warn">🚨 重大：${fatal.map(esc).join(" ／ ")}</div>` : "";
  const cc = s.copycheck;
  $("copycheck-result").innerHTML = cc ? (cc.violations.length
    ? `<div class="banner warn">⚠ 表現コピー疑い${cc.violations.length}件：${cc.violations.map(esc).join(" ／ ")}</div>`
    : `<div class="banner">✅ 機械コピーチェック合格（${esc(cc.summary)}）</div>`) : "";
  const h = a.hook || {};
  const score = Number(h.score) || 0;
  $("hook-box").innerHTML = h.score !== undefined ? `
    <div class="hook-score ${score < 70 ? "low" : ""}">${score}点 <small>${esc(h.grade || "")}</small></div>
    <p>${esc(h.structure || "")}</p>
    ${(h.u4 || []).map((u) => `<div><b>${esc(u.item)}</b> ${esc(u.score)}/5 — ${esc(u.why)}</div>`).join("")}
    ${(h.problems || []).length ? `<p><b>検知した問題</b></p><ul>${h.problems.map((p) => `<li>${esc(p.pattern)}「${esc(p.quote)}」</li>`).join("")}</ul>` : ""}
    ${(h.fixes || []).length ? `<p><b>改善案</b></p><ul>${h.fixes.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    <p class="hint">${esc(h.retention_forecast || "")}</p>` : '<p class="hint">未採点</p>';
  const j = a.jev;
  $("jev-box").innerHTML = !j ? "" : j.skipped ? `<p class="hint">外部採点：スキップ（${esc(j.skipped)}）</p>`
    : `<h4>外部採点（Jev）</h4><table class="tbl">${(j.rows || []).map((r) => `<tr><td>${esc(r.label)}</td><td class="${r.verdict === "×" ? "ng" : r.verdict === "？" ? "" : "ok"}">${esc(r.verdict)}</td><td>${esc(r.value)}（自信${esc(r.confidence)}）</td></tr>`).join("")}</table>`;
}

async function onRevise() {
  const decisions = collectDecisions();
  const extra = $("s-revise").value.trim();
  const nAi = Object.values(decisions).filter((d) => d.decision === "ai").length;
  if (!nAi && !extra) { toast("AIに直させる項目を選ぶか、その他の修正を書いてください"); return; }
  const result = await runJob(`/api/project/${PROJECT.id}/revise_selected`, { decisions, extra }, `選んだ${nAi}件を修正中…`);
  $("s-revise").value = "";
  applyResult(result);
}

async function saveDecisions() {
  const r = await api(`/api/project/${PROJECT.id}/fix_decisions`, { method: "POST", body: { decisions: collectDecisions() } });
  PROJECT.audit = r.audit;
}

async function onPolish() {
  applyResult(await runJob(`/api/project/${PROJECT.id}/polish`, {}, "日本語を推敲中…"));
}

async function onReaudit() {
  applyResult(await runJob(`/api/project/${PROJECT.id}/audit`, {}, "再検品中…"));
}

/* ---------------- STEP 6 ---------------- */

function renderSelfFix() {
  const items = ((PROJECT.audit || {}).fix_items || []).filter((i) => i.decision === "self");
  const box = $("self-fix-list");
  $("self-fix-panel").classList.toggle("hidden", !items.length);
  box.innerHTML = items.map((it) => `<div class="rule"><span class="cat">${esc(it.label)}</span><div><b>${esc(it.item)}</b>${it.where ? `<span class="hint">（${esc(it.where)}）</span>` : ""}${it.quote ? `<div class="ex">「${esc(it.quote)}」 <button class="btn small find-quote" data-q="${esc(it.quote)}">▶ 該当箇所へ</button></div>` : ""}<div class="hint">${esc(it.why)}${it.hint ? "／直し方：" + esc(it.hint) : ""}${it.note ? "／メモ：" + esc(it.note) : ""}</div></div></div>`).join("");
  box.querySelectorAll(".find-quote").forEach((b) => b.onclick = () => {
    const ta = $("rv-edited");
    const q = b.dataset.q.slice(0, 20);
    const i = ta.value.indexOf(q);
    if (i < 0) { toast("該当箇所が見つかりません（すでに直っている可能性があります）"); return; }
    ta.focus(); ta.setSelectionRange(i, i + b.dataset.q.length);
    const lineNo = ta.value.slice(0, i).split("\n").length;
    ta.scrollTop = Math.max(0, (lineNo - 3) * 24);
  });
}

function prepareReview() {
  renderSelfFix();
  const orig = PROJECT.original_script_text || scriptFullText(PROJECT.script);
  $("rv-original").value = orig;
  if (!$("rv-edited").value.trim() || $("rv-edited").dataset.pid !== PROJECT.id) {
    $("rv-edited").value = (PROJECT.review && PROJECT.review.edited_text) || orig;
    $("rv-edited").dataset.pid = PROJECT.id;
  }
  if (PROJECT.review && PROJECT.review.diff) renderDiff(PROJECT.review.diff);
  else $("diff-view").innerHTML = '<p class="hint">右側を直したら「差分を見る」を押してください。</p>';
  $("learn-view").innerHTML = "";
}

function lineDiff(a, b) {
  const al = a.split("\n"), bl = b.split("\n");
  const n = al.length, m = bl.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = al[i] === bl[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (al[i] === bl[j]) { out.push({ type: 0, text: al[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ type: -1, text: al[i++] });
    else out.push({ type: 1, text: bl[j++] });
  }
  while (i < n) out.push({ type: -1, text: al[i++] });
  while (j < m) out.push({ type: 1, text: bl[j++] });
  return out;
}

function renderDiff(diff) {
  const changed = diff.filter((d) => d.type !== 0).length;
  $("diff-view").innerHTML = (changed ? `<p class="hint">変更 ${changed} 行</p>` : '<p class="hint">差分はありません</p>') +
    diff.filter((d) => d.type !== 0 || d.text.trim()).map((d) => `<div class="l ${d.type < 0 ? "del" : d.type > 0 ? "ins" : "eq"}">${d.type < 0 ? "−" : d.type > 0 ? "＋" : "　"} ${esc(d.text)}</div>`).join("");
}

async function onApplyRules() {
  const edited = $("rv-edited").value;
  const r = await runJob(`/api/project/${PROJECT.id}/apply_rules`, { edited_text: edited }, "学習したルールを残りに当てています…");
  $("rv-edited").value = r.edited_text;
  PROJECT.review = { ...(PROJECT.review || {}), edited_text: r.edited_text, diff: r.diff };
  if (r.diff) renderDiff(r.diff);
  const ch = r.changes || [];
  $("apply-view").innerHTML = `<div class="banner">${ch.length ? `🪄 ${r.boundary}行目より後ろに固有ルールを当て、${ch.length}か所を直しました。右の編集欄に反映済みです。気になる箇所があれば直して、もう一度「差分から学習」を押してください` : `直す箇所はありませんでした${r.note ? "／" + esc(r.note) : ""}`}</div>` +
    ch.map((c) => `<div class="rule"><span class="cat">${esc(c.rule)}</span><div class="ex">「${esc(c.before)}」→「${esc(c.after)}」</div></div>`).join("");
}

async function onLearn() {
  const edited = $("rv-edited").value;
  const result = await runJob(`/api/project/${PROJECT.id}/learn`, { edited_text: edited, note: $("rv-note").value.trim() }, "差分からルールを抽出中…");
  PROJECT.review = { edited_text: edited, diff: result.diff };
  renderDiff(result.diff);
  const rules = result.rules || [];
  $("learn-view").innerHTML = `<div class="banner">${rules.length ? `🧠 ルール${rules.length}件を抽出、新規${result.added}件をカルテへ追加（合計${result.total_rules}件）` : "抽出できるルールはありませんでした"}</div>
    <p class="hint">${esc(result.summary || "")}</p>
    ${rules.map((r) => `<div class="rule"><span class="cat">${esc(r.category || "")}</span><div>${esc(r.rule)}${r.before ? `<div class="ex">「${esc(r.before)}」→「${esc(r.after)}」</div>` : ""}</div></div>`).join("")}
    ${(result.keep || []).length ? `<p><b>次回も維持する点</b></p><ul>${result.keep.map((k) => `<li>${esc(k)}</li>`).join("")}</ul>` : ""}`;
  if (result.house_style) { PROJECT.karte.house_style = result.house_style; renderRules(result.house_style); }
  $("rv-note").value = "";
}

async function onFinish() {
  const fb = prompt("この台本の感想・学び（カルテに記録されます。空欄OK）", "") || "";
  const r = await api(`/api/project/${PROJECT.id}/finish`, { method: "POST", body: { feedback: fb, mark_success: true } });
  renderFinish(r);
  showStage(7);
}

function renderFinish(r) {
  $("finish-box").innerHTML = r
    ? `<h3>💾 カルテ「${esc(r.karte)}」に成功台本として保存しました</h3><p>台本ファイル：<code>${esc(r.file)}</code></p>
       <p class="hint">次回はホームのカルテから「この型で新作」を押すと、分析を飛ばして企画から始められます。固有ルールは次の初稿から自動で効きます。</p>`
    : `<h3>このプロジェクトは完了済みです</h3><p class="hint">台本はプロジェクトフォルダの「台本.txt」にあります。</p>`;
}

/* ---------------- 設定 ---------------- */

function openSettings() {
  const s = STATE.settings;
  $("set-engine").value = s.engine || "claude";
  $("set-rounds").value = s.audit_rounds ?? 2;
  $("set-model").value = s.model || "";
  $("set-write").value = s.write_mode || "blocks";
  $("set-ref").value = s.ref_mode || "full";
  $("set-jev").checked = !!(s.jev && s.jev.enabled);
  $("set-polish").checked = s.auto_polish !== false;
  $("set-tone").checked = s.tone_check !== false;
  $("jev-hint").textContent = STATE.jev_ready ? "（Node と jev/node_modules を確認済み）" : "（Node または jev/node_modules が未準備）";
  $("settings-modal").classList.remove("hidden");
}

async function saveSettings() {
  await api("/api/settings", { method: "POST", body: { engine: $("set-engine").value, model: $("set-model").value.trim(), write_mode: $("set-write").value, ref_mode: $("set-ref").value, audit_rounds: Number($("set-rounds").value), auto_polish: $("set-polish").checked, tone_check: $("set-tone").checked, jev: { enabled: $("set-jev").checked } } });
  $("settings-modal").classList.add("hidden");
  toast("⚙ 設定を保存しました");
  loadHome();
}

/* ---------------- イベント ---------------- */

window.addEventListener("DOMContentLoaded", () => {
  $("brand").onclick = showHome;
  $("btn-new").onclick = () => newProject("");
  $("btn-settings").onclick = openSettings;
  $("btn-settings-close").onclick = () => $("settings-modal").classList.add("hidden");
  $("btn-settings-save").onclick = saveSettings;
  $("btn-ping").onclick = async () => {
    overlay(true, "Claude Codeに接続テスト中…（30秒ほど）");
    try { const r = await api("/api/ping", { method: "POST" }); overlay(false); toast(`✅ 接続OK（${r.seconds}秒）: ${r.reply}`, 5000); }
    catch (e) { overlay(false); toast("❌ " + e.message, 8000); }
  };

  $("btn-add-card").onclick = () => addCard();
  const ingest = (scripts) => {
    if ($("script-cards").children.length === 1 && !document.querySelector(".c-text").value) $("script-cards").innerHTML = "";
    scripts.forEach((s) => addCard(s.title, s.text));
    toast(`${scripts.length}ファイルを読み込みました`);
  };
  $("file-input").addEventListener("change", async (e) => {
    const fd = new FormData();
    [...e.target.files].forEach((f) => fd.append("files", f));
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) { toast("⚠ " + data.error); return; }
    ingest(data.scripts);
  });
  $("btn-folder").onclick = async () => {
    try { ingest((await api("/api/read_folder", { method: "POST", body: { path: $("folder-path").value } })).scripts); }
    catch (e) { toast("⚠ " + e.message); }
  };
  $("btn-analyze").onclick = onAnalyze;
  $("btn-back-1").onclick = () => showStage(1);
  $("btn-to-3").onclick = () => showStage(3);
  $("btn-diagnose").onclick = onDiagnose;
  $("btn-back-2").onclick = () => showStage(2);
  $("btn-concept").onclick = onConcept;
  $("btn-character").onclick = onCharacter;
  $("btn-world").onclick = onWorld;
  $("btn-channel-save").onclick = onChannelSave;
  $("btn-to-4").onclick = () => showStage(4);
  $("btn-back-3").onclick = () => showStage(3);
  $("btn-propose").onclick = onPropose;
  $("btn-blueprint").onclick = () => onBlueprint("");
  $("btn-reconsider").onclick = onReconsider;
  $("btn-go-setup").onclick = async () => {
    if (!PROJECT.diagnosis) { showStage(2); toast("まず「診断する」を押してください"); return; }
    const m = { ...(PROJECT.diagnosis.suggested_memos || {}) };
    const w = (PROJECT.diagnosis.needs || []).find((n) => n.item === "world");
    if (w && w.required === "optional" && !m.world) m.world = "";
    await runJob(`/api/project/${PROJECT.id}/setup_all`, { memos: m }, "コンセプト→キャラクター→世界設定を作成中…");
    await refreshProject();
    toast("チャンネル設計を作りました。STEP 3 で確認してから設計図を作り直してください");
    showStage(3);
  };
  $("btn-bp-revise").onclick = () => { const r = $("bp-revise").value.trim(); if (!r) { toast("修正指示を入れてください"); return; } $("bp-revise").value = ""; onBlueprint(r); };
  $("btn-write").onclick = onWrite;
  $("btn-write-pack").onclick = onWritePack;
  $("btn-import").onclick = onImportScript;
  $("stepnav").addEventListener("click", () => {});
  $("btn-back-4").onclick = () => showStage(4);
  $("btn-copy").onclick = () => { navigator.clipboard.writeText(`${PROJECT.script.title}\n\n${scriptFullText(PROJECT.script)}`); toast("📋 台本をコピーしました"); };
  $("btn-s-revise").onclick = onRevise;
  $("btn-reaudit").onclick = onReaudit;
  $("btn-polish").onclick = onPolish;
  $("btn-to-6").onclick = async () => { try { await saveDecisions(); } catch (e) {} prepareReview(); showStage(6); };
  $("btn-back-5").onclick = () => showStage(5);
  $("btn-diff").onclick = () => renderDiff(lineDiff($("rv-original").value, $("rv-edited").value));
  $("btn-learn").onclick = onLearn;
  $("btn-apply-rules").onclick = onApplyRules;
  $("btn-finish").onclick = onFinish;
  $("btn-home").onclick = showHome;

  showHome();
});
