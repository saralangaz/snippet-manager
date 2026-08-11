/**
 * Pure, DOM-free logic shared by the app UI and the test suite.
 *
 * Nothing in this file touches `document`, `window`, `localStorage`, or
 * `fetch` — every function here is a plain input -> output transformation,
 * which is what makes it possible to unit test in plain Node with no
 * browser, no DOM shim, and no build step. DOM-coupled code (rendering,
 * event handlers, fetch calls) stays in index.html.
 *
 * Loaded as a classic <script src="logic.js"> in the browser (attaches to
 * `window`) and as a CommonJS module under Node for tests.
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    Object.assign(root, factory());
  }
})(typeof window !== 'undefined' ? window : globalThis, function () {

  // ══════════════════════════════════════════════════════════════════════
  // ESCAPING
  // ══════════════════════════════════════════════════════════════════════
  // One implementation, safe for both text-node and attribute-value
  // contexts (a strict superset of what either alone needs) — two different
  // escaping mechanisms for the same job is how XSS gaps sneak in.
  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  const escapeAttr = escapeHtml;

  // ══════════════════════════════════════════════════════════════════════
  // CATEGORY DETECTION — mirrors the server-side heuristic in app.py.
  // First tries the base command, then scans the whole command for a
  // keyword (so shell builtins like `source`/`cd`/`export` still land
  // somewhere sensible), and never falls back to an unhelpful literal
  // "General".
  // ══════════════════════════════════════════════════════════════════════
  const CATEGORY_HINTS = [
    { test: /^(kubectl|helm)\b/, label: 'Kubernetes' },
    { test: /^docker(-compose)?\b/, label: 'Docker' },
    { test: /^git\b/, label: 'Git' },
    { test: /^(python3?|pip3?|pytest|venv|virtualenv|black|ruff|flake8|mypy|poetry)\b/, label: 'Python' },
    { test: /^(npm|npx|yarn|pnpm|node)\b/, label: 'JavaScript / Node' },
    { test: /^curl\b/, label: 'API / cURL' },
    { test: /^aws\b/, label: 'AWS CLI' },
    { test: /^az\b/, label: 'Azure CLI' },
    { test: /^(find|grep|chmod|chown|ps|kill|df|du|lsof|awk|sed|tar|ssh|scp|rsync|xargs)\b/, label: 'Bash / Linux' },
  ];
  const CATEGORY_KEYWORDS = [
    { test: /\b(venv|pip3?|pytest|activate|poetry|pyproject|requirements\.txt|virtualenv|django)\b/, label: 'Python' },
    { test: /\b(npm|npx|yarn|pnpm|node|package\.json)\b/, label: 'JavaScript / Node' },
    { test: /\b(kubectl|k8s|kubeconfig)\b/, label: 'Kubernetes' },
    { test: /\b(docker|dockerfile|container)\b/, label: 'Docker' },
    { test: /\b(git|commit|rebase|checkout)\b/, label: 'Git' },
    { test: /\bcurl\b|https?:\/\//, label: 'API / cURL' },
    { test: /\baws\b/, label: 'AWS CLI' },
    { test: /\baz\b|\bazure\b/, label: 'Azure CLI' },
  ];

  function suggestCategoryLabel(command) {
    const cmd = command.trim();
    for (const h of CATEGORY_HINTS) if (h.test.test(cmd)) return h.label;
    for (const h of CATEGORY_KEYWORDS) if (h.test.test(cmd)) return h.label;
    return 'Bash / Linux';
  }

  // ══════════════════════════════════════════════════════════════════════
  // CATEGORY DOT COLOR HASHING
  // ══════════════════════════════════════════════════════════════════════
  function hashString(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  // ══════════════════════════════════════════════════════════════════════
  // LIGHT VARIABLE GUESSING — for the "+" quick single-add only.
  // Deliberately simple: a token right after a flag is a variable
  // candidate, nothing fancier. Bulk/library-quality snippets come from
  // AI-generated Import instead of a bigger pattern library here.
  // ══════════════════════════════════════════════════════════════════════
  const FLAG_NAME_OVERRIDES = {
    '-n': 'namespace', '-p': 'port', '-f': 'file', '-t': 'tag',
    '-o': 'output', '-c': 'count', '-u': 'user',
  };

  function sanitizeName(str) {
    return str.replace(/^-+/, '').replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();
  }

  function nameForFlag(flagTok) {
    return FLAG_NAME_OVERRIDES[flagTok.toLowerCase()] || sanitizeName(flagTok) || 'value';
  }

  // Light naming assist for a token the user manually clicks to mark as a
  // variable (no flag context to go on) — a couple of obvious shape hints,
  // not a detection heuristic.
  function genericNameForToken(tok) {
    if (/^\d+$/.test(tok)) return 'port';
    if (/\.[a-z][a-z0-9]{0,5}$/i.test(tok)) return 'file';
    if (tok.includes('/')) return 'path';
    return 'value';
  }

  function uniqueName(base, used) {
    let name = base, n = 2;
    while (used.has(name)) { name = `${base}_${n}`; n++; }
    used.add(name);
    return name;
  }

  // Split on whitespace, but keep a quoted "..."/'...' span as a single raw
  // token even though it contains spaces — e.g. curl -H "Authorization:
  // Bearer x" must not shatter into three garbage tokens.
  function tokenizeRaw(command) {
    const out = [];
    const re = /"[^"]*"|'[^']*'|\S+|\s+/g;
    let m;
    while ((m = re.exec(command))) out.push({ text: m[0], isWhitespace: /^\s+$/.test(m[0]) });
    return out;
  }

  // Best-guess first pass for a raw, unmarked command: only "token right
  // after a flag" is treated as a variable.
  function guessCandidateSegments(command) {
    const segs = tokenizeRaw(command).map(s => ({ ...s, isVar: false, varName: null, isBase: false }));
    const nonWsIdx = segs.map((s, i) => (s.isWhitespace ? -1 : i)).filter(i => i >= 0);
    if (!nonWsIdx.length) return segs;

    segs[nonWsIdx[0]].isBase = true;
    const usedNames = new Set();
    let prevTok = segs[nonWsIdx[0]].text;

    for (let pos = 1; pos < nonWsIdx.length; pos++) {
      const seg = segs[nonWsIdx[pos]];
      const tok = seg.text;
      const prevIsFlag = prevTok.startsWith('-') && prevTok !== '--';
      if (prevIsFlag) {
        seg.isVar = true;
        seg.varName = uniqueName(nameForFlag(prevTok), usedNames);
      }
      prevTok = tok;
    }
    return segs;
  }

  // Parse a command that's already been marked up with {{name}} placeholders
  // (imported from AI-generated JSON) — no guessing needed, just recognize
  // what's already there while still producing the same clickable-chip
  // segment model so mistakes are just as easy to fix by hand.
  function parseMarkedCommandToSegments(command) {
    const raw = tokenizeRaw(command);
    const out = [];
    let seenNonWs = false;
    for (const t of raw) {
      if (t.isWhitespace) { out.push({ ...t, isVar: false, varName: null, isBase: false }); continue; }
      const parts = t.text.split(/(\{\{\w+\}\})/).filter(p => p !== '');
      parts.forEach((p, i) => {
        const m = p.match(/^\{\{(\w+)\}\}$/);
        out.push({
          text: p,
          isWhitespace: false,
          isVar: !!m,
          varName: m ? m[1] : null,
          isBase: !seenNonWs && i === 0 && !m,
        });
      });
      seenNonWs = true;
    }
    return out;
  }

  function segmentsToCommand(segments) {
    return segments.map(s => (s.isVar ? `{{${s.varName}}}` : s.text)).join('');
  }

  return {
    escapeHtml, escapeAttr,
    CATEGORY_HINTS, CATEGORY_KEYWORDS, suggestCategoryLabel,
    hashString,
    FLAG_NAME_OVERRIDES, sanitizeName, nameForFlag, genericNameForToken, uniqueName,
    tokenizeRaw, guessCandidateSegments, parseMarkedCommandToSegments, segmentsToCommand,
  };
});
