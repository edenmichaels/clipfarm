// Injected into ClipFarm's in-app campaign browser (contentrewards.com / whop.com only).
//
// Adds a small toolbar with "Import this page". Pressing it reads ONLY the page on screen
// (its URL, title, visible text and campaign links) and hands it to ClipFarm by navigating to
// https://clipfarm.import/..., which the app intercepts and cancels -- so the site never gets any
// access to ClipFarm, and nothing is sent anywhere else.
//
// This script never clicks, submits or fills anything on the page, and never runs on its own:
// no timers, no observers, no load hooks. Joining and submitting is always done by you.
(function () {
  "use strict";
  if (window.top !== window.self) return;
  var host = location.hostname.replace(/^www\./, "");
  if (!/(^|\.)contentrewards\.com$|(^|\.)whop\.com$/.test(host)) return;
  if (window.__clipfarmToolbar) return;
  window.__clipfarmToolbar = true;

  var CHUNK = 200000;

  function collect() {
    var links = [];
    var seen = {};
    var anchors = document.querySelectorAll('a[href*="/c/campaigns/"]');
    for (var i = 0; i < anchors.length && links.length < 200; i++) {
      var a = anchors[i];
      if (seen[a.href]) continue;
      seen[a.href] = 1;
      // The campaign "card" around the link: climb while the box still holds only this one campaign.
      var card = a;
      for (var up = 0; up < 6 && card.parentElement; up++) {
        var p = card.parentElement;
        if (p.querySelectorAll('a[href*="/c/campaigns/"]').length > 1) break;
        card = p;
      }
      links.push({ href: a.href, text: (card.innerText || "").slice(0, 4000) });
    }
    return {
      url: location.href,
      title: document.title,
      text: (document.body ? document.body.innerText : "").slice(0, 400000),
      links: links,
    };
  }

  function toBase64(str) {
    var bytes = new TextEncoder().encode(str);
    var bin = "";
    for (var i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function pause(ms) {
    return new Promise(function (r) { window.setTimeout(r, ms); });
  }

  async function sendPage(button) {
    button.disabled = true;
    setStatus("Reading this page…");
    var data = toBase64(JSON.stringify(collect()));
    var id = Math.random().toString(36).slice(2, 10);
    var n = Math.max(1, Math.ceil(data.length / CHUNK));
    for (var i = 0; i < n; i++) {
      location.assign("https://clipfarm.import/chunk?id=" + id + "&i=" + i + "&n=" + n +
        "&d=" + data.slice(i * CHUNK, (i + 1) * CHUNK));
      await pause(40);
    }
    setStatus("Sent to ClipFarm…");
    button.disabled = false;
  }

  var root, statusEl;
  function setStatus(msg) { if (statusEl) statusEl.textContent = msg; }
  // ClipFarm writes the import summary back here ("Imported 6 campaigns: 2 new, 3 updated, 1 ended.").
  window.__clipfarmStatus = setStatus;

  function build() {
    if (root || !document.body) return;
    var host = document.createElement("div");
    host.style.cssText = "position:fixed;z-index:2147483647;left:50%;bottom:16px;transform:translateX(-50%);";
    root = host.attachShadow({ mode: "closed" });
    root.innerHTML =
      '<style>' +
      '.bar{display:flex;align-items:center;gap:12px;padding:8px 10px 8px 14px;border-radius:14px;' +
      'background:#111827;color:#f9fafb;font:500 13px/1.2 "Segoe UI",system-ui,sans-serif;' +
      'box-shadow:0 8px 28px rgba(0,0,0,.35)}' +
      '.brand{font-weight:700;letter-spacing:.2px}' +
      '.status{opacity:.75;max-width:340px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      'button{border:0;border-radius:10px;padding:9px 14px;font:600 13px "Segoe UI",system-ui,sans-serif;' +
      'background:#22c55e;color:#052e16;cursor:pointer}button:disabled{opacity:.6;cursor:wait}' +
      '</style>' +
      '<div class="bar"><span class="brand">ClipFarm</span>' +
      '<span class="status">Open a campaign (or the Discover list), then press Import.</span>' +
      '<button type="button">Import this page</button></div>';
    statusEl = root.querySelector(".status");
    var btn = root.querySelector("button");
    btn.addEventListener("click", function () { sendPage(btn); });
    document.documentElement.appendChild(host);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", build);
  } else {
    build();
  }
})();
