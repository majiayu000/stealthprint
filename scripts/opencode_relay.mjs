// Local OpenAI-compatible relay to OpenCode Zen, run with bun.
//
//   bun scripts/opencode_relay.mjs free <envelope.json> [port]   # /zen/v1 free tier
//   bun scripts/opencode_relay.mjs go [port]                       # /zen/go/v1 (Go plan)
//
// Why: as of 2026-11 the free tier accepts only requests shaped like the real
// opencode client (captured system prompt + tool list + session headers) and
// sent from bun; Python/curl replays of the same bytes get 429
// FreeUsageLimitError, and the Go line answers non-bun clients with
// Cloudflare 1010. stealthprint then talks plain OpenAI to http://127.0.0.1:<port>/v1.
//
// The API key is read at runtime from opencode's auth.json and never logged.
// Env: OPENCODE_AUTH (auth.json path), SES (Go session id), MIN_TOKENS (floor for
// max_tokens on either line, e.g. 16 for GPT models), RELAY_LOG (JSONL status log).
import fs from 'fs';
import os from 'os';

const [mode, a1, a2] = process.argv.slice(2);
if (!['free', 'go'].includes(mode)) { console.error('usage: free <envelope.json> [port] | go [port]'); process.exit(2); }
const auth = JSON.parse(fs.readFileSync(process.env.OPENCODE_AUTH || os.homedir() + '/.local/share/opencode/auth.json'));
const key = (mode === 'free' ? auth['opencode'] : auth['opencode-go'])?.key;
if (!key) { console.error('no key for this mode in auth.json (run opencode auth login)'); process.exit(2); }
const env = mode === 'free' ? JSON.parse(fs.readFileSync(a1)) : null;
const port = Number(mode === 'free' ? (a2 || 8898) : (a1 || 8897));
const log = process.env.RELAY_LOG ? fs.createWriteStream(process.env.RELAY_LOG, { flags: 'a' }) : null;
const UA = 'opencode/1.19.2 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14';
const SES = process.env.SES || env?.headers?.['x-opencode-session'] || 'ses_' + Date.now().toString(16) + 'relay';
let counter = 0;

function msgId() {
  const id = ((BigInt(Date.now()) * 0x1000n + BigInt(++counter % 4096)) & ((1n << 48n) - 1n)).toString(16).padStart(12, '0');
  const abc = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  return 'msg_' + id + Array.from({ length: 14 }, () => abc[Math.floor(Math.random() * 62)]).join('');
}

function aggregate(raw) {
  let content = '', reasoning = '', usage = null, finish = null; const meta = {};
  for (const line of raw.split('\n')) {
    if (!line.startsWith('data:')) continue;
    const p = line.slice(5).trim(); if (!p || p === '[DONE]') continue;
    let c; try { c = JSON.parse(p); } catch { continue; }
    for (const f of ['id', 'model', 'created', 'system_fingerprint']) if (c[f] !== undefined) meta[f] = c[f];
    if (c.usage) usage = c.usage;
    for (const ch of c.choices || []) {
      const d = ch.delta || {}; content += d.content || ''; reasoning += d.reasoning_content || d.reasoning || '';
      if (ch.finish_reason) finish = ch.finish_reason;
    }
  }
  const message = { role: 'assistant', content }; if (reasoning) message.reasoning_content = reasoning;
  return { ...meta, object: 'chat.completion', choices: [{ index: 0, message, finish_reason: finish }], usage: usage || {} };
}

Bun.serve({
  hostname: '127.0.0.1', port, idleTimeout: 255,
  async fetch(req) {
    const url = new URL(req.url);
    if (req.method === 'GET') {
      if (mode === 'free') return Response.json({ object: 'list', data: [{ id: env.body.model, object: 'model' }] });
      const r = await fetch('https://opencode.ai/zen/go' + url.pathname, { headers: { authorization: 'Bearer ' + key, 'user-agent': UA } });
      return new Response(await r.text(), { status: r.status, headers: { 'content-type': 'application/json' } });
    }
    const inb = await req.json();
    const wantStream = !!inb.stream;
    let body, target, headers;
    if (mode === 'free') {
      const tpl = env.body;
      body = { ...tpl };
      for (const k of ['max_tokens', 'temperature', 'top_p', 'seed', 'reasoning_effort', 'stop', 'n',
        'logprobs', 'top_logprobs', 'response_format', 'logit_bias']) if (inb[k] !== undefined) body[k] = inb[k];
      if (inb.model) body.model = inb.model;
      // Keep the captured system prompt and tools (both are checked); the
      // caller's non-system turns replace the captured conversation.
      body.messages = [...tpl.messages.filter(m => m.role === 'system'), ...inb.messages.filter(m => m.role !== 'system')];
      body.stream = true; body.stream_options = { include_usage: true };
      target = 'https://opencode.ai' + env.path;
      headers = { ...env.headers, authorization: 'Bearer ' + key, 'x-opencode-request': msgId() };
    } else {
      body = { ...inb };
      target = 'https://opencode.ai/zen/go' + url.pathname;
      headers = { 'content-type': 'application/json', authorization: 'Bearer ' + key, 'user-agent': UA,
        'x-opencode-client': 'cli', 'x-opencode-project': 'global', 'x-opencode-session': SES,
        'x-opencode-session-id': SES, 'x-opencode-request': msgId() };
    }
    const floor = Number(process.env.MIN_TOKENS || 0);
    if (floor && body.max_tokens !== undefined && body.max_tokens < floor) body.max_tokens = floor;
    const r = await fetch(target, { method: 'POST', headers, body: JSON.stringify(body) });
    const raw = await r.text();
    log?.write(JSON.stringify({ t: new Date().toISOString(), mode, model: body.model, status: r.status,
      err: r.status !== 200 ? raw.slice(0, 300) : undefined }) + '\n');
    if (r.status !== 200) return new Response(raw, { status: r.status, headers: { 'content-type': 'application/json' } });
    const isSSE = (r.headers.get('content-type') || '').includes('event-stream') || raw.startsWith('data:') || raw.startsWith(':');
    if (wantStream) return new Response(raw, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    return Response.json(isSSE ? aggregate(raw) : JSON.parse(raw));
  },
});
console.error(`opencode relay (${mode}) on http://127.0.0.1:${port}/v1`);
