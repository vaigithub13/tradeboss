/** Capture before/after Paper UI fixtures at 1440 and 1024 widths.
 *  Run: node docs/screenshots/paper-redesign/capture.mjs
 */
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = path.join(dir, "fixtures.html");

const shots = [
  { view: "before-panel", name: "before-panel", widths: [1440, 1024] },
  { view: "after-panel", name: "after-panel", widths: [1440, 1024] },
  { view: "after-dash", name: "after-dashboard", widths: [1440, 1024] },
];

const browser = await chromium.launch();
for (const shot of shots) {
  for (const width of shot.widths) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    await page.goto(`file://${fixture}?view=${shot.view}`);
    await page.waitForTimeout(100);
    const out = path.join(dir, `${shot.name}-${width}.png`);
    await page.screenshot({ path: out, fullPage: false });
    console.log("wrote", out);
    await page.close();
  }
}
await browser.close();
