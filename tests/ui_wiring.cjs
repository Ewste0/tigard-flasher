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
      const server = spawn((process.env.PYTHON || path.join(root, ".venv/bin/python")), ["-m", "spc5tool", "gui", "--port", "0", "--workspace", workspace, "--reset-pin", reset,
        ...(mode === "hardware" ? ["--allow-hardware", "--mode", "hardware", "--freq", "100000"] : [])], {cwd:root});
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
        await page.setRequestInterception(true);
        page.on("request", request => {
          if (request.url().endsWith("/api/adapter")) request.respond({status:200, contentType:"application/json", body:JSON.stringify({connected:true, devices:[{serial:"TEST-ONLY"}]})});
          else request.continue();
        });
        const errors = [];
        page.on("pageerror", error => errors.push(String(error)));
        page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
        await page.setViewport({width:1440, height:1200});
        await page.goto(url, {waitUntil:"networkidle0"});
        await page.waitForFunction(() => !document.getElementById("wiringButton").disabled && document.getElementById("chipSelect").value === "spc5748g");
        assert.equal(await page.$eval("#modeBadge", node => node.textContent), "Tigard hardware");
        assert.equal(await page.$eval("#clockValue", node => node.textContent), mode === "hardware" ? "100 kHz" : "500 kHz");
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
        assert.equal(await page.$eval("#connectorReference", node => node.hidden), false);
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 10);
        await page.click('#targetConnector [data-wire="reset"]');
        assert.match(await page.$eval("#selectedWire", node => node.textContent), /pin 10 · RESET/);
        if (reset === "trst") await page.screenshot({path:path.join(screenshots,"spc560-10pin.png"),fullPage:true});
        assert.equal(await page.$$eval("#targetConnector .wire-jcomp", nodes => nodes.length), 0);
        await page.select("#chipSelect", "spc58nh92");
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 14);
        assert.equal(await page.$eval("#readScope", node => node.value), "dflash");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00040000");
        assert.equal(await page.$eval('[data-operation="erase"]', node => node.disabled), true);
        await page.select("#chipSelect", "spc5748g");
        assert.equal(await page.$eval("#readScope", node => node.value), "config");
        assert.equal(await page.$eval("#addressInput", node => node.value), "0x00FA8000");
        assert.equal(await page.$eval("#lengthInput", node => node.value), "0x00008000");
        await page.select("#readScope", "full");
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
        assert.equal(await page.$("#demoButton"), null);
        assert.equal(await page.$("#modeSelect"), null);
        await page.select("#familySelect", "spi");
        assert.equal(await page.$eval("#targetConnector", node => node.children.length), 8);
        assert.equal(await page.$$eval("#targetConnector .pin b", nodes => nodes.map(node => Number(node.textContent)).join()), "1,8,2,7,3,6,4,5");
        if (reset === "trst") await page.screenshot({path:path.join(screenshots,"spi-desktop.png"),fullPage:true});
        const state = await page.evaluate(async () => (await fetch("/api/status")).json());
        assert.equal(state.hardware_enabled, mode === "hardware");
        assert.equal(state.jobs.length, 0);
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
