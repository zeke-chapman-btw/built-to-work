/* Native disclosures remain usable without JavaScript. No menu role or custom tab model. */
(function () {
  "use strict";
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var open = document.querySelectorAll("details[data-ui-menu][open]");
    open.forEach(function (menu) {
      if (menu.contains(event.target)) {
        menu.open = false;
        var summary = menu.querySelector("summary");
        if (summary) summary.focus();
        event.preventDefault();
      }
    });
  });
  document.addEventListener("pointerdown", function (event) {
    document.querySelectorAll("details[data-ui-menu][open]").forEach(function (menu) {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
})();
