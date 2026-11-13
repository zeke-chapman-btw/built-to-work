/* Participants table: each viewer picks which columns to show. The choice is kept in this browser. */
(function () {
  "use strict";
  var KEY = "btw.participants.hiddenColumns";
  var DEFAULT_HIDDEN = ["id"];
  var WIDTHS = { id: "60px", name: "minmax(170px,1.6fr)", interest: "minmax(120px,1.1fr)", seeking: "100px",
                 travel: "90px", certs: "minmax(140px,1.3fr)", last: "minmax(130px,1.2fr)", type: "100px" };
  var ORDER = ["id", "name", "interest", "seeking", "travel", "certs", "last", "type"];
  var wide = window.matchMedia("(min-width: 901px)");

  function load() {
    try { var v = JSON.parse(window.localStorage.getItem(KEY)); if (Array.isArray(v)) return v; } catch (e) {}
    return DEFAULT_HIDDEN.slice();
  }
  function save(hidden) { try { window.localStorage.setItem(KEY, JSON.stringify(hidden)); } catch (e) {} }

  function apply(table, hidden) {
    var present = ORDER.filter(function (c) { return table.querySelector('.pl-head [data-col="' + c + '"]'); });
    var shown = present.filter(function (c) { return c === "name" || hidden.indexOf(c) === -1; });
    table.querySelectorAll("[data-col]").forEach(function (cell) {
      cell.hidden = cell.dataset.col !== "name" && hidden.indexOf(cell.dataset.col) !== -1;
    });
    var template = wide.matches ? shown.map(function (c) { return WIDTHS[c]; }).join(" ") : "";
    table.querySelectorAll(".pl-head, .pl-row").forEach(function (row) {
      if (template) row.style.setProperty("grid-template-columns", template, "important");
      else row.style.removeProperty("grid-template-columns");
    });
  }

  function initialize() {
    var table = document.querySelector("[data-column-table]");
    var menu = document.querySelector("[data-column-menu]");
    if (!table || !menu) return;
    var hidden = load();
    var boxes = menu.querySelectorAll('input[type="checkbox"]');
    function sync() { boxes.forEach(function (b) { b.checked = hidden.indexOf(b.value) === -1; }); apply(table, hidden); }
    boxes.forEach(function (box) {
      box.addEventListener("change", function () {
        hidden = hidden.filter(function (c) { return c !== box.value; });
        if (!box.checked) hidden.push(box.value);
        save(hidden); sync();
      });
    });
    var reset = menu.querySelector("[data-column-reset]");
    if (reset) reset.addEventListener("click", function () { hidden = []; save(hidden); sync(); });
    document.addEventListener("click", function (e) { if (menu.open && !menu.contains(e.target)) menu.open = false; });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && menu.open) { menu.open = false; menu.querySelector("summary").focus(); } });
    if (wide.addEventListener) wide.addEventListener("change", function () { apply(table, hidden); });
    sync();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize, { once: true });
  else initialize();
})();
