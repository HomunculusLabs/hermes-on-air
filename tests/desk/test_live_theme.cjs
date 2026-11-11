const assert=require('node:assert/strict'),fs=require('node:fs');
const css=fs.readFileSync('dashboard/dist/live.css','utf8');
assert.match(css,/\.onair-live[^}]*font-family\s*:\s*var\(--theme-font-sans/,'Live must use the dashboard UI font token, not the design-system display face');
assert.match(css,/--color-background\s*,\s*var\(--background/,'Live background must follow the dashboard shell theme');
assert.doesNotMatch(css,/(?:^|[},])\s*\.onair-live\s*\{/m,'Live container rules must not style the Details LIVE badge');
const fixture=fs.readFileSync('tests/desk/live_screenshots.cjs','utf8');
assert.match(fixture,/web\/src\/index\.css/,'fixture must load the host dashboard theme variables');
console.log('theme font contract passed');
