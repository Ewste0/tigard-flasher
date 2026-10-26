// Optional browser smoke test; requires Puppeteer and Chrome. Never runs hardware operations.
// PUPPETEER_MODULE=/path/to/puppeteer CHROME_PATH=/path/to/chrome node tests/ui_wiring.cjs
const assert = require("node:assert/strict");
const {spawn} = require("node:child_process");
const {mkdtempSync, mkdirSync, writeFileSync, rmSync} = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const puppeteer = require(process.env.PUPPETEER_MODULE || "puppeteer");
const root = path.resolve(__dirname, "..");
const screenshots = path.resolve(root, "test-results/ui-guide");

async function main() {
  mkdirSync(screenshots, {recursive:true});
  const browser = await puppeteer.launch({headless:true, executablePath:process.env.CHROME_PATH, args:process.env.CI ? ["--no-sandbox"] : []});
  try {
    for (const reset of ["trst", "srst"]) {
      const mode = "hardware";
      const workspace = mkdtempSync(path.join(os.tmpdir(), "tigard-ui-"));
      const server = spawn((process.env.PYTHON || path.join(root, ".venv/bin/python")), ["-m", "tests.ui_server", "gui", "--port", "0", "--workspace", workspace, "--reset-pin", reset,
        ...(reset === "srst" ? ["--freq", "100000"] : [])], {cwd:root});
      let page;
      try {
        const url = await new Promise((resolve, reject) => {
          let output = "";
          const timeout = setTimeout(() => reject(new Error("UI server startup timed out")), 10000);
          server.stdout.on("data", data => {
            output += data;
            const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
            if (match) { clearTimeout(timeout); resolve(match[0]); }
          });
          server.on("error", error => { clearTimeout(timeout); reject(error); });
          server.on("exit", code => { clearTimeout(timeout); reject(new Error(`Server exited: ${code}`)); });
        });
        page = await browser.newPage();
        // The hardware-mode visual case uses a descriptor fixture: no USB access.
        const descriptor = serial => ({serial, description:"Tigard test fixture", interface:2, url:`ftdi://ftdi:2232h:${serial}/2`});
        let usbState = {connected:true, devices:[descriptor("TEST-ONLY")], selected_url:descriptor("TEST-ONLY").url,
          selection:null, revision:1, message:"USB detected · TEST-ONLY"};
        let jobFixture = null, stopRequests = 0;
        await page.setRequestInterception(true);
        page.on("request", request => {
          if (request.url().endsWith("/api/adapter")) {
            if (request.method() === "POST") {
              const selection = JSON.parse(request.postData()).url;
              usbState = {...usbState, selection, selected_url:selection, connected:true,
                revision:usbState.revision + 1, message:`USB detected · ${usbState.devices.find(d => d.url === selection).serial}`};
            }
            request.respond({status:200, contentType:"application/json", body:JSON.stringify(usbState)});
          }
          else if (jobFixture && request.url().endsWith("/api/jobs/ui-stop-test")) {
            request.respond({status:200, contentType:"application/json", body:JSON.stringify(jobFixture)});
          }
          else if (jobFixture && request.url().endsWith("/api/cancel")) {
            assert.equal(JSON.parse(request.postData()).job_id, "ui-stop-test");
            stopRequests++;
            jobFixture.cancel_requested = true;
            request.respond({status:200, contentType:"application/json", body:JSON.stringify(jobFixture)});
          }
          else request.continue();
        });
        const errors = [];
        page.on("pageerror", error => errors.push(String(error)));
        page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
        await page.setViewport({width:1440, height:1200});
        await page.goto(url, {waitUntil:"networkidle0"});
        await page.waitForFunction(() => !document.getElementById("wiringButton").disabled && document.getElementById("chipSelect").value === "spc5748g");
        assert.equal(await page.$eval("#modeBadge", node => node.textContent), "Tigard hardware");
        assert.equal(await page.$eval('[data-operation="authenticate"]', node => node.disabled), false);
        assert.equal(await page.$eval("#currentPassword", node => node.disabled), false);
        assert.match(await page.$eval("#passwordNote", node => node.textContent), /Customer Delivery/);
        const loginPlan = await page.evaluate(async () => {
          const password = "0123456789ABCDEF".repeat(4);
          document.getElementById("currentPassword").value = password;
          const response = await fetch("/api/plan", {method:"POST", headers:{"Content-Type":"application/json"},
            body:JSON.stringify(request("authenticate"))});
          return {status:response.status, body:await response.json()};
        });
        assert.equal(loginPlan.status, 200);
        assert.equal(JSON.stringify(loginPlan.body).includes("0123456789ABCDEF"), false);
        await page.$eval("#currentPassword", node => { node.value = ""; });
        await page.click("#settingsButton");
        assert.equal(await page.$eval("#readModeSelect", node => node.value), "queued");
        assert.equal(await page.evaluate(() => request("read").fast_read), true);
        await page.select("#readModeSelect", "checked");
        await page.click('#settingsDialog button[value="save"]');
        assert.equal(await page.evaluate(() => request("read").fast_read), false);
        await page.click("#settingsButton");
        await page.select("#readModeSelect", "queued");
        await page.$eval("#readDelayUs", node => { node.value = "20"; });
        await page.click('#settingsDialog button[value="save"]');
        const queuedPlan = await page.evaluate(async () => {
          const response = await fetch("/api/plan", {method:"POST", headers:{"Content-Type":"application/json"},
            body:JSON.stringify(request("read"))});
          return {status:response.status, body:await response.json(), erase:request("erase").fast_read};
        });
        assert.equal(queuedPlan.status, 200);
        assert.equal(queuedPlan.body.fast_read, true);
        assert.equal(queuedPlan.body.read_delay_us, 20);
        assert.equal(queuedPlan.erase, true);
        assert.equal(await page.$eval('[data-operation="program"]', node => node.hidden && node.disabled), true);
        assert.equal(await page.$eval("#clockValue", node => node.textContent), reset === "srst" ? "100 kHz" : "5 MHz");
        await page.click("#wiringButton");
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 14);
        assert.equal(await page.$eval("#wiringConfig", node => node.textContent), `Session reset: ${reset.toUpperCase()}`);
        assert.equal(await page.$eval("#wiringRows .wire-reset .pin span", node => node.textContent), reset.toUpperCase());
        assert.equal(await page.$eval(".tigard-photo img", img => img.complete && img.naturalWidth > 0), true);
        await page.click('#targetConnector [data-wire="reset"]');
        assert.match(await page.$eval("#selectedWire", node => node.textContent), /pin 9 · RESET/);
        assert.equal(await page.$$eval('#adapterPins .wire-selected', nodes => nodes.map(node => node.textContent).join()), `${reset === "trst" ? 7 : 8}${reset.toUpperCase()}`);
        if (reset === "trst") {
          await page.screenshot({path:path.join(screenshots,"mpc5748-desktop.png"),fullPage:true});
          await page.setViewport({width:850, height:1100});
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
          await page.screenshot({path:path.join(screenshots,"mpc5748-narrow.png"),fullPage:true});
          await page.setViewport({width:1440, height:1200});
        }
        await page.select("#chipSelect", "spc560b64");
        await page.click("#settingsButton");
        assert.equal(await page.$eval("#readModeSelect", n => n.disabled), true);
        assert.equal(await page.$eval("#readDelayUs", n => n.disabled), true);
        assert.match(await page.$eval("#queuedAccessNote", n => n.textContent), /Not used for SPC560B64L3/);
        await page.click('#settingsDialog button[value="save"]');
        assert.equal(await page.evaluate(() => request("write").fast_read), false);
        assert.equal(await page.$eval("#connectorReference", node => node.hidden), false);
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 10);
        await page.click('#targetConnector [data-wire="reset"]');
        assert.match(await page.$eval("#selectedWire", node => node.textContent), /pin 10 · RESET/);
        if (reset === "trst") await page.screenshot({path:path.join(screenshots,"spc560-10pin.png"),fullPage:true});
        assert.equal(await page.$$eval("#targetConnector .wire-jcomp", nodes => nodes.length), 0);
        await page.select("#chipSelect", "spc58nh92");
        assert.equal(await page.evaluate(() => request("read").fast_read), false);
        assert.equal(await page.$eval("#currentPassword", node => node.disabled), true);
        assert.equal(await page.$eval('[data-operation="authenticate"]', node => node.disabled), true);
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 14);
        assert.equal(await page.$eval("#readScope", node => node.value), "dflash");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00040000");
        assert.equal(await page.$eval('[data-operation="erase"]', node => node.disabled), false);
        await page.select("#readScope", "cflash");
        assert.equal(await page.$eval('[name="region"]:checked', node => node.value), "CFLASH");
        await page.click('[name="region"][value="UTEST"]');
        assert.equal(await page.$eval("#addressInput", node => node.value), "0x00401000");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00001000");
        assert.equal(await page.$eval("#readScope", node => node.value), "utest_user");
        assert.match(await page.$eval("#profileNote", node => node.textContent), /Cut 2.0/);
        assert.match(await page.$eval("#regionNote", node => node.textContent), /0x00401FFF/);
        await page.select("#chipSelect", "spc5748g");
        await page.click("#settingsButton");
        assert.equal(await page.$eval("#readDelayUs", n => n.disabled), false);
        assert.equal(await page.$eval("#readDelayUs", n => n.value), "20");
        await page.click('#settingsDialog button[value="cancel"]');
        assert.equal(await page.$eval("#readScope", node => node.value), "config");
        assert.equal(await page.$eval("#addressInput", node => node.value), "0x00FA8000");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00008000");
        await page.click('[name="region"][value="UTEST"]');
        assert.equal(await page.evaluate(() => request("read").fast_read), false);
        assert.equal(await page.$eval("#readScope", node => node.value), "utest");
        assert.equal(await page.$eval("#addressInput", node => node.value), "0x004000A0");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00003F60");
        assert.equal(await page.$eval('[data-operation="program"]', node => node.hidden), false);
        await page.select("#readScope", "utest_user");
        assert.equal(await page.$eval("#addressInput", node => node.value), "0x00401000");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00003000");
        await page.select("#readScope", "full");
        assert.equal(await page.$eval('[data-operation="program"]', node => node.hidden && node.disabled), true);
        assert.equal(await page.$eval("#addressInput", node => node.value), "0x00F90000");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x005F0000");
        assert.match(await page.$eval("#saveName", node => node.value), /MPC5748G_Full_/);
        await page.select("#readScope", "config");
        const fixture = path.join(workspace, "viewer-fixture.bin");
        writeFileSync(fixture, Buffer.alloc(512, 0x5A));
        await (await page.$("#fileInput")).uploadFile(fixture);
        await page.waitForFunction(() => document.getElementById("fileName").textContent === "viewer-fixture.bin");
        await page.$eval("#saveName", node => { node.value = "Renamed_Config.bin"; });
        await page.click("#saveButton");
        await page.waitForFunction(() => document.getElementById("fileName").textContent === "Renamed_Config.bin");
        assert.equal(await page.$eval("#currentPassword", node => node.maxLength), 64);
        await page.select("#chipSelect", "spc560b64");
        assert.equal(await page.$eval("#currentPassword", node => node.maxLength), 16);
        await page.click('[name="region"][value="CFLASH_OTP"]');
        assert.equal(await page.$eval('[data-operation="erase"]', node => node.disabled), true);
        assert.equal(await page.$eval('[data-operation="write"]', node => node.disabled), true);
        assert.equal(await page.$eval('[data-operation="program"]', node => node.disabled), false);
        assert.equal(await page.$eval('[data-operation="program"]', node => node.hidden), false);
        assert.equal(await page.$eval('[data-operation="program"]', node => node.textContent), "Program once");
        assert.equal(await page.$eval('[data-operation="blank"]', node => node.disabled), false);
        // Review a plan only: no job/USB request is submitted.
        await page.click("#settingsButton");
        await page.type("#expectedId", "0x1234501D");
        await page.click('#settingsDialog button[value="save"]');
        await page.waitForFunction(() => !document.getElementById("settingsDialog").open);
        await page.click('[data-operation="program"]');
        await page.waitForSelector("#confirmDialog[open]");
        assert.match(await page.$eval("#planNote", node => node.textContent), /One-time programming/);
        await page.click('#confirmDialog .dialog-actions button[value="cancel"]');
        if (reset === "trst") await page.screenshot({path:path.join(screenshots,"otp-desktop.png"),fullPage:true});
        await page.click('[name="region"][value="CFLASH"]');
        assert.equal(await page.$eval('[data-operation="program"]', node => node.hidden && node.disabled), true);
        assert.equal(await page.$eval('[data-operation="write"]', node => node.disabled), false);
        assert.equal(await page.$("#demoButton"), null);
        assert.equal(await page.$("#modeSelect"), null);
        await page.select("#familySelect", "spi");
        assert.equal(await page.$eval('[data-operation="program"]', node => node.hidden && node.disabled), true);
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 8);
        assert.equal(await page.$$eval("#targetConnector .pin b", nodes => nodes.map(node => Number(node.textContent)).join()), "1,8,2,7,3,6,4,5");
        if (reset === "trst") await page.screenshot({path:path.join(screenshots,"spi-desktop.png"),fullPage:true});
        // Hot-plug updates connection state without reloading or starting a job.
        await page.evaluate(() => { expectedId = "0x1234501D"; $("currentPassword").value = "TEST-SECRET"; });
        usbState = {...usbState, connected:false, devices:[], selected_url:null, revision:2, message:"Tigard not found on USB"};
        await page.evaluate(() => checkAdapter());
        assert.equal(await page.$eval("#adapterState", node => node.textContent), "Disconnected");
        assert.equal(await page.evaluate(() => expectedId), "");
        assert.equal(await page.$eval("#currentPassword", node => node.value), "");
        usbState = {...usbState, connected:true, devices:[descriptor("NEW-TIGARD")], selected_url:descriptor("NEW-TIGARD").url,
          revision:3, message:"USB detected · NEW-TIGARD"};
        // Wait for the actual idle poll, rather than calling it for reconnect.
        await page.waitForFunction(() => document.getElementById("connectionNote").textContent.includes("NEW-TIGARD"), {timeout:7000});
        await page.click("#settingsButton");
        assert.equal(await page.$eval("#adapterSelect", node => node.options.length), 2);
        usbState = {...usbState, devices:[descriptor("NEW-TIGARD"), descriptor("SECOND-TIGARD")]};
        await page.evaluate(() => checkAdapter());
        await page.waitForFunction(() => document.getElementById("adapterSelect").options.length === 3);
        await page.select("#adapterSelect", descriptor("SECOND-TIGARD").url);
        await page.click('#settingsDialog button[value="save"]');
        await page.waitForFunction(() => !document.getElementById("settingsDialog").open);
        await page.waitForFunction(() => document.getElementById("connectionNote").textContent.includes("SECOND-TIGARD"));
        assert.equal(usbState.selection, descriptor("SECOND-TIGARD").url);
        await page.click("#settingsButton");
        assert.equal(await page.$eval("#adapterSelect", node => node.value), descriptor("SECOND-TIGARD").url);
        if (reset === "srst") await page.screenshot({path:path.join(screenshots,"adapter-settings.png"),fullPage:true});
        await page.click('#settingsDialog button[value="cancel"]');
        const state = await page.evaluate(async () => (await fetch("/api/status")).json());
        assert.equal(state.hardware_enabled, mode === "hardware");
        assert.equal(await page.$eval("#appVersion", n => n.textContent), `LOCAL / ${state.version}`);
        assert.equal(state.jobs.length, 0);
        // Exercise the real polling UI with a running, already-modified Write.
        // Only the HTTP job is a fixture; no /api/run or USB operation occurs.
        jobFixture = {id:"ui-stop-test", plan:{action:"write", mode:"hardware"}, status:"running",
          cancellable:true, modification_started:true, progress:53, phase:"Write · program / program",
          elapsed_seconds:240, phase_seconds:47, logs:[], result:{}};
        await page.evaluate(job => { window.stopTestDone = false; follow(job).then(() => { window.stopTestDone = true; }); }, jobFixture);
        for (const width of [1440, 1024, 850, 480]) {
          await page.setViewport({width,height:900});
          assert.equal(await page.$eval("#stopRead", n => !n.hidden && !n.disabled), true);
          assert.equal(await page.$eval("#stopRead", n => n.textContent), "Stop");
          assert.equal(await page.$eval("#statusDetail", n => getComputedStyle(n).display !== "none" && n.getBoundingClientRect().height > 0), true);
          assert.match(await page.$eval("#statusDetail", n => n.textContent), /program.*47s/);
          assert.equal(await page.$eval("#jobElapsed", n => n.textContent), "4m 00s");
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        }
        if (reset === "srst") await page.screenshot({path:path.join(screenshots,"write-stop-mobile.png"),fullPage:true});
        await page.click("#stopRead");
        await page.waitForFunction(() => document.getElementById("stopRead").textContent === "Stopping…");
        assert.equal(stopRequests, 1);
        assert.equal(await page.$eval("#stopRead", n => n.disabled), true);
        jobFixture = {...jobFixture, status:"cancelled", cancellable:false, finished:Date.now()/1000,
          result:{message:"Stopped at a flash checkpoint. Flash may be partially erased or programmed.",
            backup:{id:"fixture-backup",name:"backup-test.zip"}}};
        await page.waitForFunction(() => window.stopTestDone);
        assert.equal(await page.$eval("#statusText", n => n.textContent), "Stopped");
        assert.equal(await page.$eval("#stopRead", n => n.hidden), true);
        assert.equal(await page.$eval("#jobProgress", n => n.value), 53);
        assert.equal(await page.$eval("#resultActions a", n => n.textContent), "Download backup ZIP");
        assert.deepEqual(errors, []);
        console.log(`Browser wiring checks passed (${reset}, ${mode}): JTAG, SPI, highlighting, images, presets, names, no hardware operations.`);
      } finally {
        if (page) await page.close();
        if (server.exitCode === null && server.signalCode === null) {
          const exited = new Promise(resolve => server.once("exit", resolve));
          server.kill("SIGINT"); await exited;
        }
        rmSync(workspace, {recursive:true, force:true});
      }
    }
  } finally {
    const deadline = setTimeout(() => browser.process()?.kill("SIGKILL"), 5000);
    try { await browser.close(); } finally { clearTimeout(deadline); }
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
