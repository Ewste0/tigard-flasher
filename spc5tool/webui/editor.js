"use strict";
// The editor only creates local image revisions. Hardware writes still use
// the shared review/IDCODE/job flow in app.js.
window.SRecordizer = (() => {
  let parsed = null, selected = null, context = "", internal = false;
  let undo = [], redo = [], valueFormat = "hex", bytesFormat = "hex";
  const supported = () => Boolean(target?.editor);
  const base = () => file?.format === "srec" ? 0 : number($("addressInput").value);
  const identity = () => `${target?.key}:${file?.id}:${base()}`;
  const payload = () => ({target:target.key, file_id:file.id, sha256:file.sha256, base:base()});
  const encode = bytes => Array.from(bytes, b => b.toString(16).padStart(2,"0")).join("").toUpperCase();
  const decode = text => {
    const clean = text.replace(/\s/g, "");
    if (!clean || clean.length % 2 || /[^\da-f]/i.test(clean)) throw new Error("Enter complete HEX byte pairs.");
    return Uint8Array.from(clean.match(/../g), pair => parseInt(pair,16));
  };
  function fromInput(text, format) {
    if (format === "hex") return encode(decode(text));
    if (!text || [...text].some(c => c.codePointAt(0) > 127)) throw new Error("ASCII input must contain only characters 0–127.");
    return encode(Uint8Array.from(text, c => c.charCodeAt(0)));
  }
  function toASCII(text) {
    const data = decode(text);
    if (data.some(b => b > 127 || b < 32 && ![9,10,13].includes(b))) throw new Error("This value contains binary bytes; use HEX to preserve them.");
    return Array.from(data, b => String.fromCharCode(b)).join("");
  }
  function notify(text) { $("edMessage").textContent = text; }
  async function work(fn) {
    if (busy) return;
    setBusy(true);
    try { await fn(); }
    catch (error) { notify(error.message); message(error); }
    finally { setBusy(false); }
  }
  function sync() {
    let next;
    try { next = identity(); } catch { next = "invalid address"; }
    if (next !== context) {
      context = next; parsed = null; selected = null;
      if (!internal) { undo = []; redo = []; }
      $("edRows").replaceChildren(); $("edInspector").hidden = true; $("edNoSelection").hidden = false;
      $("edEmpty").hidden = false;
      $("edSummary").textContent = file ? `${file.name} · ${sizeText(file.size)} · scan to inspect` : "Open an image or read a dump to inspect its contents.";
      $("edNotes").textContent = "";
      $("edRevision").textContent = file?.editor_changes?.length ? "EDITED IMAGE" : "ORIGINAL";
    }
    const enabled = ready && supported() && Boolean(file) && !busy;
    $("edRows").setAttribute("aria-busy", String(busy));
    for (const button of $("edRows").querySelectorAll("button")) button.disabled = !enabled;
    $("edSegmentRow").hidden = !file?.segments;
    if ($("edSegment").dataset.file !== file?.id) {
      $("edSegment").replaceChildren(...(file?.segments || []).map((s,i)=>new Option(`${i+1} · ${hex(s.address)} · ${sizeText(s.size)}`,String(i))));
      $("edSegment").dataset.file = file?.id || "";
    }
    $("edSegment").value = String(imageSegment);
    for (const id of ["edScan","edExportSrec","edExportBin","edReadBytes","edApplyBytes","edSearch","edReplaceAll"]) $(id).disabled = !enabled;
    $("edWrite").disabled = !enabled || !document.querySelector('[data-operation="write"]') || region?.writable === false || region?.kind === "otp";
    $("edUndo").disabled = !enabled || !undo.length; $("edRedo").disabled = !enabled || !redo.length;
    $("edCatalog").disabled = busy || !supported();
    for (const id of ["edApply","edApplyVin","edSetKey","edClearKey","edCertificateImport"]) $(id).disabled = !enabled || !selected?.editable;
    $("edCertificateExport").disabled = !enabled || !selected?.is_certificate;
    for (const node of $("editorView").querySelectorAll("input:not([type=file]), textarea, select")) node.disabled = busy || !supported();
    $("edValue").readOnly = !selected?.editable;
  }
  const records = () => parsed?.[$("edSection").value] || [];
  function selectRecord(row) {
    if (busy || row.id === selected?.id) return;
    selected = row;
    // Keep the row buttons in place so keyboard focus survives selection.
    for (const tr of $("edRows").children) {
      const active = tr.dataset.recordId === row.id;
      tr.classList.toggle("selected", active);
      tr.querySelector("button").setAttribute("aria-pressed", String(active));
    }
    inspect(); sync();
  }
  function draw() {
    const section = $("edSection").value, raw = section === "bytes";
    $("edStructured").hidden = raw; $("edBytes").hidden = !raw;
    $("edCurrent").closest("label").hidden = section !== "vcsec";
    $("edForceTesla").closest("label").hidden = section !== "tesla";
    $("edFilter").closest("label").hidden = raw;
    if (raw) { if (!$("edAddress").value && file) $("edAddress").value = hex(file.segments?.[imageSegment]?.address ?? base()); sync(); return; }
    const query = $("edFilter").value.toLowerCase();
    const list = records().filter(row =>
      (section !== "vcsec" || !$("edCurrent").checked || row.state === "current") &&
      (!query || [row.name,row.detail,row.value_hex,row.text,row.parameter_id,hex(row.address)].join(" ").toLowerCase().includes(query)));
    if (!list.some(row => row.id === selected?.id)) selected = list[0] || null;
    $("edRows").replaceChildren(...list.slice(0,500).map(row => {
      const tr = document.createElement("tr"); tr.classList.toggle("selected", row.id === selected?.id);
      tr.dataset.recordId = row.id;
      tr.onclick = () => selectRecord(row);
      const address = document.createElement("td"), name = document.createElement("td"), state = document.createElement("td");
      const button = document.createElement("button"); button.type = "button"; button.textContent = hex(row.address);
      button.setAttribute("aria-label", `${row.name} at ${hex(row.address)}`);
      button.setAttribute("aria-controls", "edInspector");
      button.setAttribute("aria-pressed", String(row.id === selected?.id));
      address.append(button);
      if (row.parameter_id !== undefined) { const id = document.createElement("small"); id.textContent = `ID ${row.parameter_id.toString(16).toUpperCase().padStart(4,"0")}`; address.append(id); }
      name.textContent = row.name; state.textContent = row.state + (row.checksum_valid === false ? " · CRC error" : "");
      tr.append(address,name,state); return tr;
    }));
    $("edEmpty").hidden = Boolean(list.length);
    $("edEmpty").textContent = parsed ? "No matching records. Use HEX / ASCII for other data." : "Scan the image to inspect its structures.";
    $("edNotes").textContent = [...(parsed?.notes || []), ...(section === "tesla" && parsed?.tesla_hint ? [parsed.tesla_hint] : []),
      ...(list.length > 500 ? [`Showing 500 of ${list.length} records. Narrow the filter.`] : [])].join(" ");
    inspect(); sync();
  }
  function inspect() {
    $("edInspector").hidden = !selected; $("edNoSelection").hidden = Boolean(selected);
    if (!selected) return;
    const row = selected, text = row.family === "spc56";
    $("edName").textContent = row.name; $("edDetail").textContent = row.detail || (row.warnings || []).join(" ");
    const facts = [["Address",hex(row.address)],["Length",`${row.byte_count ?? (row.value_hex?.length || 0) / 2} bytes`],["State",row.state]];
    if (row.stored_checksum !== undefined) facts.push(["Checksum",`${hex(row.stored_checksum)} · ${row.checksum_valid ? "valid" : "mismatch"}`]);
    if (text) facts.push(["Reserved through",hex(row.zone_end)],["Trailing FF",`${row.padding_byte_count} bytes`]);
    if (row.vin_trailing_bytes) facts.push(["VIN field",`17 bytes + ${row.vin_trailing_bytes} preserved bytes`]);
    $("edFacts").replaceChildren(...facts.map(([key,value]) => { const div=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd"); dt.textContent=key;dd.textContent=value;div.append(dt,dd);return div; }));
    $("edValueFormat").hidden = text; $("edValueFormat").previousElementSibling.hidden = text;
    valueFormat = "hex"; $("edValueFormat").value = "hex";
    $("edValue").value = text ? row.text : (row.value_hex?.match(/.{1,2}/g) || []).join(" ");
    $("edValueHint").textContent = text ? "Text may grow within its reserved area; existing FF padding is preserved." : row.editable ? "The byte length stays fixed. The supported record checksum is recalculated." : "Historical, corrupt or unsupported records are read-only.";
    $("edVinTools").hidden = !row.is_vin;
    $("edVin").value = row.vin || row.certificate?.vin || (row.is_vin ? row.ascii : "") || "";
    $("edKeyTools").hidden = !row.is_key_container;
    if (row.is_key_container) {
      $("edKeySlot").replaceChildren(...row.key_slots.map(slot => new Option(`${slot.slot + 1} · ${slot.status}`,String(slot.slot))));
      showKey();
    }
    $("edCertificateTools").hidden = !row.is_certificate;
    $("edCertificateInfo").textContent = row.certificate?.summary || "";
  }
  async function scan(keepSelection = null) {
    if (!supported() || !file) return;
    const request = {...payload(), force_tesla:$("edForceTesla").checked}, key = identity();
    notify("Scanning image…");
    const result = await api("/api/editor/analyze", request);
    if (identity() !== key) return;
    parsed = result;
    if (keepSelection) selected = [result.tesla,result.vcsec,result.spc56].flat().find(r=>r.id===keepSelection) || null;
    $("edSummary").textContent = `${file.name} · ${result.document.segments.length} segment(s) · ${result.tesla.length} Tesla / ${result.vcsec.length} VCSEC / ${result.spc56.length} SPC56`;
    if (!records().length && $("edSection").value !== "bytes") $("edSection").value = ["spc56","vcsec","tesla"].find(k=>result[k].length) || "bytes";
    notify(file.editor_changes?.length ? "Edited image loaded. Save it or review Write for the selected segment." : "Image parsed. Edits stay local until you review and run Write.");
    draw();
  }
  async function change(action) {
    const before = {file, segment:imageSegment}, recordId = selected?.id;
    const result = await api("/api/editor/apply", {...payload(), change:action});
    if (result.file.id === file.id) { notify(result.message); return; }
    undo.push(before); if (undo.length > 30) undo.shift(); redo = [];
    internal = true;
    try { await loadFile(result.file, Math.min(imageSegment,(result.file.segments?.length || 1)-1)); }
    finally { internal = false; }
    await scan(recordId); tab("editor"); notify(result.message);
    log(`Editor: ${result.changed_bytes.toLocaleString()} bytes changed; loaded ${result.file.name}. No hardware operation started.`);
  }
  function structured(operation, fields) {
    if (!selected) throw new Error("Select a record first.");
    return {operation, record_id:selected.id, expected_snapshot:selected.snapshot, ...fields};
  }
  async function navigate(from, to) {
    if (!from.length) return;
    const next = from.pop(); to.push({file,segment:imageSegment});
    internal = true;
    try { await loadFile(next.file,next.segment); } finally { internal = false; }
    await scan(); tab("editor");
  }
  function download(url, name) { const a=document.createElement("a");a.href=url;a.download=name;a.click(); }
  function downloadBytes(data, name) { const url=URL.createObjectURL(new Blob([data]));download(url,name);setTimeout(()=>URL.revokeObjectURL(url),1000); }
  async function exportImage(format) {
    const result = await api("/api/editor/export", {...payload(),format,segment:imageSegment});
    download(`/api/download/${result.file.id}`,result.file.name);
    notify(`Exported ${result.file.name}${format === "bin" && file.segments?.length > 1 ? " · selected segment only, no gap filling" : ""}.`);
  }
  function showKey() { const slot=selected?.key_slots?.[Number($("edKeySlot").value)]; $("edKey").value=slot?.key_hex || ""; }
  $("edScan").onclick = () => work(()=>scan());
  $("edUndo").onclick = () => work(()=>navigate(undo,redo));
  $("edRedo").onclick = () => work(()=>navigate(redo,undo));
  $("edExportSrec").onclick = () => work(()=>exportImage("srec"));
  $("edExportBin").onclick = () => work(()=>exportImage("bin"));
  $("edWrite").onclick = () => operation("write");
  $("edSection").onchange = () => { selected=null; draw(); };
  $("edSegment").onchange = () => work(async()=>{
    const index=Number($("edSegment").value);internal=true;
    try{await loadFile(file,index);}finally{internal=false;}
    tab("editor");draw();notify("Write and BIN export use this selected segment. Other segments stay in the S-record image.");
  });
  $("edFilter").oninput = draw; $("edCurrent").onchange = draw;
  $("edForceTesla").onchange = () => work(()=>scan());
  $("edApply").onclick = () => work(()=>change(selected.family === "spc56"
    ? structured("spc56",{text:$("edValue").value,normalization:"preserve",padding_policy:"preserve"})
    : structured("parameter",{value_hex:fromInput($("edValue").value,valueFormat)})));
  $("edApplyVin").onclick = () => work(()=>change(structured("vin",{vin:$("edVin").value.toUpperCase()})));
  $("edKeySlot").onchange = showKey;
  $("edSetKey").onclick = () => work(()=>change(structured("key",{slot:Number($("edKeySlot").value),key_hex:fromInput($("edKey").value,"hex")})));
  $("edClearKey").onclick = () => work(()=>change(structured("key",{slot:Number($("edKeySlot").value),key_hex:null})));
  $("edValueFormat").onchange = () => {
    try { const value=fromInput($("edValue").value,valueFormat); $("edValue").value=$("edValueFormat").value === "ascii" ? toASCII(value) : value.match(/../g).join(" ");valueFormat=$("edValueFormat").value; }
    catch(error) { $("edValueFormat").value=valueFormat; message(error); }
  };
  $("edBytesFormat").onchange = () => {
    try { if ($("edBytesValue").value) { const value=fromInput($("edBytesValue").value,bytesFormat);$("edBytesValue").value=$("edBytesFormat").value === "ascii" ? toASCII(value) : value.match(/../g).join(" "); } bytesFormat=$("edBytesFormat").value; }
    catch(error) { $("edBytesFormat").value=bytesFormat; message(error); }
  };
  $("edReadBytes").onclick = () => work(async()=>{
    const result=await api("/api/editor/read",{...payload(),address:number($("edAddress").value),length:Number($("edLength").value)});
    bytesFormat="hex";$("edBytesFormat").value="hex";$("edBytesValue").value=result.value_hex.match(/../g).join(" ");notify("Bytes loaded from the image. No device access.");
  });
  $("edApplyBytes").onclick = () => work(()=>change({operation:"bytes",address:number($("edAddress").value),value_hex:fromInput($("edBytesValue").value,bytesFormat)}));
  $("edSearch").onclick = () => work(async()=>{
    const result=await api("/api/editor/search",{...payload(),find_hex:fromInput($("edFind").value,bytesFormat)});
    $("edHits").textContent=`${result.addresses.length}${result.truncated?"+":""} matches: ${result.addresses.map(hex).join(", ")}`;
    if(result.addresses.length) $("edAddress").value=hex(result.addresses[0]);
  });
  $("edReplaceAll").onclick = () => work(()=>change({operation:"replace",find_hex:fromInput($("edFind").value,bytesFormat),value_hex:fromInput($("edReplace").value,bytesFormat)}));
  $("edCatalog").onclick = () => $("edCatalogInput").click();
  $("edCatalogInput").onchange = () => work(async()=>{
    const input=$("edCatalogInput").files[0];if(!input)return;
    if(input.size>1024*1024)throw new Error("Catalog must be at most 1 MiB.");
    await api("/api/editor/catalog",{catalog:JSON.parse(await input.text())});$("edCatalogInput").value="";
    if(file)await scan();notify("Parameter names imported for this local workspace.");
  });
  $("edCertificateExport").onclick = () => downloadBytes(decode(selected.value_hex),"certificate-blob.bin");
  $("edCertificateImport").onclick = () => $("edCertificateInput").click();
  $("edCertificateInput").onchange = () => work(async()=>{
    const input=$("edCertificateInput").files[0];if(!input)return;
    if(input.size!==1002)throw new Error("This record requires the complete 1002-byte certificate blob.");
    await change(structured("certificate",{value_hex:encode(new Uint8Array(await input.arrayBuffer()))}));$("edCertificateInput").value="";
  });
  return {sync, open:()=>{sync();if(supported()&&file&&!parsed&&!busy)work(()=>scan());}};
})();
