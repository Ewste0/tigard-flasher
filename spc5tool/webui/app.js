"use strict";
const $ = (id) => document.getElementById(id);
const paths = {
  folder:'<path d="M3 7V5h6l2 2h10v12H3z"/><path d="M3 10h18"/>',
  save:'<path d="M4 3h13l3 3v15H4z"/><path d="M8 3v6h8V3M8 21v-8h8v8"/>',
  flask:'<path d="M9 3h6m-5 0v7L4 20h16L14 10V3M7 15h10"/>',
  scan:'<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M7 12h10M12 7v10"/>',
  terminal:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3m6 0h4"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
  upload:'<path d="M12 15V3m-4 4 4-4 4 4M4 16v5h16v-5"/>',
  check:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  eraser:'<path d="m4 12 9-9 8 8-9 9H7l-5-5zm3-3 8 8M12 20h9"/>',
  square:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8v8H8z"/>',
  shield:'<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 5v2"/>',
  chip:'<rect x="6" y="6" width="12" height="12" rx="1"/><path d="M9 1v5m6-5v5M9 18v5m6-5v5M1 9h5m-5 6h5m12-6h5m-5 6h5M10 10h4v4h-4z"/>',
  cable:'<path d="m7 7 3 3-4 4-3-3zm1-1 3-3m-1 5 3-3m4 12-3-3 4-4 3 3zm-1 1-3 3m1-5-3 3M8 12c5 6 9-5 4-4"/>',
  usb:'<path d="M12 21V3m-3 3 3-3 3 3m-3 9 5-3V9m-5 8-5-3v-4"/><circle cx="7" cy="8" r="2"/><path d="M15 5h4v4h-4z"/><circle cx="12" cy="21" r="1"/>',
  file:'<path d="M5 3h9l5 5v13H5zM14 3v6h5M9 13h6m-6 4h4"/>',
  expand:'<path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6"/>',
  settings:'<path d="m9 3-1 3-3 1v4l-2 1 2 2v4l3 1 1 2h6l1-2 3-1v-4l2-2-2-1V7l-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>'
};
for (const element of document.querySelectorAll("[data-icon]")) {
  element.insertAdjacentHTML("afterbegin", `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[element.dataset.icon]}</svg>`);
}
let targets = [], target, region, file = null, busy = false, ready = false;
const mode = "hardware";
let frequency = 5000000, expectedId = "", pendingPlan = null, resetPin = "srst";
let fastRead = true, readDelayUs = 10;
let seenLogs = 0, eventCount = 0, toastTimer, previewSequence = 0;
let activeJob = null, suggestedName = "";
let adapterState = null, adapterChecking = false;
let adapterRequest = 0, adapterSaving = false;
let viewBytes = null, viewFileId = null, scrollFrame = null;
const HEX_ROW_HEIGHT = 22;
const hex = (n) => "0x" + Number(n).toString(16).toUpperCase().padStart(8, "0");
const sizeText = (n) => n >= 1048576 ? `${Number((n / 1048576).toFixed(2))} MiB` : n >= 1024 ? `${Number((n / 1024).toFixed(2))} KiB` : `${n} bytes`;
const number = (value) => {
  const text = String(value).trim();
  if (!/^(0x[0-9a-f]+|[0-9]+)$/i.test(text)) throw new Error("Use decimal or 0x-prefixed hexadecimal for the address and length.");
  const result = Number(text);
  if (!Number.isSafeInteger(result) || result < 0 || result > 0xFFFFFFFF) throw new Error("Address or length is outside the supported range.");
  return result;
};
async function api(path, data, raw = false) {
  const options = data === undefined ? {} : {method:"POST", headers:{"Content-Type":raw ? "application/octet-stream" : "application/json"}, body:raw ? data : JSON.stringify(data)};
  let response;
  try { response = await fetch(path, options); }
  catch { throw new Error("Local server is unavailable. Restart spc5tool gui, then reload this page."); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Operation failed");
  return result;
}
function message(error) {
  $("toast").textContent = error.message || String(error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 9000);
  log(error.message || String(error), "ERROR");
}
function log(text, level = "INFO", timestamp = Date.now()) {
  const row = document.createElement("div"); row.className = "log-entry " + (level === "ERROR" ? "error" : level === "OK" ? "success" : "");
  const time = document.createElement("time"); time.textContent = new Date(timestamp).toLocaleTimeString("en-GB");
  const badge = document.createElement("span"); badge.className = "log-level"; badge.textContent = level;
  const content = document.createElement("span"); content.textContent = text;
  row.append(time, badge, content); $("eventLog").append(row);
  while ($("eventLog").children.length > 250) $("eventLog").firstChild.remove();
  $("eventLog").scrollTop = $("eventLog").scrollHeight;
  $("logCount").textContent = `${++eventCount} events`;
}
function controls() {
  for (const button of document.querySelectorAll("[data-operation]")) {
    const action = button.dataset.operation;
    const mutation = ["write", "program", "erase"].includes(action);
    if (action === "program") button.hidden = region?.kind !== "otp";
    button.disabled = !ready || busy || (mutation && (target?.read_only || region?.writable === false)) ||
      (action === "program" && region?.kind !== "otp") ||
      (["write", "erase"].includes(action) && region?.kind === "otp") ||
      (["write", "program", "verify"].includes(action) && !file) ||
      (action === "authenticate" && !target?.password_login) ||
      (action === "install_password" && !target?.password_install);
  }
  for (const id of ["openButton", "emptyOpen", "identifyButton", "settingsButton", "familySelect", "chipSelect", "addressInput", "lengthInput", "regionRange", "readScope", "saveName", "currentPassword", "newPassword", "confirmPassword", "passwordSlot"]) $(id).disabled = !ready || busy;
  $("currentPassword").disabled = !ready || busy || !target?.password_login;
  for (const input of document.querySelectorAll(".region-option input, .block-card")) input.disabled = busy;
  $("saveButton").disabled = !file || busy;
  $("jumpButton").disabled = !viewBytes;
}
function setBusy(value) { busy = value; controls(); }
function setFamily() {
  const protocol = $("familySelect").value === "spi" ? "SPI" : "JTAG";
  $("chipSelect").replaceChildren(...targets.filter(t => t.protocol === protocol).map(t => new Option(t.name, t.key)));
  setTarget();
}
function setTarget() {
  expectedId = "";
  $("targetState").textContent = "Not identified";
  target = targets.find(t => t.key === $("chipSelect").value);
  for (const id of ["currentPassword", "newPassword", "confirmPassword"]) $(id).value = "";
  $("securityPanel").hidden = target.protocol !== "JTAG";
  $("passwordLength").textContent = target.password_bits ? `${target.password_bits / 4} hex digits` : "";
  for (const id of ["currentPassword", "newPassword", "confirmPassword"]) {
    $(id).maxLength = target.password_bits / 4 || 64;
    $(id).placeholder = `${target.password_bits / 4 || 64} hexadecimal digits`;
  }
  $("passwordSlot").replaceChildren(new Option("JTAG", "jtag"), ...(target.password_bits === 256 ? [0,1,2,3].map(n => new Option(`PASS group ${n}`, `group${n}`)) : []));
  $("passwordNote").textContent = !target.password_login
    ? "Password storage is supported; JTAG login is not yet supported for this device. Installing a password does not enable protection."
    : target.password_bits === 256
      ? "64 hex digits, in flash-address order. Power-cycle after installation. Customer Delivery checks the installed password while halted under reset. Protected-target entry may run firmware briefly before halt; unlocking awaits bench validation."
      : "Enter words in flash-address order (NVPWD0, then NVPWD1). Kept in memory for the session; supplied again after each reset.";
  $("protocolBadge").textContent = target.protocol;
  $("targetAlias").textContent = target.alias; $("targetCore").textContent = target.core;
  $("footerTarget").textContent = target.name; $("regionCount").textContent = String(target.regions.length).padStart(2, "0");
  $("regionList").replaceChildren();
  target.regions.forEach((r, index) => {
    const label = document.createElement("label"); label.className = "region-option";
    const radio = document.createElement("input"); radio.type = "radio"; radio.name = "region"; radio.value = r.name; radio.checked = !index; radio.setAttribute("aria-label", `${r.name}, ${sizeText(r.size)}`);
    const name = document.createElement("span"); name.className = "region-name";
    const title = document.createElement("strong"); title.textContent = r.name;
    const address = document.createElement("small"); address.textContent = `${hex(r.base)} · ${r.writable === false ? "Read" : r.kind === "otp" ? "Read / Program once" : "Read / Write / Erase"}`;
    const capacity = document.createElement("span"); capacity.className = "region-capacity"; capacity.textContent = sizeText(r.size);
    name.append(title, address); label.append(radio, name, capacity); $("regionList").append(label);
    radio.addEventListener("change", () => setRegion(r));
  });
  $("readScope").replaceChildren(...(target.read_presets || []).map(p => new Option(p.name, p.key)), new Option("Custom range", "custom"));
  $("profileNote").textContent = target.key.startsWith("spc58") ? "Hardware validation pending · 16-byte programming units" : "";
  setRegion(target.regions[0]);
  if (target.read_presets?.length) applyPreset(target.read_presets[0].key);
  renderWiring(); controls();
}
function applyPreset(key) {
  const preset = target.read_presets?.find(p => p.key === key);
  if (preset) { $("addressInput").value = hex(preset.address); $("lengthInput").value = hex(preset.size); }
  updateRange();
  suggestFilename(true);
}
function suggestFilename(force = false) {
  if (!target) return;
  const preset = target.read_presets?.find(p => p.key === $("readScope").value);
  const base = preset?.filename || `${target.key.toUpperCase()}_${$("addressInput").value}`;
  const stamp = new Date().toISOString().slice(0,19).replace(/[:T]/g,"-");
  const name = `${base}_${stamp}_${mode}.bin`;
  if (force || !$("saveName").value || $("saveName").value === suggestedName) $("saveName").value = name;
  suggestedName = name;
}
function setRegion(value) {
  region = value; $("addressInput").value = hex(region.base);
  const firstBlock = target.blocks.find(b => b.address === region.base);
  $("lengthInput").value = hex(firstBlock ? firstBlock.size : target.sector);
  const preset = target.read_presets?.find(p => p.region_default === region.name);
  if (preset) { $("addressInput").value = hex(preset.address); $("lengthInput").value = hex(preset.size); }
  $("regionNote").textContent = region.note || "";
  updateRange(); renderMap(); controls();
}
function updateRange() {
  try {
    const address = number($("addressInput").value), length = number($("lengthInput").value);
    const selected = target?.regions.find(r => address >= r.base && address + length <= r.base + r.size);
    if (selected) {
      region = selected;
      for (const radio of document.querySelectorAll('[name="region"]')) radio.checked = radio.value === region.name;
      $("regionNote").textContent = region.note || "";
    }
    $("rangeHint").textContent = sizeText(length) + " selected";
    $("readScope").value = target?.read_presets?.find(p => p.address === address && p.size === length)?.key || "custom";
    suggestFilename();
    controls();
  }
  catch { $("rangeHint").textContent = "Enter a valid range"; }
}
function tab(name) {
  for (const key of ["buffer", "map", "wiring"]) {
    $(key + "Tab").setAttribute("aria-selected", String(key === name));
    $(key + "Tab").tabIndex = key === name ? 0 : -1;
    $(key + "View").hidden = key !== name;
  }
  $("workspaceTitle").textContent = {buffer:"Binary buffer", map:"Memory map", wiring:"Connection guide"}[name];
  if (name === "map") renderMap();
  if (name === "buffer" && viewBytes) requestAnimationFrame(preview);
}
function renderMap() {
  let blocks = target.blocks;
  if (target.protocol === "SPI") {
    const start = Math.min(Math.max(0, Number($("addressInput").value) || 0), target.regions[0].size - 1);
    const index = Math.floor(start / target.sector);
    blocks = Array.from({length:Math.min(64, target.regions[0].size / target.sector - index)}, (_, i) => ({name:`Sector ${index + i}`, address:(index + i) * target.sector, size:target.sector}));
    $("mapView").querySelector(".view-intro p").textContent = "Up to 64 sectors from the start address. Select a block to set its erase range.";
  } else $("mapView").querySelector(".view-intro p").textContent = "Select a block to set the range. OTP supports Program once; factory and HSM regions are read-only.";
  $("blockGrid").replaceChildren(...blocks.map(block => {
    const button = document.createElement("button"); button.className = "block-card"; button.disabled = busy;
    const name = document.createElement("strong"); name.textContent = block.name;
    const address = document.createElement("small"); address.textContent = hex(block.address);
    const capacity = document.createElement("span"); capacity.textContent = sizeText(block.size);
    button.append(name, address, capacity);
    button.addEventListener("click", () => {
      $("addressInput").value = hex(block.address); $("lengthInput").value = hex(block.size); updateRange();
      const match = target.regions.find(r => r.base <= block.address && r.base + r.size > block.address);
      if (match) { region = match; for (const radio of document.querySelectorAll('[name="region"]')) radio.checked = radio.value === match.name; }
      log(`Selected ${block.name}: ${hex(block.address)}, ${sizeText(block.size)}.`);
    });
    return button;
  }));
}
let currentWiring;
function pinButton(pin, label, wire, className = "") {
  const element = document.createElement(wire ? "button" : "span");
  element.className = `pin ${className} ${wire ? "wire-" + wire.id : "pin-unused"}`;
  if (wire) { element.type = "button"; element.dataset.wire = wire.id; element.setAttribute("aria-pressed", "false"); }
  const number = document.createElement("b"); number.textContent = pin;
  const signal = document.createElement("span"); signal.textContent = label;
  element.append(number, signal); return element;
}
function selectWire(id) {
  const wire = currentWiring.rows.find(row => row.id === id);
  if (!wire) return;
  for (const element of $("wiringView").querySelectorAll("[data-wire]")) {
    const selected = element.dataset.wire === id;
    element.classList.toggle("wire-selected", selected);
    if (element.tagName === "BUTTON") element.setAttribute("aria-pressed", String(selected));
  }
  const pins = wire.pins.length ? `pin ${wire.pins.join(" / ")} · ` : "";
  $("selectedWire").textContent = `Tigard ${wire.adapterPin} · ${wire.adapter} → ${pins}${wire.signal}. ${wire.note}`;
}
function renderWiring() {
  const guide = currentWiring = wiringGuide(target.key, resetPin);
  $("wiringIntro").textContent = "Select a pin or a table row to trace the connection. Available offline.";
  $("wiringConfig").textContent = guide.spi ? "SPI · CS0 · mode 0" : `Session reset: ${guide.resetLead}`;
  $("adapterHeaderTitle").textContent = `${guide.spi ? "SPI / I²C · 2 × 4" : "JTAG · 1 × 8"} header · channel B`;
  $("adapterVisual").classList.toggle("spi-visual", guide.spi);
  const labels = guide.spi ? ["CS", "VTGT", "CIPO", "EN", "WP", "SCK", "GND", "COPI"] : ["VTGT", "GND", "TCK", "TDI", "TDO", "TMS", "TRST", "SRST"];
  $("adapterPins").replaceChildren(...labels.map((label, i) => pinButton(i + 1, label, guide.rows.find(row => row.adapterPin === i + 1))));
  $("connectorTitle").textContent = guide.title;
  $("connectorSubtitle").textContent = guide.mpc ? "Connector reference · 2 × 7" : guide.mini ? "Connector reference · 2 × 5" : "SOIC-8 reference · match package pin 1";
  $("connectorOrientation").textContent = guide.orientation;
  $("connectorReference").hidden = !guide.reference;
  if (guide.reference) {
    const img = $("connectorReference").querySelector("img"); img.src = guide.reference;
    img.alt = `${guide.pins.length}-pin target connector diagram`;
  }
  $("targetConnector").className = `target-connector ${guide.spi ? "spi-connector" : "jtag-connector"}`;
  $("targetConnector").replaceChildren(...(guide.pins.length ? guide.pins.map(([pin, label]) => pinButton(pin, label, guide.rows.find(row => row.pins.includes(pin)))) : guide.rows.map(row => pinButton("—", row.signal, row))));
  $("wiringRows").replaceChildren(...guide.rows.map(wire => {
    const tr = document.createElement("tr"); tr.dataset.wire = wire.id; tr.className = "wire-" + wire.id;
    const adapter = document.createElement("td"); adapter.append(pinButton(wire.adapterPin, wire.adapter, wire));
    const gpio = document.createElement("td"); gpio.textContent = wire.gpio;
    const destination = document.createElement("td"); destination.textContent = `${wire.pins.length ? wire.pins.join(", ") + " · " : ""}${wire.signal}`;
    tr.append(adapter, gpio, destination); return tr;
  }));
  $("selectedWire").textContent = "Select a signal to highlight both ends of its connection.";
  $("wiringNote").textContent = guide.spi ? "Set the mode switch to JTAG/SPI. Match the flash supply voltage. Keep WP# and HOLD#/RESET# inactive; Tigard’s SPI header provides pull-ups on WP and EN. For a separately powered target, select VTGT."
    : `Set the mode switch to JTAG/SPI. For a separately powered target, select VTGT and connect the target’s debug I/O reference. ${guide.mpc ? `This session pulses ${guide.resetLead} → pin 9 RESET low and holds ${guide.jcompLead} → pin 14 JCOMP HIGH. JCOMP enables JTAG; it is separate from MCU reset.` : `This session uses ${guide.resetLead} for active-low functional reset. Locate the correct reset signal in the board schematic.`}`;
  $("packageNote").textContent = `Colors link signals in this guide; wire colors may differ. ${guide.mpc ? "Pins 7, 8 and 13 are unused in this connector reference. Confirm connector orientation before connecting." : guide.spi ? "This drawing applies to the 8-pin package; verify other package variants separately." : "Pin 7 is NC. Pin 9 is JTAG_MOD / NC* in this connector reference: leave unconnected unless your board documentation requires it. It is not assigned to JCOMP here."} Opening this guide does not access USB.`;
}
$("wiringView").addEventListener("click", event => {
  const item = event.target.closest("[data-wire]"); if (item) selectWire(item.dataset.wire);
});
async function loadFile(info) {
  file = info;
  $("fileName").textContent = file.name;
  $("saveName").value = file.name;
  suggestedName = file.name;
  $("fileDetails").textContent = `${sizeText(file.size)} · ${file.size.toLocaleString()} bytes · ${file.kind === "dump" ? "Flash readout" : "Raw binary image"}`;
  $("fileTag").textContent = file.kind === "dump" ? "READ COMPLETE" : "BINARY IMAGE";
  $("fileTag").classList.add("loaded"); $("checksumValue").textContent = file.sha256;
  $("emptyBuffer").hidden = true; $("hexScroll").hidden = false;
  tab("buffer");
  const sequence = ++previewSequence;
  if (viewFileId !== info.id) {
    viewBytes = null; viewFileId = null; $("hexRows").replaceChildren();
    $("hexCanvas").style.height = "0px";
    $("bufferPosition").textContent = "Loading dump…";
    const response = await fetch(`/api/download/${info.id}`);
    if (!response.ok) throw new Error("Could not load the dump for scrolling.");
    const data = new Uint8Array(await response.arrayBuffer());
    if (sequence !== previewSequence) return;
    if (data.length !== info.size) throw new Error("The dump download was incomplete.");
    viewBytes = data; viewFileId = info.id;
  }
  $("hexCanvas").style.height = `${Math.ceil(viewBytes.length / 16) * HEX_ROW_HEIGHT}px`;
  $("hexScroll").scrollTop = 0;
  $("jumpOffset").value = "";
  preview(); controls();
}
function preview() {
  if (!viewBytes || !file || viewFileId !== file.id) return;
  const viewport = $("hexScroll"), rowCount = Math.ceil(viewBytes.length / 16);
  const visibleRow = Math.min(rowCount - 1, Math.floor(viewport.scrollTop / HEX_ROW_HEIGHT));
  const visibleCount = Math.ceil((viewport.clientHeight || 360) / HEX_ROW_HEIGHT);
  // Keep only the viewport and a few surrounding rows in the DOM, including
  // for the largest supported 16 MiB dump. The scrollbar spans the whole file.
  const firstRow = Math.max(0, visibleRow - 8);
  const lastRow = Math.min(rowCount, visibleRow + visibleCount + 9);
  const fragment = document.createDocumentFragment();
  for (let index = firstRow; index < lastRow; index++) {
    const rowOffset = index * 16;
    const line = document.createElement("span"); line.className = "hex-row";
    line.dataset.offset = rowOffset;
    const address = document.createElement("span"); address.className = "hex-offset"; address.textContent = rowOffset.toString(16).toUpperCase().padStart(8,"0");
    const bytes = document.createElement("span"); bytes.className = "hex-bytes";
    let ascii = "";
    for (let i = 0; i < 16; i++) {
      if (rowOffset + i >= viewBytes.length) { bytes.append("   "); continue; }
      const value = viewBytes[rowOffset + i], span = document.createElement("span");
      if (value === 255 || value === 0) span.className = "hex-byte-empty";
      span.textContent = value.toString(16).toUpperCase().padStart(2,"0") + (i === 15 ? "" : " "); bytes.append(span);
      ascii += value >= 32 && value <= 126 ? String.fromCharCode(value) : ".";
    }
    const text = document.createElement("span"); text.className = "hex-ascii"; text.textContent = ascii;
    line.append(address, bytes, text); fragment.append(line);
  }
  $("hexRows").style.transform = `translateY(${firstRow * HEX_ROW_HEIGHT}px)`;
  $("hexRows").replaceChildren(fragment);
  const start = visibleRow * 16;
  const end = Math.min(viewBytes.length - 1, (visibleRow + visibleCount) * 16 - 1);
  $("bufferPosition").textContent = `${hex(start)}–${hex(end)} · ${file.size.toLocaleString()} bytes`;
}
function queuePreview() {
  if (scrollFrame !== null) return;
  scrollFrame = requestAnimationFrame(() => { scrollFrame = null; preview(); });
}
$("hexScroll").addEventListener("scroll", queuePreview, {passive:true});
$("hexScroll").addEventListener("wheel", event => {
  // Use the same scroll position for wheel, keyboard and the virtual rows.
  // Leave browser zoom gestures and horizontal Shift-wheel gestures intact.
  if (event.ctrlKey || event.shiftKey || !viewBytes || !event.deltaY) return;
  const viewport = $("hexScroll");
  if (viewport.scrollHeight <= viewport.clientHeight) return;
  const scale = event.deltaMode === 1 ? HEX_ROW_HEIGHT : event.deltaMode === 2 ? viewport.clientHeight : 1;
  event.preventDefault();
  viewport.scrollTop += event.deltaY * scale;
  viewport.scrollLeft += event.deltaX * scale;
  queuePreview();
}, {passive:false});
window.addEventListener("resize", queuePreview);
$("hexScroll").addEventListener("keydown", event => {
  const viewport = $("hexScroll");
  const delta = {ArrowDown:HEX_ROW_HEIGHT, ArrowUp:-HEX_ROW_HEIGHT,
    PageDown:viewport.clientHeight, PageUp:-viewport.clientHeight}[event.key];
  if (delta !== undefined) { event.preventDefault(); viewport.scrollTop += delta; }
  else if (["Home", "End"].includes(event.key)) {
    event.preventDefault(); viewport.scrollTop = event.key === "Home" ? 0 : viewport.scrollHeight;
  }
});
$("jumpForm").addEventListener("submit", event => {
  event.preventDefault();
  if (!viewBytes) return;
  try {
    const address = number($("jumpOffset").value);
    if (address >= viewBytes.length) throw new Error(`Offset must be below ${hex(viewBytes.length)} for this file.`);
    $("hexScroll").scrollTop = Math.floor(address / 16) * HEX_ROW_HEIGHT;
    preview(); $("hexScroll").focus();
  } catch (error) { message(error); }
});

async function upload(input) {
  if (!input || busy) return;
  setBusy(true);
  try {
    if (!input.size || input.size > 16 * 1024 * 1024) throw new Error("Choose a nonempty raw binary up to 16 MiB.");
    const info = await api(`/api/files?name=${encodeURIComponent(input.name)}`, input, true);
    await loadFile(info); log(`Loaded ${info.name} (${sizeText(info.size)}). Write and verify use the image length.`);
  } catch (error) { message(error); } finally { setBusy(false); $("fileInput").value = ""; }
}
function request(action) {
  return {target:target.key, action, mode, frequency, expected_id:expectedId || null,
    ...(adapterState ? {adapter_revision:adapterState.revision} : {}),
    fast_read:fastRead && frequency >= 100000 && ["read", "write", "program", "erase", "verify", "blank"].includes(action) && target.key === "spc5748g" && document.querySelector('input[name="region"]:checked')?.value === "CFLASH",
    read_delay_us:readDelayUs,
    filename:$("saveName").value, password:target.password_login && action !== "identify" ? $("currentPassword").value : "",
    ...(action === "install_password" ? {new_password:$("newPassword").value, confirm_password:$("confirmPassword").value, password_slot:$("passwordSlot").value} : {}),
    address:action === "identify" ? 0 : number($("addressInput").value), length:action === "identify" ? 0 : number($("lengthInput").value), file_id:file?.id};
}
async function operation(action) {
  if (busy) return; setBusy(true);
  try {
    const plan = await api("/api/plan", request(action));
    if (action === "install_password") { $("newPassword").value = ""; $("confirmPassword").value = ""; }
    if (["write", "erase", "program", "install_password"].includes(action)) { review(plan); }
    else await run(plan);
  } catch (error) { message(error); setBusy(false); checkAdapter(); }
}
function review(plan) {
  pendingPlan = plan;
  $("confirmDialog").returnValue = "cancel";
  $("confirmMode").textContent = "TIGARD HARDWARE";
  $("confirmTitle").textContent = `Review ${plan.action}`;
  $("confirmText").textContent = "This operation changes the connected target. The MCU will be reset and halted.";
  const values = [["Target", target.name], ["Adapter", plan.adapter_url || "No adapter selected"], ["Start address", hex(plan.address)], ["Image / range", sizeText(plan.length)], ["Affected blocks", String(plan.blocks.length)], ["Backup size", sizeText(plan.backup_bytes)]];
  $("planDetails").replaceChildren(...values.map(([label, value]) => { const row = document.createElement("div"); const dt = document.createElement("dt"); dt.textContent = label; const dd = document.createElement("dd"); dd.textContent = value; row.append(dt, dd); return row; }));
  $("planNote").textContent = plan.action === "write" ? "Full affected blocks are backed up before erasing. Bytes outside the image are preserved. Written data is verified automatically." : "Selected blocks are backed up before erasing, then checked for blank bytes. The backup will be available to download.";
  if (["program", "install_password"].includes(plan.action)) $("planNote").textContent = plan.blocks.some(b => b.kind === "shadow") && plan.action === "install_password" ? "Back up SHADOW, preserve configuration, erase and rebuild it with the new password, then verify." : "Back up first, program erased ECC units, then verify. No erase is performed.";
  $("planNote").textContent += " " + (plan.notes || []).join(" ");
  $("confirmRun").textContent = `Run ${plan.action}`;
  $("confirmDialog").showModal();
}
async function run(plan) {
  $("resultActions").hidden = true; $("resultActions").replaceChildren();
  $("statusText").textContent = "Working"; $("statusDetail").textContent = `${plan.action} · ${plan.target}`;
  $("jobDot").className = "ready-dot busy"; seenLogs = 0;
  try {
    let job = await api("/api/run", {plan_id:plan.id});
    await follow(job);
  } catch (error) { message(error); $("statusText").textContent = "Error"; $("jobDot").className = "ready-dot failed"; }
  finally { setBusy(false); checkAdapter(); }
}
async function follow(job) {
  activeJob = job.id;
  while (true) {
    $("stopRead").hidden = !!job.finished || !job.cancellable;
    $("stopRead").disabled = !!job.cancel_requested;
    $("stopRead").textContent = job.cancel_requested ? "Stopping…" : job.plan.action === "read" ? "Stop read" : ["write", "erase", "program"].includes(job.plan.action) ? "Stop before changes" : "Stop";
    $("jobProgress").value = job.progress;
    if (!job.finished) { $("statusText").textContent = job.plan.action.replaceAll("_", " "); $("statusDetail").textContent = job.phase; }
    $("progressText").textContent = `${job.progress}%`;
    $("statusDetail").title = job.phase;
    for (const entry of job.logs.slice(seenLogs)) log(entry.message, "INFO", entry.time * 1000);
    seenLogs = job.logs.length;
    if (job.finished) break;
    await new Promise(resolve => setTimeout(resolve, 350));
    job = await api("/api/jobs/" + job.id);
  }
  const success = job.status === "complete";
  activeJob = null;
  const cancelled = job.status === "cancelled";
  $("statusText").textContent = success ? "Complete" : cancelled ? "Stopped" : "Failed";
  $("statusDetail").textContent = success || cancelled ? job.result.message : job.error;
  $("jobDot").className = "ready-dot" + (success ? "" : " failed");
  if (job.result.file) await loadFile(job.result.file);
  if (job.result.idcode !== undefined) { expectedId = hex(job.result.idcode); $("expectedId").value = expectedId; $("targetState").textContent = expectedId; $("adapterState").textContent = "Responding"; }
  if (job.plan.mode === "hardware" && success) { $("adapterState").textContent = "Tigard connected"; if (!job.result.idcode) $("targetState").textContent = `${job.plan.action.replaceAll("_", " ")} successful`; $("connectionNote").textContent = "Hardware responded to the last operation. Click Identify to recheck the current connection."; }
  for (const [label, info] of [["Download dump", job.result.file], ["Download backup ZIP", job.result.backup]]) {
    if (!info) continue;
    const link = document.createElement("a"); link.href = "/api/download/" + info.id; link.download = info.name; link.textContent = label;
    $("resultActions").append(link); $("resultActions").hidden = false;
  }
  if (!success && !cancelled) message(new Error(job.error));
  else if (cancelled) log(job.result.message);
  else log(`${job.plan.action.toUpperCase()} completed · hardware`, "OK");
}
function settings() {
  renderAdapterOptions(false);
  $("frequencySelect").value = String(frequency); $("expectedId").value = expectedId;
  $("readModeSelect").value = fastRead ? "queued" : "checked";
  $("readDelayUs").value = readDelayUs;
  $("settingsDialog").showModal();
  checkAdapter();
}
function modeDisplay() {
  $("modeBadge").replaceChildren(); $("modeBadge").append(document.createElement("i"), "Tigard hardware");
  $("modeBadge").classList.toggle("live", mode === "hardware");
  $("adapterState").textContent = "Not queried"; $("targetState").textContent = "Not identified";
  $("clockValue").textContent = frequency >= 1000000 ? `${frequency / 1000000} MHz` : `${frequency / 1000} kHz`;
  $("connectionNote").textContent = "Connect a target before identifying. An attached adapter alone is not a ready JTAG target.";
}
function renderAdapterOptions(preserveChoice = true) {
  const select = $("adapterSelect");
  const choice = preserveChoice && $("settingsDialog").open ? select.value : adapterState?.selection || "";
  const options = [new Option("Automatic · detect connected Tigard", "")];
  for (const device of adapterState?.devices || []) {
    if (device.url) options.push(new Option(`${device.description} · ${device.serial}`, device.url));
  }
  if (choice && !options.some(option => option.value === choice)) options.push(new Option(`${choice} · disconnected`, choice));
  select.replaceChildren(...options);
  select.value = choice;
  $("adapterNote").textContent = adapterState?.message || "Checking USB adapters…";
}
function applyAdapterState(state) {
  if (!adapterState || state.revision !== adapterState.revision) {
    expectedId = ""; $("expectedId").value = "";
    for (const id of ["currentPassword", "newPassword", "confirmPassword"]) $(id).value = "";
    pendingPlan = null;
    if ($("confirmDialog").open) $("confirmDialog").close("cancel");
    $("targetState").textContent = "Not identified";
    if (adapterState) log(`${state.message}. Identify the target again.`);
  }
  adapterState = state;
  renderAdapterOptions();
  $("adapterState").textContent = state.connected ? "Tigard connected" : state.devices.length ? "Select Tigard" : "Disconnected";
  if (!state.connected) $("targetState").textContent = "Not connected";
  $("connectionNote").textContent = state.connected ? `${state.message}. Identify checks the JTAG target; Read uses the selected range.` : state.message;
}
async function checkAdapter() {
  if (mode !== "hardware" || busy || !ready || adapterChecking || adapterSaving) return;
  adapterChecking = true;
  const requestId = ++adapterRequest;
  try {
    const state = await api("/api/adapter");
    if (mode !== "hardware" || busy || requestId !== adapterRequest) return;
    applyAdapterState(state);
  } catch (error) { if (!busy && mode === "hardware") { $("adapterState").textContent = "Check failed"; $("connectionNote").textContent = error.message; } }
  finally { adapterChecking = false; }
}
for (const id of ["openButton", "emptyOpen"]) $(id).addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", () => upload($("fileInput").files[0]));
$("saveButton").addEventListener("click", async () => {
  if (!file || busy) return;
  setBusy(true);
  try {
    file = await api("/api/rename", {file_id:file.id, name:$("saveName").value});
    $("fileName").textContent = file.name; $("saveName").value = file.name;
    const link = document.createElement("a"); link.href = "/api/download/" + file.id; link.download = file.name; link.click();
    log(`Saved as ${file.name}.`);
  } catch (error) { message(error); } finally { setBusy(false); }
});
$("readScope").addEventListener("change", () => applyPreset($("readScope").value));
$("stopRead").addEventListener("click", async () => {
  if (!activeJob) return;
  $("stopRead").disabled = true;
  try { await api("/api/cancel", {job_id:activeJob}); log("Stop requested; waiting for a safe checkpoint…"); }
  catch (error) { message(error); }
});
$("familySelect").addEventListener("change", setFamily); $("chipSelect").addEventListener("change", setTarget);
$("regionRange").addEventListener("click", () => { $("addressInput").value = hex(region.base); $("lengthInput").value = hex(region.size); updateRange(); });
$("lengthInput").addEventListener("input", updateRange);
$("addressInput").addEventListener("input", updateRange);
for (const name of ["buffer", "map", "wiring"]) $(name + "Tab").addEventListener("click", () => tab(name));
document.querySelector('[role="tablist"]').addEventListener("keydown", event => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault(); const names = ["buffer", "map", "wiring"]; let index = names.findIndex(name => $(name + "Tab").getAttribute("aria-selected") === "true");
  index = event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowRight" ? 1 : 2)) % 3;
  tab(names[index]); $(names[index] + "Tab").focus();
});
$("wiringButton").addEventListener("click", () => tab("wiring"));
for (const button of document.querySelectorAll("[data-operation]")) button.addEventListener("click", () => operation(button.dataset.operation));
$("identifyButton").addEventListener("click", () => operation("identify"));
$("settingsButton").addEventListener("click", settings);
$("settingsForm").addEventListener("submit", async event => {
  if (event.submitter.value !== "save") return;
  event.preventDefault();
  if (adapterSaving) return;
  try {
    let input = $("expectedId").value.trim(); if (input) number(input);
    const delay = Number($("readDelayUs").value);
    if (!Number.isInteger(delay) || delay < 1 || delay > 1000) throw new Error("Read delay must be 1–1000 microseconds.");
    if ($("adapterSelect").value !== (adapterState?.selection || "")) {
      adapterSaving = true; ++adapterRequest;
      const state = await api("/api/adapter", {url:$("adapterSelect").value || null});
      if (state.revision !== adapterState?.revision) input = "";
      applyAdapterState(state);
    }
    frequency = Number($("frequencySelect").value); expectedId = input;
    fastRead = $("readModeSelect").value === "queued"; readDelayUs = delay;
    $("settingsDialog").close("save");
    modeDisplay(); suggestFilename(); checkAdapter(); log(`Mode: ${mode}. Clock: ${frequency.toLocaleString()} Hz. No JTAG session opened.`);
  } catch (error) { message(error); }
  finally { adapterSaving = false; checkAdapter(); }
});
$("confirmDialog").addEventListener("close", () => {
  const plan = pendingPlan; pendingPlan = null;
  if ($("confirmDialog").returnValue === "execute" && plan) run(plan); else setBusy(false);
});
$("logButton").addEventListener("click", () => { const hidden = !$("activityPanel").hidden; $("activityPanel").hidden = hidden; $("logButton").setAttribute("aria-expanded", String(!hidden)); });
$("clearLog").addEventListener("click", () => { $("eventLog").replaceChildren(); eventCount = 0; $("logCount").textContent = "0 events"; });
for (const event of ["dragenter", "dragover"]) $("dropZone").addEventListener(event, e => { e.preventDefault(); if (!busy) $("dropZone").classList.add("dragging"); });
$("dropZone").addEventListener("dragleave", () => $("dropZone").classList.remove("dragging"));
$("dropZone").addEventListener("drop", e => { e.preventDefault(); $("dropZone").classList.remove("dragging"); upload(e.dataTransfer.files[0]); });
document.addEventListener("keydown", event => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "o" && !busy && ready && !document.querySelector("dialog[open]")) { event.preventDefault(); $("fileInput").click(); } });
async function initialize() {
  controls(); tab("buffer");
  try {
    const state = await api("/api/status"); targets = state.targets; resetPin = state.reset_pin || "srst";
    if (!state.hardware_enabled || state.initial_mode !== "hardware") throw new Error("Restart the app with the current live-only server.");
    frequency = state.frequency || 5000000;
    if (![...$("frequencySelect").options].some(option => Number(option.value) === frequency)) {
      $("frequencySelect").add(new Option(`${frequency.toLocaleString()} Hz`, String(frequency)));
    }
    modeDisplay();
    $("workspacePath").textContent = state.workspace;
    $("settingsReset").textContent = `MCU reset lead: ${resetPin.toUpperCase()}. Set by the launcher (--reset-pin ${resetPin}); the Wiring tab shows this session’s connections.`;
    ready = true; setFamily();
    $("chipSelect").value = "spc5748g"; setTarget();
    const lastImage = state.files.filter(f => f.kind !== "backup").at(-1);
    if (lastImage) await loadFile(lastImage);
    if (state.active) {
      setBusy(true); seenLogs = 0; $("jobDot").className = "ready-dot busy";
      $("statusText").textContent = "Working";
      await follow(await api("/api/jobs/" + state.active)); setBusy(false);
    } else if (state.jobs.length) {
      const latest = state.jobs.at(-1);
      if (latest.plan.mode === mode && latest.plan.target === target.key) {
        if (latest.plan.expected_id) expectedId = hex(latest.plan.expected_id);
        if (latest.plan.action !== "identify") {
          $("addressInput").value = hex(latest.plan.address);
          $("lengthInput").value = hex(latest.plan.length); updateRange();
        }
        await follow(latest);
      }
    }
    controls();
    checkAdapter();
    setInterval(checkAdapter, 3000);
  } catch (error) { message(error); $("statusText").textContent = "Server unavailable"; }
}
initialize();
