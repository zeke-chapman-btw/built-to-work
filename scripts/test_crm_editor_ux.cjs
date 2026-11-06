/* Canonical CRM editor navigation behavior; no browser or application writes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/crm/editor.js'), 'utf8');
function editor(present = true) {
  const events = {}, formEvents = {}, values = {summary: '', operation_id: 'synthetic-fixed-operation'};
  let focusCount = 0;
  const form = {addEventListener: (name, fn) => { formEvents[name] = fn; }};
  class FormData { constructor() { return Object.entries(values); } }
  vm.runInNewContext(source, {
    document: {querySelector: selector => selector === '[data-crm-unsaved]' ? (present ? form : null) : {focus: () => { focusCount++; }}},
    window: {addEventListener: (name, fn) => { events[name] = fn; }}, FormData, URLSearchParams,
  });
  function leave() {
    const event = {prevented: false, preventDefault() { this.prevented = true; }};
    events.beforeunload?.(event);
    return event;
  }
  return {events, values, formEvents, leave, focusCount: () => focusCount};
}
const ordinary = editor(false);
assert.deepEqual(Object.keys(ordinary.events), []);
assert.equal(ordinary.focusCount(), 0);
const crm = editor();
assert.equal(crm.focusCount(), 1);
assert.equal(crm.leave().prevented, false);
crm.values.summary = 'SYNTHETIC unsaved answer';
assert.equal(crm.leave().prevented, true);
assert.equal(crm.leave().returnValue, '');
crm.values.summary = '';
assert.equal(crm.leave().prevented, false);
crm.values.summary = 'SYNTHETIC submitted answer';
crm.formEvents.submit();
assert.equal(crm.leave().prevented, false);
assert.equal(crm.values.operation_id, 'synthetic-fixed-operation');
console.log('CRM editor: 9 navigation, error-focus and isolation checks passed.');
