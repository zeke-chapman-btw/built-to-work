/* Position form helpers:
   1) Follow-up questions ([data-show-when="field=value1,value2"]) appear only after a matching answer.
   2) [data-stepper] forms show one section at a time with Back / Continue and a progress bar. */
(function () {
  "use strict";

  function wrapperOf(input) {
    return input.closest(".ux-field, .btw-field, .field, [data-field]") || input.parentElement;
  }
  function currentValue(form, name) {
    const checked = form.querySelector('input[name="' + name + '"]:checked');
    if (checked) return checked.value;
    const el = form.querySelector('[name="' + name + '"]:not([type=radio]):not([type=hidden])');
    return el ? el.value : "";
  }
  function applyRules(form) {
    form.querySelectorAll("[data-show-when]").forEach(function (input) {
      const parts = input.getAttribute("data-show-when").split("=");
      const show = parts[1].split(",").indexOf(currentValue(form, parts[0])) !== -1;
      const box = wrapperOf(input);
      if (box) box.hidden = !show;
      input.disabled = !show;
    });
  }
  function initRules(form) {
    if (form.dataset.positionRules) return;
    if (!form.querySelector("[data-show-when]")) return;
    form.dataset.positionRules = "1";
    form.addEventListener("change", function () { applyRules(form); });
    applyRules(form);
  }

  function initStepper(form) {
    if (form.dataset.stepperReady) return;
    const steps = Array.prototype.slice.call(form.querySelectorAll("[data-step]"));
    if (!steps.length) return;
    form.dataset.stepperReady = "1";
    const label = form.querySelector("[data-step-label]");
    const count = form.querySelector("[data-step-count]");
    const bar = form.querySelector("[data-step-bar]");
    const back = form.querySelector("[data-step-back]");
    const next = form.querySelector("[data-step-next]");
    const save = form.querySelector("[data-step-save]");
    const dots = form.querySelectorAll("[data-step-dot]");
    let index = 0;
    const firstError = steps.findIndex(function (s) { return s.hasAttribute("data-has-errors"); });
    if (firstError > -1) index = firstError;
    function show(i, focus) {
      index = Math.max(0, Math.min(steps.length - 1, i));
      steps.forEach(function (s, n) { s.hidden = n !== index; });
      if (label) label.textContent = steps[index].getAttribute("data-step-title");
      if (count) count.textContent = "Step " + (index + 1) + " of " + steps.length;
      if (bar) bar.style.width = Math.round(((index + 1) / steps.length) * 100) + "%";
      if (back) back.hidden = index === 0;
      if (next) next.hidden = index === steps.length - 1;
      if (save) {
        const last = index === steps.length - 1;
        save.textContent = last ? "Save position" : "Save draft";
        save.classList.toggle("primary", last); save.classList.toggle("ux-primary", last);
      }
      dots.forEach(function (d, n) { d.setAttribute("aria-current", n === index ? "step" : "false"); d.classList.toggle("is-done", n < index); });
      if (focus) { const h = steps[index].querySelector("h2"); if (h) { h.setAttribute("tabindex", "-1"); h.focus(); } window.scrollTo({ top: 0, behavior: "smooth" }); }
    }
    if (back) back.addEventListener("click", function () { show(index - 1, true); });
    if (next) next.addEventListener("click", function () { show(index + 1, true); });
    dots.forEach(function (d, n) { d.addEventListener("click", function () { show(n, true); }); });
    show(index, false);
  }

  function scan(root) {
    (root || document).querySelectorAll("form").forEach(function (form) { initRules(form); initStepper(form); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { scan(); }, { once: true });
  else scan();
  new MutationObserver(function (records) {
    records.forEach(function (r) { r.addedNodes.forEach(function (n) { if (n.nodeType === 1) scan(n.matches && n.matches("form") ? n.parentElement : n); }); });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
