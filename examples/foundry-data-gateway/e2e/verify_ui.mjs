// UI checks for slice v1: drive the console in Chromium by data-testid, assert what the
// user sees, and save screenshots (desktop 1440 and mobile 390, DPR 2).
//
//   node e2e/verify_ui.mjs <evidence-dir>
//
// Writes <dir>/ui/*.png and <dir>/ui-checks.json; exit 1 if any check fails.
import { createRequire } from "node:module";
import fs from "node:fs";
const { chromium } = createRequire(import.meta.url)("playwright");
import path from "node:path";

const out = process.argv[2];
const dir = path.join(out, "ui");
fs.mkdirSync(dir, { recursive: true });
const BASE = "http://127.0.0.1:18300";
const checks = [];
const check = (name, pass, detail = "") => {
  checks.push({ check: name, pass: !!pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"} ${name}${pass ? "" : " -- " + JSON.stringify(detail)}`);
};

const flows = [
  { persona: "shepherd-acme-operator", tool: "failed_runs", expectTools: 4, expectText: /results from failed_runs/ },
  { persona: "shepherd-globex-finance", tool: "model_usage", expectTools: 2, expectText: /results from model_usage/ },
  { persona: "alphazed-parent-amal", tool: "needs_attention", expectTools: 4, expectText: /results from needs_attention/,
    family: ["Yousef", "Lina", "Omar"], forbidCells: ["Sara", "Adam", "Maryam"] },
  { persona: "alphazed-parent-free", tool: "next_achievements", expectTools: 2, expectText: /results from next_achievements/,
    family: ["Sara"], forbidCells: ["Yousef", "Adam"] },
];

const browser = await chromium.launch();
try {
  for (const f of flows) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
    await page.goto(`${BASE}/?persona=${f.persona}`);
    await page.waitForSelector("body[data-ready=true]");
    const tools = await page.locator("[data-testid^=tool-]").count();
    check(`ui: ${f.persona} lists ${f.expectTools} granted tools`, tools === f.expectTools, tools);
    await page.locator(`[data-testid=example-${f.tool}]`).first().click();
    await page.locator("[data-testid=chat-send]").click();
    await page.waitForSelector("[data-testid=msg-agent], [data-testid=msg-error]", { timeout: 30000 });
    const reply = (await page.locator("[data-testid=msg-agent]").first().textContent()) || "";
    check(`ui: ${f.persona} gets an answer from ${f.tool}`, f.expectText.test(reply), reply.slice(0, 160));
    const cells = await page.locator("[data-testid=data-card] td").allTextContents();
    check(`ui: ${f.persona} sees a data table with rows`, cells.length > 0, cells.length);
    if (f.family) check(`ui: ${f.persona} table shows this family's children`, f.family.some((n) => cells.includes(n)), cells.slice(0, 8));
    for (const name of f.forbidCells || []) check(`ui: ${f.persona} table never shows ${name}`, !cells.some((c) => c.includes(name)));
    await page.waitForSelector("[data-testid=receipts-table]");
    const usage = await page.locator(`[data-testid=usage-${f.tool}]`).count();
    check(`ui: ${f.persona} audit panel shows usage for ${f.tool}`, usage === 1, usage);
    await page.screenshot({ path: path.join(dir, `${f.persona}-desktop.png`), fullPage: true });
    await page.close();
  }

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await mobile.goto(`${BASE}/?persona=alphazed-parent-amal`);
  await mobile.waitForSelector("body[data-ready=true]");
  await mobile.locator("[data-testid=example-list_children]").first().click();
  await mobile.locator("[data-testid=chat-send]").click();
  await mobile.waitForSelector("[data-testid=msg-agent]", { timeout: 30000 });
  const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  const wide = await mobile.evaluate(() => [...document.querySelectorAll("body *")]
    .filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1)
    .slice(0, 6).map((e) => `${e.tagName}.${e.className}[${e.dataset.testid || ""}] ${Math.round(e.getBoundingClientRect().right)}`));
  check("ui: mobile 390px has no horizontal page scroll", overflow <= 1, { overflow, wide });
  await mobile.screenshot({ path: path.join(dir, "alphazed-parent-amal-mobile.png"), fullPage: true });
  await mobile.close();
} finally {
  await browser.close();
}
fs.writeFileSync(path.join(out, "ui-checks.json"), JSON.stringify(checks, null, 2));
const failed = checks.filter((c) => !c.pass).length;
console.log(`UI: ${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
