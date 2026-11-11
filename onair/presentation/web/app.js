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
    if (snap.mode === 'work' && snap.work) {
      renderWork(snap.work);
      return;
    }
    show($('work'), false);
    if (snap.mode === 'privacy') {
      $('work-panes').replaceChildren(); $('work-title').textContent = ''; $('work-status').textContent = '';
      $('work-steps').replaceChildren(); previous = [];
      stage.dataset.mode = 'privacy';
      show($('slide'), false); show($('standby'), false);
      return;
    }
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

  var previous = [];
  function renderWork(work) {
    stage.dataset.mode = 'work';
    show($('slide'), false); show($('standby'), false); show($('work'), true);
    $('work-title').textContent = work.title;
    $('work-status').textContent = work.status || '';
    show($('work-status'), !!work.status);
    var rail = $('work-steps');
    rail.style.setProperty('--step-size', '40px');
    while (rail.firstChild) rail.removeChild(rail.firstChild);
    show(rail, !!work.steps);
    if (work.steps) {
      var head = document.createElement('div'); head.className = 'steps-heading'; head.textContent = 'PROGRESS'; rail.appendChild(head);
      [['done', work.steps.done], ['current', [work.steps.current]], ['next', work.steps.next]].forEach(function (group) {
        group[1].forEach(function (text) {
          var item = document.createElement('div'); item.className = 'step step-' + group[0];
          var icon = document.createElement('span'); icon.textContent = group[0] === 'done' ? '\u2713' : group[0] === 'current' ? '\u25cf' : '\u25cb';
          var label = document.createElement('span'); label.textContent = text;
          item.appendChild(icon); item.appendChild(label);
          rail.appendChild(item);
        });
      });
      // Preserve all six bounded labels instead of clipping long progress lists.
      var stepSize = 40;
      while (rail.scrollHeight > rail.clientHeight + 1 && stepSize > 12) {
        stepSize -= 2;
        rail.style.setProperty('--step-size', stepSize + 'px');
      }
    }
    var panes = $('work-panes');
    while (panes.firstChild) panes.removeChild(panes.firstChild);
    work.panes.forEach(function (pane, index) {
      var box = document.createElement('article'); box.className = 'work-pane';
      box.dataset.kind = pane.kind;
      var label = document.createElement('div'); label.className = 'pane-label';
      label.textContent = pane.label + (pane.language ? '  /  ' + pane.language : '');
      box.appendChild(label);
      var content = document.createElement('div'); content.className = 'pane-content';
      var lines = pane.content.split('\n');
      lines.forEach(function (text, i) {
        var line = document.createElement('div'); line.className = 'work-line';
        line.textContent = text || '\u00a0';
        if (pane.kind === 'diff' && text[0] === '+') line.classList.add('added');
        if (pane.kind === 'diff' && text[0] === '-') line.classList.add('removed');
        if (previous[index] && previous[index][i] !== text) line.classList.add('changed');
        content.appendChild(line);
      });
      box.appendChild(content);
      var more = document.createElement('div'); more.className = 'pane-more'; box.appendChild(more);
      panes.appendChild(box);
      previous[index] = lines;
    });
    // Measure only after all panes are present, so flex widths are final.
    Array.from(panes.children).forEach(function (box) {
      var content = box.querySelector('.pane-content');
      var more = box.querySelector('.pane-more');
      if (box.dataset.kind === 'terminal') {
        content.scrollTop = content.scrollHeight;
        var top = content.getBoundingClientRect().top;
        var first = Array.from(content.children).find(function (line) { return line.getBoundingClientRect().bottom > top; });
        if (first && first.getBoundingClientRect().top < top) content.scrollTop -= (top - first.getBoundingClientRect().top) / (parseFloat(getComputedStyle(stage).getPropertyValue('--window')) || 1);
      } else {
        var hidden = Array.from(content.children).filter(function (line) {
          return line.offsetTop + line.offsetHeight > content.offsetTop + content.clientHeight + 1;
        }).length;
        if (hidden) { more.textContent = hidden + ' more lines'; box.classList.add('has-more'); }
      }
    });
    previous.length = work.panes.length;
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
    show($('work'), false);
    $('work-panes').replaceChildren(); $('work-title').textContent = ''; $('work-status').textContent = '';
    $('work-steps').replaceChildren(); previous = [];
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
