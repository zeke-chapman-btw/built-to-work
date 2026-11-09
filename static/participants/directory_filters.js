/* One GET form and one set of controls, with a native phone disclosure. */
(function () {
  "use strict";
  function initialize() {
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
