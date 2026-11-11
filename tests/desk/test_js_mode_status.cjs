// Check the real plainStatus function across all desk modes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../../dashboard/dist/index.js'), 'utf8');
const start = source.indexOf('  function plainStatus(');
const end = source.indexOf('\n  /* Only a plain loopback', start);
assert(start >= 0 && end > start);
const plainStatus = vm.runInNewContext('(' + source.slice(start, end).trim() + ')');
for (const practice of [false, true]) {
  const status = mode => plainStatus({ mode, privacy: false, muted: false, voice: false }, true, practice, false, false, false);
  const overlay = x => x.facts.find(f => f.label === 'Overlay').value;
  assert.match(overlay(status('approval')), /approve/i);
  assert.match(overlay(status('autonomous')), /without approval/i);
  assert.doesNotMatch(status('autonomous').next, /queue/i);
  assert.match(overlay(status('paused')), /paused/i);
  assert.match(overlay(status('off_air')), /nothing/i);
}
const helpers = source.slice(source.indexOf('  function privacyLine('), source.indexOf('\n  /* Fixed help strings', source.indexOf('  function privacyLine(')));
const stateParts = vm.runInNewContext('(function() { const MODE_LABEL = {approval: "APPROVAL"}; ' + helpers + '; return stateParts; })()');
const armed = { mode: 'approval', live: true, privacy: false, muted: false, voice: false };
assert(stateParts(armed, true).includes('PRACTICE · NOT BROADCAST'));
assert(!stateParts(armed, true).includes('LIVE'));
assert(stateParts(armed, false).includes('LIVE'));
console.log('mode status passed');
