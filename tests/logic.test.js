// Unit tests for ui/logic.js — the pure, DOM-free logic behind the "+"
// quick-add guess engine, JSON-import parsing, category detection, and
// HTML escaping. Run with: node --test tests/
'use strict';
const { test, describe } = require('node:test');
const assert = require('node:assert/strict');
const logic = require('../ui/logic.js');

describe('escapeHtml / escapeAttr', () => {
  test('escapes the full unsafe character set', () => {
    const out = logic.escapeHtml(`<script>alert("x")</script>&'`);
    assert.equal(out, '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;&#39;');
  });

  test('a crafted category label cannot break out of an attribute value', () => {
    // This is the exact shape of payload that would have escaped a
    // value="..." attribute and injected a live tag before this was fixed.
    const payload = `"><script>alert(1)</script>`;
    const escaped = logic.escapeAttr(payload);
    assert.ok(!escaped.includes('"'));
    assert.ok(!escaped.includes('<'));
  });

  test('plain text passes through unchanged', () => {
    assert.equal(logic.escapeHtml('git status'), 'git status');
  });
});

describe('suggestCategoryLabel', () => {
  test('recognizes known base commands', () => {
    assert.equal(logic.suggestCategoryLabel('kubectl get pods'), 'Kubernetes');
    assert.equal(logic.suggestCategoryLabel('docker ps'), 'Docker');
    assert.equal(logic.suggestCategoryLabel('git status'), 'Git');
  });

  test('falls back to a keyword scan for shell builtins', () => {
    // The exact case that motivated the fallback: "source" isn't a known
    // tool, but "venv" elsewhere in the command is a strong Python signal.
    assert.equal(logic.suggestCategoryLabel('source venv/bin/activate'), 'Python');
  });

  test('never falls back to a literal "General"', () => {
    const samples = ['totally-unknown-tool --flag', '', 'xyz123'];
    for (const cmd of samples) {
      assert.notEqual(logic.suggestCategoryLabel(cmd), 'General');
    }
  });

  test('unrecognizable command defaults to Bash / Linux', () => {
    assert.equal(logic.suggestCategoryLabel('totally-unknown-tool --flag'), 'Bash / Linux');
  });
});

describe('hashString', () => {
  test('is deterministic', () => {
    assert.equal(logic.hashString('Terraform'), logic.hashString('Terraform'));
  });

  test('differs for different strings (no trivial collisions on common inputs)', () => {
    const labels = ['Git', 'Docker', 'Kubernetes', 'Python', 'Terraform', 'AWS CLI'];
    const hashes = new Set(labels.map(l => logic.hashString(l)));
    assert.equal(hashes.size, labels.length);
  });

  test('always non-negative', () => {
    for (const s of ['a', 'Category With Spaces', '', '日本語']) {
      assert.ok(logic.hashString(s) >= 0);
    }
  });
});

describe('guessCandidateSegments (light "+ " guess)', () => {
  function reconstruct(cmd) {
    return logic.segmentsToCommand(logic.guessCandidateSegments(cmd));
  }

  test('marks the token right after a known flag as a variable', () => {
    assert.equal(reconstruct('kubectl get pods -n prod'), 'kubectl get pods -n {{namespace}}');
  });

  test('uses the flag name override table for readable variable names', () => {
    const segs = logic.guessCandidateSegments('docker build -t myimage .');
    const tagVar = segs.find(s => s.isVar);
    assert.equal(tagVar.varName, 'tag');
  });

  test('derives a variable name from a long flag when there is no override', () => {
    const segs = logic.guessCandidateSegments('aws s3 cp --bucket mybucket file.txt');
    const bucketVar = segs.find(s => s.isVar && s.text === 'mybucket');
    assert.equal(bucketVar.varName, 'bucket');
  });

  test('does not guess anything for a command with no flags (deliberately simple)', () => {
    // This is the documented, accepted trade-off: the light guesser only
    // looks at flag-value pairs, so a flagless command like this one is
    // left fully literal for the user to mark up by hand if they want to.
    assert.equal(reconstruct('source venv/bin/activate'), 'source venv/bin/activate');
  });

  test('the base command token is never marked as a variable', () => {
    const segs = logic.guessCandidateSegments('git checkout -b feature');
    assert.equal(segs[0].isVar, false);
    assert.equal(segs[0].isBase, true);
  });

  test('duplicate variable names within one command get disambiguated', () => {
    const segs = logic.guessCandidateSegments('cp -f a.txt -f b.txt');
    const varNames = segs.filter(s => s.isVar).map(s => s.varName);
    assert.equal(new Set(varNames).size, varNames.length);
  });

  test('a quoted multi-word flag value stays a single token, not three', () => {
    const segs = logic.guessCandidateSegments('curl -H "Authorization: Bearer x" https://api.example.com');
    const headerVar = segs.find(s => s.isVar);
    assert.equal(headerVar.text, '"Authorization: Bearer x"');
  });
});

