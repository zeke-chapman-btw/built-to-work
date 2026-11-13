/* One GET form and one set of controls, with a native phone disclosure. */
(function () {
  "use strict";
  function initialize() {
    document.querySelectorAll("[data-more-filters-toggle]").forEach(function (button) {
      const panel = document.getElementById(button.getAttribute("aria-controls"));
      if (!panel) return;
      button.addEventListener("click", function () {
        const open = button.getAttribute("aria-expanded") !== "true";
        button.setAttribute("aria-expanded", open ? "true" : "false");
        panel.hidden = !open;
        if (open) { const first = panel.querySelector("select, input:not([type=hidden])"); if (first) first.focus(); }
      });
    });
    document.querySelectorAll("[data-participant-filter-disclosure]").forEach(function (details) {
      const media = window.matchMedia("(max-width: 639px)");
      const summary = details.querySelector("summary");
      const hasErrors = details.dataset.filterErrors === "true";
      function update() {
        const editingInside = details.contains(document.activeElement) && document.activeElement !== summary;
        details.open = !media.matches || hasErrors || editingInside;
      }
      update();
      if (media.addEventListener) media.addEventListener("change", update);
      else media.addListener(update);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
})();
