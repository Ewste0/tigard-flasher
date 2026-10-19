// Run: node tests/test_wiring.cjs. Pin numbers are physical connector pins.
const assert = require("node:assert/strict");
const {wiringGuide} = require("../spc5tool/webui/wiring.js");
for (const key of ["spc5748g", "spc58nh92"]) for (const reset of ["srst", "trst"]) {
  const guide = wiringGuide(key, reset);
  const rows = Object.fromEntries(guide.rows.map(row => [row.id, row]));
  assert.deepEqual(guide.pins.map(p => p[0]), Array.from({length:14}, (_, i) => i + 1));
  assert.equal(rows.reset.adapter, reset.toUpperCase());
  assert.equal(rows.reset.gpio, reset === "trst" ? "BD4" : "BD5");
  assert.equal(rows.reset.adapterPin, reset === "trst" ? 7 : 8);
  assert.deepEqual(rows.reset.pins, [9]);
  assert.deepEqual(rows.jcomp.pins, [14]);
  assert.notEqual(rows.reset.adapterPin, rows.jcomp.adapterPin);
  for (const [id, adapter, target] of [["tdi",4,1], ["tdo",5,3], ["clock",3,5], ["mode",6,10], ["power",1,11]]) {
    assert.equal(rows[id].adapterPin, adapter); assert.deepEqual(rows[id].pins, [target]);
  }
  assert.deepEqual(rows.ground.pins, [2,4,6,12]);
  for (const unused of [7,8,13]) assert(!guide.rows.some(row => row.pins.includes(unused)));
}
const generic = wiringGuide("spc560b64", "trst");
assert.equal(generic.pins.length, 10);
const miniRows = Object.fromEntries(generic.rows.map(row => [row.id, row]));
for (const [id, pin] of [["tdi",8], ["tdo",6], ["clock",4], ["mode",2], ["reset",10], ["power",1]]) assert.deepEqual(miniRows[id].pins, [pin]);
assert.deepEqual(miniRows.ground.pins, [3,5]);
for (const unused of [7,9]) assert(!generic.rows.some(row => row.pins.includes(unused)));
assert(!generic.rows.some(row => row.id === "jcomp"));
const spi = wiringGuide("w25q128jv");
assert.deepEqual(spi.pins.map(p => p[0]), [1,8,2,7,3,6,4,5]);
assert(!spi.rows.some(row => ["reset", "jcomp"].includes(row.id)));
assert.deepEqual(spi.rows.map(row => [row.adapterPin, ...row.pins]), [[6,6],[8,5],[3,2],[1,1],[5,3],[4,7],[7,4],[2,8]]);
assert.throws(() => wiringGuide("spc5748g", "invalid"));
console.log("Wiring checks passed: both reset mappings, 14-pin orientation, 10-pin JTAG and SPI.");
