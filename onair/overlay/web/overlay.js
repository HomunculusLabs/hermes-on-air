(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var caption = $("caption"), captionText = $("caption-text"), speaker = $("speaker");
  var chat = $("chat"), cover = $("cover"), liveLabel = $("live-label"), alertBox = $("alert");
  var unlock = $("audio-unlock");
  var params = new URLSearchParams(location.search);
  var layout = params.get("layout"), theme = params.get("theme"), scale = Number(params.get("scale"));
  document.body.dataset.status = params.get("status") === "1" ? "on" : "off";
  document.body.dataset.layout = ["full", "captions", "chat", "alerts"].includes(layout) ? layout : "full";
  document.body.dataset.theme = ["dark", "light"].includes(theme) ? theme : "dark";
  if (params.has("scale") && Number.isFinite(scale) && scale > 0) {
    document.documentElement.style.setProperty("--scale", String(Math.max(.5, Math.min(2, scale))));
  }

  var state = { live: false, mode: "off_air", privacy: true, muted: true };
  var captionTimer = null, pageTimer = null, captionPages = [], pageIndex = 0, currentCaption = null, audio = null, pendingAudio = null;
  var captionExpired = false, pagesComplete = false;
  var alertTimer = null, alertQueue = [], source = null, retryTimer = null, retryDelay = 500;
  var lastSeq = 0, receivedState = false;
  var loadedBuild = document.documentElement.dataset.build, reloadAttempted = false;
  function changedBuild(token) {
    if (!loadedBuild || typeof token !== "string" || !token || token === loadedBuild) return false;
    if (!reloadAttempted) {
      try {
        var storage = window.sessionStorage, key = "onair.overlay.reload", last = storage.getItem(key), now = Date.now();
        if (last === null || (Number.isFinite(Number(last)) && now - Number(last) >= 30000)) {
          storage.setItem(key, String(now)); reloadAttempted = true; location.reload();
        }
      } catch (err) { /* No durable guard: keep the privacy cover up. */ }
    }
    return true;
  }

  function stopAudio() {
    pendingAudio = null;
    unlock.hidden = true;
    if (audio) {
      var old = audio;
      audio = null;
      old.pause();
      old.removeAttribute("src");
      old.load();
    }
  }
  function clearCaption() {
    clearTimeout(captionTimer); captionTimer = null; clearTimeout(pageTimer); pageTimer = null; captionPages = []; pageIndex = 0;
    captionExpired = false; pagesComplete = false;
    if (audio && audio._captionId === currentCaption) stopAudio();
    caption.classList.remove("fading");
    caption.hidden = true; captionText.textContent = ""; speaker.textContent = ""; currentCaption = null;
  }
  function fadeCaption() {
    clearTimeout(captionTimer);
    caption.classList.add("fading");
    captionTimer = setTimeout(clearCaption, 400);
  }
  function fitCaption() {
    if (currentCaption === null) return;
    caption.hidden = false;
    captionText.style.fontSize = "";
    captionText.style.webkitLineClamp = "unset";
    if (!captionText.offsetWidth) { captionText.style.webkitLineClamp = ""; return; }
    var size = parseFloat(getComputedStyle(captionText).fontSize);
    var floor = 36 * (Number.isFinite(scale) && scale > 0 ? Math.max(.5, Math.min(2, scale)) : 1);
    while (size > floor && captionText.scrollHeight > parseFloat(getComputedStyle(captionText).lineHeight) * 2 + 6) {
      size = Math.max(size * .85, floor); captionText.style.fontSize = size + "px";
    }
    captionText.style.webkitLineClamp = "";
  }
  function preparePages(text) {
    captionText.textContent = text; fitCaption();
    captionText.style.webkitLineClamp = "unset";
    var words = text.trim().split(/\s+/), pages = [], part = "";
    if (!text.trim() || !captionText.offsetWidth) { captionText.style.webkitLineClamp = ""; return [text]; }
    words.forEach(function (word) {
      var next = part ? part + " " + word : word;
      captionText.textContent = next;
      if (part && captionText.scrollHeight > parseFloat(getComputedStyle(captionText).lineHeight) * 2 + 6) {
        pages.push(part); part = word;
      } else part = next;
    });
    if (part) pages.push(part);
    captionText.style.webkitLineClamp = "";
    return pages;
  }
  function showPage(index) {
    pageIndex = Math.min(index, captionPages.length - 1);
    captionText.textContent = captionPages[pageIndex] || "";
  }
  function scheduleCaptionPage() {
    clearTimeout(pageTimer);
    if (!captionPages.length || pagesComplete) return;
    pageTimer = setTimeout(function () {
      pageTimer = null;
      if (pageIndex < captionPages.length - 1) { showPage(pageIndex + 1); scheduleCaptionPage(); }
      else { pagesComplete = true; maybeExpireCaption(); }
    },
      Math.max(1800, captionPages[pageIndex].length / 14 * 1000));
  }
  function maybeExpireCaption() {
    if (captionExpired && pagesComplete && (!audio || audio.paused || audio._captionId !== currentCaption)) fadeCaption();
  }
  function syncAudioPages(clip) {
    if (audio !== clip || clip.paused || !Number.isFinite(clip.duration) || clip.duration <= 0 || captionPages.length < 2) return;
    clearTimeout(pageTimer); pageTimer = null;
    var total = captionPages.reduce(function (sum, page) { return sum + page.length; }, 0);
    var target = clip.currentTime / clip.duration * total, cumulative = 0, index = 0;
    while (index < captionPages.length - 1 && cumulative + captionPages[index].length <= target) cumulative += captionPages[index++].length;
    showPage(index);
  }
  function fitChat() {
    if (!chat.clientHeight) return; // A layout may intentionally hide chat.
    while (chat.children.length && chat.scrollHeight > chat.clientHeight + 2) {
      chat.firstChild.remove();
    }
  }
  function clearAlerts() {
    clearTimeout(alertTimer); alertTimer = null; alertQueue = [];
    alertBox.hidden = true; alertBox.textContent = ""; alertBox.classList.remove("exiting");
  }
  function clearSurfaces() { stopAudio(); clearCaption(); clearAlerts(); chat.textContent = ""; }
  function cut() {
    receivedState = false;
    state = { live: false, mode: "off_air", privacy: true, muted: true };
    cover.hidden = false; clearSurfaces();
    document.body.classList.remove("live"); liveLabel.textContent = "OFF";
  }
  function validState(ev) {
    return typeof ev.live === "boolean" && typeof ev.privacy === "boolean" &&
      typeof ev.muted === "boolean" && ["off_air", "paused", "approval", "autonomous"].includes(ev.mode) &&
      ev.live === ["approval", "autonomous"].includes(ev.mode);
  }
  function applyState(ev) {
    state = ev;
    // In this single event handler, cover and stop playback before the next paint.
    if (ev.privacy) { cover.hidden = false; clearSurfaces(); }
    else { cover.hidden = true; }
    if (!ev.live) clearSurfaces();
    if (ev.muted) { stopAudio(); if (captionPages.length) scheduleCaptionPage(); maybeExpireCaption(); }
    document.body.classList.toggle("live", !!ev.live);
    liveLabel.textContent = ev.live ? (ev.mode === "approval" ? "APPROVAL" : "ON") : "OFF";
  }
  function showCaption(ev) {
    if (state.privacy || !state.live) return;
    clearCaption(); currentCaption = ev.id;
    speaker.textContent = ev.speaker || "";
    captionPages = preparePages(ev.text || ""); showPage(0);
    pagesComplete = captionPages.length <= 1;
    scheduleCaptionPage();
    captionTimer = setTimeout(function () {
      captionTimer = null;
      captionExpired = true; maybeExpireCaption();
    }, duration(ev.ttl_ms, 4000));
  }
  function duration(value, fallback) {
    return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(value, 60000)) : fallback;
  }
  function addChat(ev) {
    if (state.privacy || !state.live) return;
    var li = document.createElement("li"), b = document.createElement("b"), span = document.createElement("span");
    b.textContent = (ev.name || "") + ":"; span.textContent = ev.text || "";
    li.appendChild(b); li.appendChild(span); chat.appendChild(li);
    while (chat.children.length > 5) chat.removeChild(chat.firstChild);
    fitChat();
    setTimeout(function () { if (li.parentNode) li.parentNode.removeChild(li); }, 12000);
  }
  var ALERT_TEXT = {
    sub: function (a) { return "subscribed" + (a.months > 1 ? " · " + a.months + " months" : ""); },
    resub: function (a) { return "resubscribed" + (a.months > 1 ? " · " + a.months + " months" : ""); },
    subgift: function () { return "gifted a sub"; },
    giftbomb: function (a) { return "gifted " + a.amount + " subs"; },
    bits: function (a) { return "cheered " + a.amount + " bits"; },
    raid: function (a) { return "is raiding with " + a.amount + " viewers"; }
  };
  function fitAlert() {
    if (alertBox.hidden) return;
    alertBox.classList.remove("long-name");
    var name = alertBox.querySelector(".alert-name");
    if (name && name.scrollWidth > name.clientWidth) alertBox.classList.add("long-name");
  }
  function nextAlert() {
    alertTimer = null;
    if (state.privacy || !state.live || !alertQueue.length) {
      alertBox.hidden = true; alertBox.textContent = ""; return;
    }
    var ev = alertQueue.shift(), format = ALERT_TEXT[ev.kind];
    var name = document.createElement("span"), event = document.createElement("span");
    name.className = "alert-name"; event.className = "alert-event";
    name.textContent = ev.name || "Someone"; event.textContent = format ? format(ev) : "supported the show";
    alertBox.textContent = ""; alertBox.classList.remove("exiting");
    alertBox.appendChild(name); alertBox.appendChild(event); alertBox.hidden = false;
    fitAlert();
    alertTimer = setTimeout(function () {
      alertBox.classList.add("exiting");
      alertTimer = setTimeout(nextAlert, 250);
    }, duration(ev.ttl_ms, 8000));
  }
  function showAlert(ev) {
    if (state.privacy || !state.live) return;
    if (alertQueue.length >= 50) return;
    alertQueue.push(ev);
    if (alertTimer === null) nextAlert();
  }
  function playAudio(ev) {
    if (state.privacy || state.muted || !state.live || ev.caption_id !== currentCaption ||
        typeof ev.url !== "string" || !/^\/audio\/[A-Za-z0-9_-]{1,64}\.wav$/.test(ev.url)) return;
    stopAudio();
    var clip = new Audio(ev.url); audio = clip; clip._captionId = ev.caption_id;
    clip.addEventListener("loadedmetadata", function () { syncAudioPages(clip); });
    clip.addEventListener("timeupdate", function () { syncAudioPages(clip); });
    clip.addEventListener("playing", function () { syncAudioPages(clip); });
    clip.addEventListener("ended", function () {
      if (audio !== clip) return;
      audio = null; pendingAudio = null; unlock.hidden = true;
      if (currentCaption === ev.caption_id) fadeCaption();
    });
    clip.addEventListener("error", function () {
      if (audio === clip) { stopAudio(); if (!captionTimer) clearCaption(); }
    });
    tryPlay(clip);
  }
  function tryPlay(clip) {
    var result;
    try { result = clip.play(); } catch (err) { result = Promise.reject(err); }
    if (result && typeof result.catch === "function") result.catch(function () {
      if (audio === clip && !state.privacy && !state.muted && state.live) {
        pendingAudio = clip; unlock.hidden = false;
      }
    });
  }
  unlock.addEventListener("click", function () {
    if (!pendingAudio || state.privacy || state.muted || !state.live) return;
    var clip = pendingAudio; pendingAudio = null; unlock.hidden = true; tryPlay(clip);
  });
  function receive(message) {
    var ev;
    try { ev = JSON.parse(message.data); } catch (err) { cut(); return; }
    if (!ev || typeof ev !== "object") { cut(); return; }
    if (ev.type === "state" && (!Number.isSafeInteger(ev.seq) || ev.seq <= 0 || !validState(ev))) {
      if (Number.isSafeInteger(ev.seq) && ev.seq > lastSeq) lastSeq = ev.seq;
      cut(); return;
    }
    if (ev.type === "state" && changedBuild(ev.build)) { cut(); return; }
    if (!Number.isSafeInteger(ev.seq) || ev.seq <= lastSeq) return;
    if (!receivedState && ev.type !== "state") return;
    lastSeq = ev.seq;
    switch (ev.type) {
      case "state": receivedState = true; applyState(ev); break;
      case "caption": showCaption(ev); break;
      case "caption_clear": stopAudio(); clearCaption(); break;
      case "chat": addChat(ev); break;
      case "audio": playAudio(ev); break;
      case "alert": showAlert(ev); break;
    }
  }
  function connect() {
    receivedState = false; lastSeq = 0;
    var connection = new EventSource("/events"); source = connection;
    connection.onopen = function () { if (source === connection) retryDelay = 500; };
    connection.onmessage = function (message) { if (source === connection) receive(message); };
    connection.onerror = function () {
      if (source !== connection) return;
      connection.close(); source = null;
      // A disconnected client cannot know if privacy changed; fail closed until a fresh snapshot.
      cut();
      retryTimer = setTimeout(connect, retryDelay); retryDelay = Math.min(retryDelay * 2, 30000);
    };
  }
  window.addEventListener("resize", function () { fitCaption(); fitChat(); fitAlert(); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { fitCaption(); fitChat(); });
  window.addEventListener("pagehide", function () { clearTimeout(retryTimer); if (source) source.close(); clearSurfaces(); });
  connect();
})();
