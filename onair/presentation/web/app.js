// On Air presentation page. Read-only: it renders the public program snapshot as TEXT (textContent only),
// never HTML, and has no audio. On disconnect it shows the standby card instead of stale content.
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var stage = $('stage');
  var lastRevision = -1;

  function show(el, on) { el.hidden = !on; }

  function render(snap) {
    if (!snap || snap.type !== 'program') return;
    lastRevision = snap.revision;
    stage.dataset.revision = String(snap.revision);
    $('deck-title').textContent = snap.deck_title || '';
    if (snap.mode === 'slides' && snap.slide) {
      var s = snap.slide;
      stage.dataset.mode = 'slides';
      stage.dataset.kind = s.kind;
      $('heading').textContent = s.heading || '';
      show($('heading'), !!s.heading);
      $('subtitle').textContent = s.subtitle || '';
      show($('subtitle'), s.kind === 'title' && !!s.subtitle);
      var ul = $('bullets');
      while (ul.firstChild) ul.removeChild(ul.firstChild);
      (s.kind === 'bullets' ? s.bullets || [] : []).forEach(function (b) {
        var li = document.createElement('li');
        li.textContent = b;
        ul.appendChild(li);
      });
      show(ul, s.kind === 'bullets');
      $('body').textContent = s.body || '';
      show($('body'), s.kind === 'text');
      $('code').textContent = s.code || '';
      $('language').textContent = s.language || '';
      show($('code-wrap'), s.kind === 'code');
      $('counter').textContent = (snap.index + 1) + ' / ' + snap.count;
      show($('slide'), true);
      show($('standby'), false);
      fit();
    } else {
      standby((snap.standby && snap.standby.title) || 'We\u2019ll be right back', (snap.standby && snap.standby.note) || '', 'standby');
    }
  }

  // Shrink the slide's text in small steps until it fits its area (the bottom band is kept clear for captions).
  // A code block counts as fitting only when none of its lines are cut.
  var MIN_FIT = 0.45;
  function overflows() {
    var slide = $('slide');
    var wrap = $('code-wrap');
    return slide.scrollHeight > slide.clientHeight + 1 || (!wrap.hidden && wrap.scrollHeight > wrap.clientHeight + 1);
  }
  function fit() {
    var scale = 1;
    stage.style.setProperty('--fit', '1');
    while (scale > MIN_FIT + 0.001 && overflows()) {
      scale -= 0.05;
      stage.style.setProperty('--fit', scale.toFixed(2));
    }
    stage.dataset.fit = overflows() ? 'cut' : 'whole';
  }

  function standby(title, note, mode) {
    stage.dataset.mode = mode;
    $('standby-title').textContent = title;
    $('standby-note').textContent = note;
    $('counter').textContent = '';
    show($('slide'), false);
    show($('standby'), true);
  }

  function connect() {
    var es = new EventSource('/present/events');
    es.onmessage = function (e) {
      try { render(JSON.parse(e.data)); } catch (err) { /* ignore malformed frames */ }
    };
    es.onerror = function () {
      // Never leave stale content up while disconnected; EventSource retries by itself.
      standby('We\u2019ll be right back', '', 'disconnected');
    };
  }
  // Scale the 1920x1080 stage to fit the window that shows it, so a plain browser window never crops a slide.
  function fitWindow() {
    var k = Math.min(window.innerWidth / 1920, window.innerHeight / 1080);
    stage.style.setProperty('--window', (k > 0 ? k : 1).toFixed(4));
  }
  window.addEventListener('resize', fitWindow);
  fitWindow();
  window.__onairPresentation = { revision: function () { return lastRevision; } };
  connect();
})();
