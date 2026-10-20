// Run with node --test scripts/test_shell_focus_9g.cjs. No third-party packages.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {runInNewContext} = require('node:vm');
const {setImmediate: settle} = require('node:timers/promises');
const source = readFileSync(resolve(__dirname, '../static/internal/js/shell.js'), 'utf8');

// A small DOM/event fixture executes the real shell script. Resize explicitly
// models CSS hiding the focused backdrop BEFORE matchMedia change is delivered.
// This forces the observed ordering without sleeps or probabilistic timing.
function fixture(initialMobile = true) {
  const listeners = new Map();
  const document = {activeElement: null};
  const emit = (target, type, event = {}) => {
    for (const fn of listeners.get(target + ':' + type) || []) fn(event);
  };
  const listen = name => (type, fn) => {
    const key = name + ':' + type;
    listeners.set(key, [...(listeners.get(key) || []), fn]);
  };
  function element(name, parent = null) {
    const attributes = new Map(), classes = new Set();
    let inert = false;
    const el = {
      name, parent, hidden: false,
      classList: {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c)},
      addEventListener: listen(name),
      setAttribute: (key, value) => attributes.set(key, value),
      removeAttribute: key => attributes.delete(key),
      contains(other) { return other === el || Boolean(other?.parent && el.contains(other.parent)); },
      getClientRects() { return el.hidden || (name === 'toggle' && !media.matches) || (name === 'scrim' && !media.matches) ? [] : [{}]; },
      focus() {
        for (let p = el; p; p = p.parent) if (p.inert) return;
        if (!el.getClientRects().length) return;
        document.activeElement = el; emit('document', 'focusin', {target: el});
      },
    };
    Object.defineProperty(el, 'inert', {get: () => inert, set: value => {
      inert = value;
      if (value && el.contains(document.activeElement)) document.activeElement = body;
    }});
    return el;
  }
  const body = element('body'); document.body = body; document.activeElement = body;
  const sidebar = element('sidebar', body), first = element('first', sidebar), last = element('last', sidebar);
  const toggle = element('toggle', body), scrim = element('scrim', body), content = element('content', body);
  const media = {matches: initialMobile, addEventListener: listen('media')};
  sidebar.querySelector = () => first;
  sidebar.querySelectorAll = () => [first, last];
  document.getElementById = () => sidebar;
  document.querySelector = selector => selector === '[data-menu-toggle]' ? toggle : scrim;
  document.querySelectorAll = () => [toggle, content];
  document.addEventListener = listen('document');
  runInNewContext(source, {document, window: {matchMedia: () => media}});
  const key = (key, shiftKey = false) => emit('document', 'keydown', {key, shiftKey, preventDefault() {}});
  const click = el => emit(el.name, 'click');
  const resize = (mobile, cssDropsFocus = false) => {
    const changed = media.matches !== mobile;
    media.matches = mobile;
    if (cssDropsFocus) document.activeElement = body;
    if (changed) emit('media', 'change');
  };
  return {document, body, sidebar, first, last, toggle, scrim, content, media, key, click, resize};
}

test('backdrop focus lost before breakpoint handling restores visible focus in 20 settled trials', async () => {
  const f = fixture();
  for (let trial = 0; trial < 20; trial++) {
    f.resize(true); f.click(f.toggle); f.key('Tab', true);
    assert.equal(f.document.activeElement, f.scrim);
    f.resize(false, true);
    await settle(); await settle();
    assert.equal(f.document.activeElement, f.first, 'trial ' + trial);
    assert.equal(f.sidebar.inert, false);
    assert.equal(f.content.inert, false);
    assert.ok(f.first.getClientRects().length);
  }
});

test('open drawer also restores focus when CSS has not yet dropped backdrop focus', async () => {
  const f = fixture(); f.click(f.toggle); f.scrim.focus(); f.resize(false);
  await settle(); assert.equal(f.document.activeElement, f.first);
});

test('closed drawer does not steal content focus on entering desktop', async () => {
  const f = fixture(); f.content.focus(); f.resize(false);
  await settle(); assert.equal(f.document.activeElement, f.content);
});

test('ordinary desktop resizing preserves focus and does not introduce a trap', () => {
  const f = fixture(false); f.content.focus(); f.resize(false); f.key('Tab');
  assert.equal(f.document.activeElement, f.content); assert.equal(f.content.inert, false);
});

test('desktop navigation entering mobile restores toggle and isolates closed sidebar', () => {
  const f = fixture(false); f.last.focus(); f.resize(true);
  assert.equal(f.document.activeElement, f.toggle); assert.equal(f.sidebar.inert, true);
});

test('open focus wraps in both directions and Escape restores focus and background', () => {
  const f = fixture(); f.click(f.toggle);
  assert.equal(f.content.inert, true); assert.equal(f.toggle.inert, true);
  f.key('Tab', true); assert.equal(f.document.activeElement, f.scrim);
  f.key('Tab'); assert.equal(f.document.activeElement, f.first);
  f.key('Escape'); assert.equal(f.document.activeElement, f.toggle);
  assert.equal(f.sidebar.inert, true); assert.equal(f.content.inert, false);
});

test('backdrop closure restores focus to toggle without leaving background inert', () => {
  const f = fixture(); f.click(f.toggle); f.click(f.scrim);
  assert.equal(f.document.activeElement, f.toggle); assert.equal(f.content.inert, false);
});
