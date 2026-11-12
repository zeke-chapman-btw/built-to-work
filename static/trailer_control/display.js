/* Local display only. No third-party assets, private record storage or raw HTML rendering. */
(function () {
  "use strict";
  const root = document.querySelector("[data-tv]");
  if (!root) return;
  const status = document.getElementById("display-status");
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  const boot = crypto.randomUUID(); let sequence = 0, failures = 0, groups = [], index = 0, last = null;
  const video = root.querySelector("video");
  function say(text, stale) { status.textContent = text; status.classList.toggle("stale", !!stale); }
  function text(tag, value) { const e = document.createElement(tag); e.textContent = value; return e; }
  function draw() {
    const host = document.getElementById("board"); if (!host) return;
    host.replaceChildren();
    if (!groups.length) { const e = text("div", "Scores will show up here soon."); e.className = "empty"; host.append(e); return; }
    const group = groups[index % groups.length];
    document.getElementById("board-group").textContent = "Top scores · " + group.simulator;
    const table = document.createElement("table"), head = document.createElement("thead"), header = document.createElement("tr");
    ["Rank", "Player", "Score"].forEach(v => { const th = text("th", v); th.scope = "col"; header.append(th); });
    head.append(header); table.append(head); const body = document.createElement("tbody");
    group.rows.forEach(row => { const tr = document.createElement("tr"); [row.rank, row.alias, Number(row.score).toFixed(2) + "%"].forEach(v => tr.append(text("td", v))); body.append(tr); });
    table.append(body); host.append(table);
  }
  async function poll() {
    try {
      const response = await fetch(root.dataset.dataUrl, { cache: "no-store", credentials: "same-origin" });
      if (!response.ok) { if (response.status === 403 || response.status === 404) { groups = []; draw(); } throw new Error("unavailable"); }
      const data = await response.json(); groups = data.groups; last = new Date(); failures = 0; draw();
      say("Local update " + last.toLocaleTimeString() + " · accepted official scores", false);
    } catch (_) { failures += 1; if (last && Date.now() - last.getTime() > 300000) { groups = []; draw(); } say(last ? "Connection interrupted · last update " + last.toLocaleTimeString() : "Display data unavailable · reconnecting", true); }
    setTimeout(poll, Math.min(30000, 5000 * Math.pow(2, Math.min(failures, 3))));
  }
  async function heartbeat() {
    const state = video ? (video.error ? "error" : video.paused ? "paused" : "playing") : root.dataset.tv === "video" ? "idle" : "operating";
    const payload = { boot_id: boot, sequence: ++sequence, event_id: root.dataset.event, state: state };
    if (video) payload.position = video.currentTime;
    try { await fetch(root.dataset.heartbeatUrl, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(payload) }); } catch (_) { /* No readiness claim on transport loss. */ }
    setTimeout(heartbeat, 15000);
  }
  document.querySelector("[data-fullscreen]")?.addEventListener("click", () => root.requestFullscreen?.().catch(() => say("Use the display browser's fullscreen controls.", true)));
  document.querySelector("[data-play]")?.addEventListener("click", () => { if (video?.error) video.load(); video?.play().catch(() => say("Playback blocked or unavailable. Use Play after checking the local asset.", true)); });
  if (video) { video.addEventListener("error", () => say("Local video unavailable. Check the approved asset, then use Play / Retry.", true)); video.addEventListener("playing", () => say("Local playback started · display visibility not verified", false)); video.play().catch(() => say("Select Play to start local video.", true)); }
  if (root.dataset.tv === "leaderboard") { poll(); setInterval(() => { index += 1; draw(); }, 15000); }
  heartbeat();
})();
