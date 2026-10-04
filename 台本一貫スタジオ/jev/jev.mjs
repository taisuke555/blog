// Jev（typesafe-ai/jev）を Vercel AI Gateway 経由で呼ぶ共通部品
import { readFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { experimental_evaluate as evaluate, generateText } from 'ai';

// キーの探し方：環境変数 AI_GATEWAY_API_KEY → ~/.config/jev/key.txt
export function loadKey() {
  if (process.env.AI_GATEWAY_API_KEY) return;
  const p = join(homedir(), '.config', 'jev', 'key.txt');
  if (!existsSync(p)) throw new Error(`APIキーが見つかりません。${p} にVercel AI GatewayのキーをUTF-8で保存してください`);
  process.env.AI_GATEWAY_API_KEY = readFileSync(p, 'utf8').replace(/^﻿/, '').trim();
}
loadKey();

const wait = ms => new Promise(r => setTimeout(r, ms));
export const JEV = 'typesafe-ai/jev';
export const WRITER = process.env.JEV_WRITER_MODEL || 'anthropic/claude-sonnet-5';

// 回数制限(429)・混雑(503/529)は待って送り直す
export async function ask({ state, questions, gapMs = 400, maxTry = 10 }) {
  for (let t = 0; ; t++) {
    try { const r = await evaluate({ model: JEV, state, questions, maxRetries: 0 }); await wait(gapMs); return r.answers; }
    catch (e) { const c = e?.statusCode; if (t >= maxTry || !(e?.isRetryable || c === 429 || c >= 500)) throw e; console.error(`  …待機して送り直し（${c}）`); await wait(8000 * (t + 1)); }
  }
}

// 書き手（修正役）のAI。Jevには文章を書かせない
export async function write(prompt) { const { text } = await generateText({ model: WRITER, prompt }); return text.trim(); }

// 点数式の答えから「自信」（最頻段階の確率）を取り出す
export const confidence = a => a.probabilities ? Math.max(...Object.values(a.probabilities)) : (a.probability >= 0.5 ? a.probability : 1 - a.probability);

// 三帯の振り分け：自信が低ければ点数にかかわらず「人が見る」
export function band(a, { pass, low = 0.8 }) {
  const c = confidence(a);
  if (c < low) return '要確認';
  if (a.score !== undefined) return a.score >= pass ? '合格' : '要修正';
  return a.probability >= 0.5 ? '該当' : '非該当';
}
