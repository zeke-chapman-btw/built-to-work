(function () {
  "use strict";
  const root = document.querySelector("[data-control-status]");
  if (!root) return;
  const set = (key, value) => { const node = document.querySelector('[data-live="' + key + '"]'); if (node) node.textContent = value; };
  async function refresh() {
    try {
      const response = await fetch(root.dataset.controlStatus, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json(), values = data.fresh ? data.server : {};
      set("application", (values.application || "Unknown") + " / " + (values.database || "Unknown"));
      set("central", values.central || "Unknown / stale");
      set("storage", values.free_bytes == null ? "Unknown" : (values.free_bytes / 1073741824).toFixed(1) + " GB free");
      set("storage-state", values.storage || "Unknown / stale");
      set("worker", data.worker);
      set("communication", data.devices.filter(x => x.communication === "Recent").length + " recent · " + data.devices.filter(x => x.communication === "Stale").length + " stale");
      set("unknown", data.devices.filter(x => x.communication === "Unknown").length);
      set("sample", data.sampled_at ? "Sample: " + new Date(data.sampled_at).toLocaleTimeString() + " · worker " + data.worker : "No worker sample yet");
      data.devices.forEach(device => {
        const row = Array.from(document.querySelectorAll("[data-device]")).find(x => x.dataset.device === device.id);
        if (!row) return;
        const badge = row.querySelector("[data-device-status]");
        if (badge) { badge.textContent = device.communication; badge.className = "tc-pill " + device.communication.toLowerCase(); }
        const seen = row.querySelector("[data-device-seen]"); if (seen) seen.textContent = device.last_seen ? new Date(device.last_seen).toLocaleTimeString() : "Never reported";
        const application = row.querySelector("[data-device-application]");
        if (application) application.textContent = (device.application || "Unknown") + (device.communication === "Stale" ? " (last report)" : "");
        const playback = row.querySelector("[data-device-playback]");
        if (playback) playback.textContent = device.communication === "Recent" && device.playback_advancing ? "advancing" : "unconfirmed";
        const activity = row.querySelector("[data-device-activity]");
        if (activity) activity.textContent = device.last_capture_activity ? new Date(device.last_capture_activity).toLocaleString() : "None recorded";
      });
      set("refresh", "Status refreshed locally");
    } catch (_) {
      set("application", "Status unavailable"); set("central", "Unknown / stale");
      set("worker", "Unknown / stale"); set("storage", "Unknown / stale"); set("storage-state", "Status unavailable");
      set("communication", "Unknown / stale"); set("unknown", "Unknown");
      document.querySelectorAll("[data-device]").forEach(row => {
        const badge = row.querySelector("[data-device-status]");
        if (badge) { badge.textContent = "Unknown / stale"; badge.className = "tc-pill stale"; }
        const application = row.querySelector("[data-device-application]");
        if (application) application.textContent = "Previous report; refresh unavailable";
        const playback = row.querySelector("[data-device-playback]"); if (playback) playback.textContent = "unconfirmed";
      });
      set("refresh", "Status refresh unavailable. Previous device reports may be stale.");
    }
    setTimeout(refresh, 15000);
  }
  refresh();
})();
