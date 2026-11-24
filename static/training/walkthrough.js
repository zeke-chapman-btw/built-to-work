// Step-by-step walk-through: a small box that stays on screen while people do the task.
(function () {
  var KEY = 'btwGuide';
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { return null; } }
  function save(s) { try { if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); } catch (e) {} }
  function csrf() {
    var input = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (input) return input.value;
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  function onPage(step) {
    if (!step.page) return true;
    var here = location.pathname, want = step.page.split('?')[0].split('#')[0];
    return here === want || (want.length > 1 && want.endsWith('/') && here.indexOf(want) === 0 && want.split('/').length > 3);
  }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  var box, lit;

  function clearHighlight() { if (lit) { lit.classList.remove('btw-guide-target'); lit = null; } }
  function highlight(step) {
    clearHighlight();
    if (!step.target || !onPage(step)) return;
    var found = null;
    step.target.split('||').some(function (sel) {
      sel = sel.trim();
      if (!sel) return false;
      try {
        if (sel.indexOf('text:') === 0) {
          var words = sel.slice(5).trim().toLowerCase();
          found = Array.prototype.find.call(document.querySelectorAll('a,button,summary,label,[role=button]'), function (n) {
            return n.offsetParent !== null && n.textContent.trim().toLowerCase().indexOf(words) === 0;
          }) || null;
        } else {
          found = Array.prototype.find.call(document.querySelectorAll(sel), function (n) { return n.offsetParent !== null; }) || null;
        }
      } catch (e) { found = null; }
      return !!found;
    });
    if (found) {
      lit = found; lit.classList.add('btw-guide-target');
      try { lit.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch (e) {}
    }
  }

  function render() {
    var s = load();
    if (box) { box.remove(); box = null; }
    clearHighlight();
    if (!s || !s.steps || !s.steps.length) return;
    var step = s.steps[s.step] || s.steps[0];
    box = el('aside', 'btw-guide' + (s.small ? ' is-small' : ''));
    box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'Walk-through: ' + s.title);
    var head = el('div', 'btw-guide-head');
    head.appendChild(el('span', 'btw-guide-kicker', 'Step ' + (s.step + 1) + ' of ' + s.steps.length + ' · ' + s.title));
    var tools = el('span', 'btw-guide-tools');
    var min = el('button', 'btw-guide-icon', s.small ? '▢' : '–'); min.type = 'button'; min.title = s.small ? 'Show' : 'Make smaller';
    min.onclick = function () { s.small = !s.small; save(s); render(); };
    var close = el('button', 'btw-guide-icon', '×'); close.type = 'button'; close.title = 'Stop the walk-through';
    close.onclick = function () { save(null); render(); };
    tools.appendChild(min); tools.appendChild(close); head.appendChild(tools); box.appendChild(head);
    if (!s.small) {
      box.appendChild(el('h3', 'btw-guide-title', step.title));
      if (step.body) box.appendChild(el('p', 'btw-guide-body', step.body));
      var bar = el('div', 'btw-guide-bar'); var fill = el('i'); fill.style.width = Math.round(100 * (s.step + 1) / s.steps.length) + '%'; bar.appendChild(fill); box.appendChild(bar);
      var actions = el('div', 'btw-guide-actions');
      if (!onPage(step)) {
        var go = el('a', 'btw-guide-go', 'Take me there →'); go.href = step.page; actions.appendChild(go);
      }
      var back = el('button', 'btw-guide-back', 'Back'); back.type = 'button'; back.disabled = s.step === 0;
      back.onclick = function () { s.step = Math.max(0, s.step - 1); save(s); render(); };
      var last = s.step >= s.steps.length - 1;
      var next = el('button', 'btw-guide-next', last ? 'Finish' : 'Next'); next.type = 'button';
      next.onclick = function () {
        if (!last) { s.step += 1; save(s); var nx = s.steps[s.step]; if (nx.page && !onPage(nx) && s.autoGo) { location.href = nx.page; } else { render(); } return; }
        var url = s.done_url; save(null); clearHighlight();
        if (url) { fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': csrf(), 'X-Requested-With': 'fetch' } }).catch(function () {}); }
        box.innerHTML = ''; box.appendChild(el('h3', 'btw-guide-title', 'All done ✓')); box.appendChild(el('p', 'btw-guide-body', 'Nice work. This guide is marked as done.'));
        setTimeout(function () { if (box) { box.remove(); box = null; } }, 3500);
      };
      actions.appendChild(back); actions.appendChild(next); box.appendChild(actions);
    }
    document.body.appendChild(box);
    highlight(step);
  }

  // Pressing the highlighted button or link moves the walk-through on by one step.
  document.addEventListener('click', function (e) {
    if (!lit || box && box.contains(e.target)) return;
    if (lit === e.target || lit.contains(e.target)) {
      var s = load(); if (!s) return;
      if (s.step < s.steps.length - 1) { s.step += 1; save(s); setTimeout(render, 400); }
    }
  }, true);

  document.addEventListener('click', function (e) {
    var start = e.target.closest('[data-guide-start]');
    if (!start) return;
    e.preventDefault();
    fetch(start.getAttribute('data-guide-start'), { credentials: 'same-origin' }).then(function (r) { return r.json(); }).then(function (data) {
      var s = { slug: data.slug, title: data.title, steps: data.steps, done_url: data.done_url, step: 0, autoGo: true };
      save(s);
      var first = s.steps[0];
      if (first && first.page && !onPage(first)) { location.href = first.page; } else { render(); }
    }).catch(function () {});
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render); else render();
})();
