// Offline browser check: continuous scrolling, final partial row, keyboard,
// offset jump and bounded DOM for the maximum supported dump size. No USB.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {mkdtempSync, mkdirSync, writeFileSync, rmSync} = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const puppeteer = require(process.env.PUPPETEER_MODULE || 'puppeteer');
const root = path.resolve(__dirname, '..');

(async () => {
 const directory = mkdtempSync(path.join(os.tmpdir(), 'tigard-scroll-'));
 const data = Buffer.alloc(16 * 1024 * 1024 - 3);
 for (let i=0; i<data.length; i++) data[i] = (i * 37 + (i >>> 8)) & 255;
 Buffer.from('SCROLL_START_123').copy(data);
 Buffer.from('SCROLL_END_12').copy(data, data.length - 13);
 const fixture = path.join(directory, 'scroll-fixture.bin');
 writeFileSync(fixture, data);
 const server = spawn((process.env.PYTHON || path.join(root,'.venv/bin/python')), ['-m','spc5tool','gui','--port','0','--workspace',path.join(directory,'workspace')], {cwd:root});
 let browser, page, stage="startup";
 try {
  const url = await new Promise((resolve,reject) => {
   let output=''; const timeout=setTimeout(()=>reject(new Error('Server startup timed out')),10000);
   server.stdout.on('data', chunk=>{ output+=chunk; const match=output.match(/http:\/\/127\.0\.0\.1:\d+/); if(match){clearTimeout(timeout);resolve(match[0]);} });
   server.on('error',reject);
  });
  browser = await puppeteer.launch({headless:true,executablePath:process.env.CHROME_PATH, args:process.env.CI ? ["--no-sandbox"] : []});
  page = await browser.newPage(); const errors=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.setRequestInterception(true);
  page.on('request', request => {
   if(request.url().endsWith('/api/adapter')) request.respond({status:200,contentType:'application/json',body:JSON.stringify({connected:false,devices:[],message:'Disconnected (UI test fixture)'})});
   else request.continue();
  });
  await page.setViewport({width:1440,height:1100});
  await page.goto(url,{waitUntil:'networkidle0'});
  stage='upload'; await (await page.$('#fileInput')).uploadFile(fixture);
  await page.waitForFunction(()=>document.querySelector('#hexRows .hex-ascii')?.textContent === 'SCROLL_START_123');
  assert.equal(await page.$('#nextPage'),null);
  assert(await page.$$eval('#hexRows .hex-row',rows=>rows.length)<80);
  stage='middle'; await page.$eval('#hexScroll',node=>{node.scrollTop=node.scrollHeight/2;});
  const middle = await page.$eval('#hexScroll',node=>Math.floor(node.scrollTop/22)*16);
  await page.waitForSelector(`.hex-row[data-offset="${middle}"]`);
  const expected = data.subarray(middle,middle+16).toString('hex');
  assert.equal(await page.$eval(`.hex-row[data-offset="${middle}"] .hex-bytes`,node=>node.textContent.replace(/\s/g,'')),expected.toUpperCase());
  stage='end'; await page.focus('#hexScroll');
  await page.keyboard.press('End');
  await page.waitForFunction(()=>document.querySelector('#hexRows .hex-row:last-child .hex-ascii')?.textContent === 'SCROLL_END_12');
  assert.equal(await page.$eval('#hexRows .hex-row:last-child .hex-offset',node=>node.textContent),'00FFFFF0');
  assert(await page.$$eval('#hexRows .hex-row',rows=>rows.length)<80);
  stage='jump'; await page.type('#jumpOffset','0x12340');
  await page.click('#jumpButton');
  await page.waitForFunction(()=>document.getElementById('bufferPosition').textContent.startsWith('0x00012340'));
  stage='home'; await page.keyboard.press('Home');
  await page.waitForFunction(()=>document.getElementById('bufferPosition').textContent.startsWith('0x00000000'));
  stage='wheel'; await page.$eval('#hexScroll',node=>node.scrollIntoView({block:'center'}));
  const box = await page.$eval('#hexScroll',node=>{const r=node.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};});
  await page.evaluate(()=>{window.wheelEvents=[];document.addEventListener('wheel',event=>window.wheelEvents.push({target:event.target.id||event.target.className,delta:event.deltaY,prevented:event.defaultPrevented}),{capture:true,passive:true});});
  await page.mouse.move(box.x,box.y); await page.mouse.wheel({deltaY:660});
  await page.waitForFunction(()=>document.getElementById('hexScroll').scrollTop > 0);
  assert(await page.$$eval('#hexRows .hex-row',rows=>rows.length)<80);
  await page.setViewport({width:850,height:1100});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),true);
  stage='file switch';
  const small = path.join(directory,'viewer-test-pattern.bin');
  const pattern = Buffer.alloc(512,0xFF);
  Buffer.from('TEST DATA ONLY - NOT FIRMWARE').copy(pattern);
  writeFileSync(small,pattern);
  await (await page.$('#fileInput')).uploadFile(small);
  await page.waitForFunction(()=>document.getElementById('fileName').textContent === 'viewer-test-pattern.bin' && document.getElementById('bufferPosition').textContent.includes('512 bytes'));
  assert.equal(await page.$eval('#hexScroll',node=>node.scrollTop),0);
  assert.match(await page.$eval('#hexRows .hex-ascii',node=>node.textContent),/TEST DATA ONLY/);
  assert.equal(await page.$('#demoButton'),null);
  assert.equal(await page.$('#modeSelect'),null);
  stage='S-record segments';
  await page.select('#chipSelect','spc560b64');
  function srecord(address, bytes) {
   const body=Buffer.alloc(5+bytes.length); body[0]=4+bytes.length;
   body.writeUIntBE(address,1,3); bytes.copy(body,4);
   body[body.length-1]=(~body.subarray(0,-1).reduce((a,b)=>a+b,0))&255;
   return 'S2'+body.toString('hex').toUpperCase()+'\r\n';
  }
  const s19=path.join(directory,'segments.S19');
  const firstSegment=Buffer.alloc(512,0x41);
  const secondSegment=Buffer.from('SECOND_SEGMENT!!');
  let records='';
  for(let offset=0;offset<firstSegment.length;offset+=16) records+=srecord(0x800000+offset,firstSegment.subarray(offset,offset+16));
  records+=srecord(0xC00000,secondSegment);
  writeFileSync(s19,records);
  await (await page.$('#fileInput')).uploadFile(s19);
  await page.waitForFunction(()=>document.getElementById('fileTag').textContent==='S-RECORD' && document.querySelector('.hex-offset')?.textContent==='00800000');
  assert.equal(await page.$eval('#addressInput',node=>node.value),'0x00800000');
  assert.equal(await page.$eval('#lengthInput',node=>node.value),'0x00000200');
  assert.equal(await page.$eval('input[name="region"]:checked',node=>node.value),'DFLASH');
  await page.type('#jumpOffset','0x800080'); await page.click('#jumpButton');
  await page.waitForFunction(()=>document.getElementById('bufferPosition').textContent.startsWith('0x00800080'));
  await page.waitForFunction(()=>!document.getElementById('imageSegment').disabled);
  await page.select('#imageSegment','1');
  await page.waitForFunction(()=>document.querySelector('.hex-offset')?.textContent==='00C00000');
  assert.equal(await page.$eval('.hex-ascii',node=>node.textContent),'SECOND_SEGMENT!!');
  assert.equal(await page.$eval('#addressInput',node=>node.value),'0x00C00000');
  assert.equal(await page.$eval('input[name="region"]:checked',node=>node.value),'DFLASH_OTP');
  assert.equal(await page.$eval('[data-operation="program"]',node=>node.hidden),false);
  assert.equal(await page.$eval('[data-operation="write"]',node=>node.disabled),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),true);
  assert.deepEqual(errors,[]);
  await page.setViewport({width:1440,height:1050});
  await page.evaluate(() => window.scrollTo(0,0));
  mkdirSync(path.join(root,'test-results'),{recursive:true});
  await page.screenshot({path:path.join(root,'test-results/ui-live.png'),fullPage:true});
  console.log('Scroll viewer passed: 16 MiB binary, exact bytes, keyboard/wheel/jump, S-record addresses/segments, OTP selection and resize.');
 } catch(error) {
  console.error('Failed stage:', stage);
  if(page) console.error(await page.evaluate(()=>({wheelEvents:window.wheelEvents,file:document.getElementById('fileName').textContent,toast:document.getElementById('toast').textContent,first:document.querySelector('.hex-ascii')?.textContent,last:document.querySelector('.hex-row:last-child .hex-ascii')?.textContent,scroll:document.getElementById('hexScroll').scrollTop,position:document.getElementById('bufferPosition').textContent})));
  throw error;
 } finally {
  if(browser) {
   const deadline=setTimeout(()=>browser.process()?.kill('SIGKILL'),5000);
   try { await browser.close(); } finally { clearTimeout(deadline); }
  }
  if(server.exitCode === null && server.signalCode === null) {
   const exited=new Promise(resolve=>server.once('exit',resolve)); server.kill('SIGINT'); await exited;
  }
  rmSync(directory,{recursive:true,force:true});
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
