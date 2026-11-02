// Synthetic dump editor + review flow. No /api/run or hardware access allowed.
const assert = require('node:assert/strict');
const {spawn, spawnSync} = require('node:child_process');
const {mkdtempSync, mkdirSync, rmSync} = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const puppeteer = require(process.env.PUPPETEER_MODULE || 'puppeteer');
const root=path.resolve(__dirname,'..'), python=process.env.PYTHON || path.join(root,'.venv/bin/python');
const directory=mkdtempSync(path.join(os.tmpdir(),'tigard-editor-'));
const fixture = spawnSync(python,['-c',`
from pathlib import Path
import sys
from tests.test_srecordizer import journal,entry,VIN,keyslot
from spc5tool.editor import record
folder=Path(sys.argv[1])
data=journal({0:[entry(51,VIN.encode()+b'ABCD'),entry(5,keyslot()+b'\\0'*1330)]})[0][1]
(folder/'vcsec.s19').write_bytes(b'\\r\\n'.join(record('3',0x800000+n,data[n:n+32]) for n in range(0,len(data),32))+b'\\r\\n')
data=bytearray(b'\\xff'*0x200000)
for address,text in ((0x18000,b'feature=off\\n'),(0x1C400,b'name=fixture\\n')):
    data[address:address+4+len(text)]=b'\\0\\0'+len(text).to_bytes(2,'big')+text
(folder/'spc5668.bin').write_bytes(data)
`,directory],{cwd:root,encoding:'utf8'});
if(fixture.status!==0)throw new Error(fixture.stderr);
const server=spawn(python,['-m','tests.ui_server','gui','--port','0','--workspace',path.join(directory,'workspace')],{cwd:root});
let browser,page,stage='startup';
async function click(selector){
 await page.$eval(selector,n=>n.scrollIntoView({block:'center'}));
 await page.click(selector);
}
(async()=>{
 try {
  const url=await new Promise((resolve,reject)=>{
   let output='';const timeout=setTimeout(()=>reject(new Error('Server timeout')),10000);
   server.stdout.on('data',data=>{output+=data;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match){clearTimeout(timeout);resolve(match[0]);}});
   server.stderr.on('data',data=>{output+=data;});
   server.on('error',error=>{clearTimeout(timeout);reject(error);});
   server.on('exit',code=>{clearTimeout(timeout);reject(new Error(`Server exited ${code}: ${output}`));});
  });
  browser=await puppeteer.launch({headless:true,executablePath:process.env.CHROME_PATH,args:process.env.CI?['--no-sandbox']:[]});
  page=await browser.newPage();const errors=[];let runRequests=0;
  page.on('pageerror',error=>errors.push(String(error)));
  await page.setRequestInterception(true);
  page.on('request',request=>{
   if(request.url().endsWith('/api/run')){runRequests++;request.abort();}
   else request.continue();
  });
  await page.setViewport({width:1440,height:1100});await page.goto(url,{waitUntil:'networkidle0'});
  await page.select('#chipSelect','spc560b64');
  await (await page.$('#fileInput')).uploadFile(path.join(directory,'vcsec.s19'));
  await page.waitForFunction(()=>document.getElementById('fileName').textContent==='vcsec.s19'&&!document.getElementById('chipSelect').disabled);
  const original=await page.evaluate(()=>({id:file.id,sha:file.sha256}));
  stage='VCSEC analyze';await click('#editorTab');
  await page.waitForFunction(()=>document.getElementById('edSection').value==='vcsec'&&!document.getElementById('edScan').disabled);
  assert.match(await page.$eval('#edName',n=>n.textContent),/0033/);
  assert.equal(await page.$eval('#edVin',n=>n.value),'5YJ3E1EA7KF000001');
  assert.equal(await page.$eval('#edVinTools',n=>n.hidden),false);
  await page.focus('#editorTab');await page.keyboard.press('End');
  assert.equal(await page.$eval('#wiringTab',n=>n.getAttribute('aria-selected')),'true');
  await page.keyboard.press('Home');await page.keyboard.press('ArrowRight');
  assert.equal(await page.$eval('#editorTab',n=>n.getAttribute('aria-selected')),'true');
  stage='VIN edit';await page.$eval('#edVin',n=>{n.value='5YJ3E1EA7KF000002';});await click('#edApplyVin');
  await page.waitForFunction(()=>document.getElementById('edRevision').textContent==='EDITED IMAGE'&&!document.getElementById('edScan').disabled);
  const edited=await page.evaluate(()=>({id:file.id,sha:file.sha256,target:file.editor_target,base:file.editor_base}));
  assert.notEqual(edited.id,original.id);assert.notEqual(edited.sha,original.sha);assert.equal(edited.target,'spc560b64');
  assert.equal(await page.$eval('#edVin',n=>n.value),'5YJ3E1EA7KF000002');
  const structured=await page.evaluate(async()=>{
   const r=await fetch('/api/editor/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:target.key,file_id:file.id,sha256:file.sha256,base:0})});return r.json();
  });
  const vin=structured.vcsec.find(r=>r.parameter_id===51);
  assert.equal(vin.value_hex.slice(-8),'41424344');assert.equal(vin.checksum_valid,true);
  stage='Undo redo';await click('#edUndo');
  await page.waitForFunction(id=>file.id===id&&!busy,{},original.id);
  assert.equal(await page.$eval('#edVin',n=>n.value),'5YJ3E1EA7KF000001');
  await click('#edRedo');await page.waitForFunction(id=>file.id===id&&!busy,{},edited.id);
  stage='Write review';await click('#settingsButton');await page.type('#expectedId','0x1234501D');await click('#settingsDialog button[value="save"]');
  await page.waitForFunction(()=>!document.getElementById('settingsDialog').open&&!busy);
  await click('#edWrite');await page.waitForSelector('#confirmDialog[open]');
  const plan=await page.evaluate(()=>({id:pendingPlan.file_id,sha:pendingPlan.image.sha256,address:pendingPlan.address,length:pendingPlan.length}));
  assert.deepEqual(plan,{id:edited.id,sha:edited.sha,address:0x800000,length:0x10000});
  await click('#confirmDialog button[value="cancel"]');
  await page.waitForFunction(()=>!document.getElementById('confirmDialog').open&&!busy);
  stage='read-only/history and generic byte edit';await page.select('#edSection','bytes');
  await page.$eval('#edAddress',n=>{n.value='0x00800054';});await page.$eval('#edLength',n=>{n.value='4';});await click('#edReadBytes');
  await page.waitForFunction(()=>document.getElementById('edBytesValue').value.length>0&&!busy);
  assert.equal(await page.$eval('#edBytesValue',n=>n.value.replace(/\s/g,'')),vin.stored_checksum.toString(16).toUpperCase().padStart(8,'0'));
  stage='SPC5668 text';await page.select('#chipSelect','spc5668g');
  assert.equal(await page.$eval('#currentPassword',n=>n.disabled),true);
  assert.equal(await page.$eval('[data-operation="install_password"]',n=>n.disabled),true);
  assert.match(await page.$eval('#passwordNote',n=>n.textContent),/serial-boot/);
  assert.equal(await page.$eval('#newPassword',n=>n.disabled),true);
  await (await page.$('#fileInput')).uploadFile(path.join(directory,'spc5668.bin'));
  await page.waitForFunction(()=>document.getElementById('fileName').textContent==='spc5668.bin'&&!busy);
  await click('#editorTab');await page.waitForFunction(()=>document.getElementById('edSummary').textContent.includes('2 SPC56')&&!busy);
  await page.select('#edSection','spc56');assert.equal(await page.$eval('#edName',n=>n.textContent),'firmware.rc');
  await page.$eval('#edValue',n=>{n.value='feature=on\n';});await click('#edApply');
  await page.waitForFunction(()=>document.getElementById('fileName').textContent==='spc5668_edited.bin'&&!busy);
  assert.equal(await page.$eval('#edValue',n=>n.value),'feature=on\n');
  stage='raw search edit';await page.select('#edSection','bytes');
  await page.$eval('#edFind',n=>{n.value='66 65 61 74 75 72 65';});await click('#edSearch');
  await page.waitForFunction(()=>document.getElementById('edHits').textContent.includes('0x00018004')&&!busy);
  await page.$eval('#edLength',n=>{n.value='7';});await click('#edReadBytes');await page.waitForFunction(()=>!busy);
  await page.$eval('#edBytesValue',n=>{n.value='46 45 41 54 55 52 45';});await click('#edApplyBytes');await page.waitForFunction(()=>!busy);
  await page.select('#edSection','spc56');assert.equal(await page.$eval('#edValue',n=>n.value),'FEATURE=on\n');
  stage='layouts';mkdirSync(path.join(root,'test-results/editor'),{recursive:true});
  for(const width of [1440,850,480]){
   await page.setViewport({width,height:1100});await page.$eval('#editorView',n=>n.scrollIntoView({block:'start'}));
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.screenshot({path:path.join(root,'test-results/editor',`editor-${width}.png`),fullPage:width===1440});
  }
  assert.equal(runRequests,0);assert.deepEqual(errors,[]);
  console.log('SRecordizer browser passed: VCSEC0033 VIN/CRC/tail, immutable revisions, Undo/Redo, correct Write image, SPC5668 text/HEX, responsive layouts; no hardware jobs.');
 }catch(error){
  console.error('Editor stage:',stage);
  if(page){
   console.error(await page.evaluate(()=>({busy,section:document.getElementById('edSection').value,readDisabled:document.getElementById('edReadBytes').disabled,message:document.getElementById('edMessage').textContent,toast:document.getElementById('toast').textContent,summary:document.getElementById('edSummary').textContent})));
   mkdirSync(path.join(root,'test-results/editor'),{recursive:true});
   await page.screenshot({path:path.join(root,'test-results/editor/failure.png'),fullPage:true});
  }
  throw error;
 }finally{if(browser)await browser.close();server.kill();rmSync(directory,{recursive:true,force:true});}
})().catch(error=>{console.error(error);process.exitCode=1;});
