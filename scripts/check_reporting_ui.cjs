/* Regression harness for complete printing and keyboard recovery; no browser dependencies. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const events = {}, media = [], cases = [];
function panel(open, errors = false) {
  const value = { open, errors, handlers: {}, focused: false };
  value.hasAttribute = name => name === 'data-filter-errors' && errors;
  value.querySelector = () => ({ focus() { value.focused = true; } });
  value.addEventListener = (name, handler) => { value.handlers[name] = handler; };
  return value;
}
const filters = [panel(false), panel(false, true)];
const disclosures = [panel(false), panel(true)];
let printCalls = 0, prevented = false;
const document = {
  addEventListener(name, handler) { events['document:' + name] = handler; },
  querySelectorAll(selector) {
    return selector === '[data-report-filters]' ? filters : disclosures;
  }
};
const window = {
  matchMedia() {
    const value = { matches: false, addEventListener(name, handler) { value.change = handler; } };
    media.push(value);
    return value;
  },
  addEventListener(name, handler) { events[name] = handler; },
  print() {
    printCalls++;
    events.beforeprint();
    assert(disclosures.every(value => value.open));
    events.afterprint();
  }
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/reporting/reporting.js'), 'utf8'), { document, window });
assert.equal(filters[0].open, false);
assert.equal(filters[1].open, true);
cases.push('phone filters compact; validation errors expanded');
events.beforeprint();
assert(disclosures.every(value => value.open));
events.beforeprint();
events.afterprint();
assert.deepEqual(disclosures.map(value => value.open), [false, true]);
cases.push('printing expands all content and restores original states, including repeated beforeprint');
let stopped = false;
disclosures[1].handlers.keydown({ key: 'Escape', preventDefault() {}, stopPropagation() { stopped = true; } });
assert.equal(disclosures[1].open, false);
assert(disclosures[1].focused && stopped);
cases.push('Escape closes the disclosure and restores summary focus');
events['document:click']({ target: { closest() { return {}; } }, preventDefault() { prevented = true; } });
assert.equal(printCalls, 1);
assert(prevented);
assert.deepEqual(disclosures.map(value => value.open), [false, false]);
cases.push('print control invokes complete printing once');
for (const value of media) { value.matches = true; value.change(); }
assert(filters.every(value => value.open));
for (const value of media) { value.matches = false; value.change(); }
assert.equal(filters[0].open, false);
assert.equal(filters[1].open, true);
cases.push('responsive filter transition preserves the error state');
process.stdout.write(JSON.stringify({ status: 'passed', cases }, null, 2) + '\n');
