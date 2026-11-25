// Exercise the production UI functions with a small DOM, without a live server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/app.js', 'utf8');
const element = (seat) => ({
  dataset: {seat}, disabled: false, textContent: '', hidden: true, attributes: {},
  classes: new Set(), setAttribute(key, value) { this.attributes[key] = value; },
  classList: {toggle() {}},
});
const seats = ['A1', 'A2'].map(element);
const fields = Object.fromEntries([
  '#selected-count', '#selected-label', '#total-amount', '#confirm-booking',
  '#quantity-value', '#quantity-minus', '#quantity-plus', '#remaining',
  '#countdown', '#start-booking', '#sale-reset',
].map(key => [key, element()]));
const notices = [];
const context = vm.createContext({
  document: {querySelectorAll: () => seats}, $: key => fields[key],
  screen: 'select', chosen: new Set(['A1', 'A2']), reserved: true,
  remaining: 28, quantity: 1, price: 55000, blocked: false, busy: false, offline: false,
  storage: {setItem() {}}, attemptId: 'test',
  fieldError: (...args) => notices.push(args), serverOffset: 0,
});
vm.runInContext(source.slice(source.indexOf('  function updateInventory('),
  source.indexOf("  if (screen === 'select') {", source.indexOf('  function updateInventory('))), context);
context.updateInventory({sold: ['A1'], remaining: 28});
assert.equal(seats[0].disabled, true);
assert.equal(context.chosen.has('A1'), false);
assert.equal(context.chosen.has('A2'), true);
assert.equal(fields['#total-amount'].textContent, '55,000원');
assert.equal(notices.length, 1);
context.updateInventory({sold: ['A1', 'A2'], remaining: 28});
assert.equal(fields['#confirm-booking'].disabled, true);
context.updateInventory({sold: [], remaining: 28});
assert.equal(seats[0].disabled, false);
assert.equal(seats[0].attributes['aria-label'], 'A열 1번 선택 가능');
context.reserved = false;
context.quantity = 4;
context.updateInventory({sold: [], remaining: 2});
assert.equal(fields['#remaining'].textContent, 2);
assert.equal(fields['#confirm-booking'].disabled, true);
context.quantity = 2;
context.renderSelection();
assert.equal(fields['#confirm-booking'].disabled, false);

vm.runInContext(source.slice(source.indexOf('  function countdown()'), source.indexOf('\n  countdown();')), context);
fields['#countdown'].dataset = {opensAt: Date.now() + 60000, resetsAt: Date.now() + 3660000};
context.countdown();
assert.equal(fields['#start-booking'].disabled, true);
fields['#countdown'].dataset.opensAt = Date.now() - 1000;
context.countdown();
assert.equal(fields['#start-booking'].disabled, false);
assert.match(fields['#sale-reset'].textContent, /좌석 초기화까지/);
console.log('Seat updates, lost selections, stock changes and opening countdown passed.');