describe('parseMarkedCommandToSegments (Import — no guessing)', () => {
  test('round-trips an already-marked command exactly', () => {
    const cmd = 'kubectl logs {{pod}} -n {{namespace}} --tail={{lines}}';
    const segs = logic.parseMarkedCommandToSegments(cmd);
    assert.equal(logic.segmentsToCommand(segs), cmd);
  });

  test('extracts variable names from {{}} tokens', () => {
    const segs = logic.parseMarkedCommandToSegments('source {{venv_name}}/bin/activate');
    const vars = segs.filter(s => s.isVar).map(s => s.varName);
    assert.deepEqual(vars, ['venv_name']);
  });

  test('a literal prefix glued to a variable in the same token is split correctly', () => {
    // "deployment/{{name}}" is one whitespace-delimited token but must
    // become a literal "deployment/" segment plus a variable segment.
    const segs = logic.parseMarkedCommandToSegments('kubectl rollout restart deployment/{{name}}');
    const literalPrefix = segs.find(s => s.text === 'deployment/');
    const variable = segs.find(s => s.isVar);
    assert.ok(literalPrefix);
    assert.equal(literalPrefix.isVar, false);
    assert.equal(variable.varName, 'name');
  });

  test('a command with no placeholders is entirely literal', () => {
    const segs = logic.parseMarkedCommandToSegments('git status');
    assert.ok(segs.every(s => !s.isVar));
  });
});

describe('segmentsToCommand', () => {
  test('a flagless command guesses nothing, so it round-trips unchanged', () => {
    // Guessing is intentionally lossy for commands with flags (that's the
    // whole point — see the guessCandidateSegments describe block above),
    // so this only holds when there's no flag context to trigger a guess.
    for (const cmd of ['git status', 'npm run build', '']) {
      assert.equal(logic.segmentsToCommand(logic.guessCandidateSegments(cmd)), cmd);
    }
  });

  test('reconstructs a marked-up command\'s variables back into the same {{}} form', () => {
    const cmd = 'kubectl logs {{pod}} -n {{namespace}}';
    assert.equal(logic.segmentsToCommand(logic.parseMarkedCommandToSegments(cmd)), cmd);
  });
});

describe('toggle-token naming helpers', () => {
  test('nameForFlag prefers the override table', () => {
    assert.equal(logic.nameForFlag('-n'), 'namespace');
  });

  test('nameForFlag falls back to a sanitized version of the flag itself', () => {
    assert.equal(logic.nameForFlag('--resource-group'), 'resource_group');
  });

  test('genericNameForToken recognizes shape hints', () => {
    assert.equal(logic.genericNameForToken('8080'), 'port');
    assert.equal(logic.genericNameForToken('config.yaml'), 'file');
    assert.equal(logic.genericNameForToken('a/b/c'), 'path');
    assert.equal(logic.genericNameForToken('plainword'), 'value');
  });

  test('uniqueName disambiguates repeated names', () => {
    const used = new Set(['value']);
    assert.equal(logic.uniqueName('value', used), 'value_2');
    assert.equal(logic.uniqueName('value', used), 'value_3');
  });
});
