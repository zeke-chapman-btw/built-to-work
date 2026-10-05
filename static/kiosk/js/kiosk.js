(function () {
  "use strict";
  const homeUrl = document.body.dataset.homeUrl || window.location.pathname;
  const idleScreen = document.querySelector("[data-idle-screen]");
  const overlay = document.getElementById("idle-warning");
  if (idleScreen && overlay) {
    const count = document.getElementById("idle-count");
    let idleTimer, warningTimer;
    const hide = () => {
      clearInterval(warningTimer);
      overlay.hidden = true;
      overlay.classList.remove("is-visible");
      overlay.setAttribute("aria-hidden", "true");
    };
    const stop = () => { clearTimeout(idleTimer); hide(); };
    const reset = () => {
      stop();
      if (document.visibilityState !== "visible") return;
      idleTimer = setTimeout(() => {
        overlay.hidden = false;
        overlay.classList.add("is-visible");
        overlay.setAttribute("aria-hidden", "false");
        let remaining = 5;
        if (count) count.textContent = remaining;
        warningTimer = setInterval(() => {
          remaining -= 1;
          if (count) count.textContent = Math.max(0, remaining);
          if (remaining <= 0) { stop(); window.location.assign(homeUrl); }
        }, 1000);
      }, 15000);
    };
    ["pointerdown", "click", "keydown", "input", "change"].forEach(event =>
      document.addEventListener(event, reset, { passive: true, capture: true }));
    document.addEventListener("visibilitychange", () =>
      document.visibilityState === "visible" ? reset() : stop());
    window.addEventListener("pagehide", stop);
    reset();
  }
  if (document.querySelector("[data-ignore-scans]")) {
    let buffer = "", last = 0;
    document.addEventListener("keydown", event => {
      const now = performance.now();
      if (now - last > 90) buffer = "";
      last = now;
      if (event.key.length === 1 && !event.ctrlKey && !event.altKey) {
        buffer = (buffer + event.key).slice(-100);
      } else if (event.key === "Enter" && buffer.length >= 6) {
        event.preventDefault(); event.stopImmediatePropagation(); buffer = "";
      } else if (event.key !== "Shift" && event.key !== "Tab") buffer = "";
    }, true);
  }
  document.querySelectorAll("[data-auto-return], [data-auto-go]").forEach(node => {
    const autoGo = node.hasAttribute("data-auto-go");
    let remaining = Number(autoGo ? node.dataset.autoGo : node.dataset.autoReturn);
    const output = node.querySelector(autoGo ? "[data-auto-go-count]" : "[data-auto-return-count]");
    if (!Number.isFinite(remaining) || remaining < 1) return;
    if (output) output.textContent = remaining;
    const interval = setInterval(() => {
      remaining -= 1;
      if (output) output.textContent = Math.max(0, remaining);
      if (remaining <= 0) {
        clearInterval(interval);
        window.location.assign(autoGo ? node.dataset.target : homeUrl);
      }
    }, 1000);
  });
  const answerForm = document.getElementById("answer-form");
  const timer = document.getElementById("timer");
  // Pending sections remain inactive until the countdown activation POST.
  if (answerForm && timer && timer.dataset.started !== "false") {
    let submitted = false;
    const deadline = Date.now() + Number(timer.dataset.seconds) * 1000;
    answerForm.addEventListener("submit", event => {
      if (submitted) { event.preventDefault(); return; }
      submitted = true;
      answerForm.style.pointerEvents = "none";
    });
    const update = () => {
      const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
      timer.textContent = remaining;
      timer.classList.toggle("is-urgent", remaining <= 10);
      return remaining;
    };
    update();
    const interval = setInterval(() => {
      if (submitted) { clearInterval(interval); return; }
      if (update() === 0) {
        clearInterval(interval);
        submitted = true;
        answerForm.elements.namedItem("action").value = "expire";
        answerForm.submit();
      }
    }, 200);
  }
})();
