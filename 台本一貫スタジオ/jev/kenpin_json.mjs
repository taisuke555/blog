// 台本一貫スタジオ用：採点基準(JSON)と原稿(UTF-8)を受け取り、判定をJSONで標準出力に返す
// 使い方: node kenpin_json.mjs --rubric rubrics/daihon.json --in 原稿.txt
// 出力: [{key,label,value,confidence,verdict,hint}]
import { readFileSync } from 'node:fs';
import { ask, band, confidence } from './jev.mjs';

const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const strip = s => s.replace(/^﻿/, '');
const rubric = JSON.parse(strip(readFileSync(arg('--rubric'), 'utf8')));
const text = strip(readFileSync(arg('--in'), 'utf8'));

const questions = Object.fromEntries(Object.entries(rubric.items).map(([k, v]) =>
  [k, { type: v.type, instructions: v.instructions, ...(v.criteria ? { criteria: v.criteria } : {}) }]));
const a = await ask({ state: (rubric.maegaki || '') + text, questions });
const rows = [];
for (const [k, v] of Object.entries(rubric.items)) {
  const ans = a[k];
  const val = ans.score !== undefined ? ans.score : ans.choice !== undefined ? ans.choice : ans.probability;
  const b = band(ans, { pass: v.pass ?? 0.5 });
  let verdict = '○';
  if (b === '要確認') verdict = '？';
  else if (b === '要修正' || (v.bad && b === '該当')) verdict = '×';
  rows.push({ key: k, label: v.label, value: typeof val === 'number' ? +val.toFixed(2) : val,
    confidence: +confidence(ans).toFixed(2), verdict, hint: v.hint || '' });
}
process.stdout.write(JSON.stringify(rows));
