// README screenshots and demo GIF. Run by scripts/readme_shots.py, which serves
// the demo library and sets SHOTS_URL, SHOTS_TOKEN and SHOTS_OUT.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "@playwright/test";

const BASE = process.env.SHOTS_URL;
const TOKEN = process.env.SHOTS_TOKEN;
const OUT = process.env.SHOTS_OUT;
const ONLY = process.env.SHOTS_ONLY?.split(",");

const HERO = ["Crazy Glue", "Dunes at Night", "Seaside Town"];
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };
const settle = (page, ms = 1400) => page.waitForTimeout(ms); // the wash crossfades over 0.9 s

async function open(page, path = "/") {
  await page.goto(`${BASE}${path}?t=${TOKEN}`);
  await page.getByRole("navigation", { name: "Sections" }).waitFor();
  // The smart-features offer is a banner every fresh install shows; not the subject here.
  const notNow = page.getByRole("button", { name: "Not now" });
  await notNow.waitFor({ timeout: 3000 }).then(() => notNow.click(), () => {});
}

async function play(page, title, at = 0) {
  await open(page, "/library");
  await page.getByRole("button", { name: "Songs" }).click();
  await page.getByRole("button", { name: new RegExp(`^${title}`) }).first().click();
  await page.locator(".player-title", { hasText: title }).first().waitFor();
  await page.evaluate((t) => {
    for (const a of document.querySelectorAll("audio")) {
      if (!a.paused) { a.pause(); a.currentTime = t; }
    }
  }, at);
}

async function nowPlaying(page, panel) {
  await page.getByRole("button", { name: /^Open now playing: / }).click();
  const sheet = page.getByRole("dialog", { name: "Now playing" });
  await sheet.waitFor();
  if (panel) await sheet.getByRole("button", { name: panel }).first().click();
  return sheet;
}

async function shot(browser, name, viewport, scheme, fn) {
  if (ONLY && !ONLY.some((o) => name.startsWith(o))) return;
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2, colorScheme: scheme });
  const page = await ctx.newPage();
  const target = (await fn(page)) ?? page; // a locator crops to that element
  await page.locator(".job-strip").waitFor({ state: "detached", timeout: 10_000 });
  await settle(page);
  await target.screenshot({ path: join(OUT, `${name}-${scheme}.jpg`), type: "jpeg", quality: 88 });
  await ctx.close();
}

async function demo(browser) {
  if (ONLY && !ONLY.includes("demo")) return;
  const dir = mkdtempSync(join(tmpdir(), "muse-demo-"));
  const ctx = await browser.newContext({
    viewport: DESKTOP, colorScheme: "dark", recordVideo: { dir, size: DESKTOP },
  });
  const t0 = Date.now();
  const page = await ctx.newPage();
  await play(page, "Seaside Town", 30);
  await page.getByRole("button", { name: "More for Crazy Glue" }).first().click();
  await page.getByRole("menuitem", { name: "Play next" }).click();
  const sheet = await nowPlaying(page);
  await page.evaluate(() => { for (const a of document.querySelectorAll("audio")) if (a.currentTime > 0) a.play(); });
  await settle(page, 1500);
  const trim = (Date.now() - t0) / 1000; // everything before this is setup
  await page.waitForTimeout(2000);
  // Next: the room crossfades from the magenta of Friendship Adventure to the orange of The Wake.
  await sheet.getByRole("button", { name: "Next", exact: true }).click();
  await page.waitForTimeout(300);
  await page.evaluate(() => { for (const a of document.querySelectorAll("audio")) if (!a.paused) a.currentTime = 40; });
  await page.waitForTimeout(1500);
  await sheet.getByRole("button", { name: "Lyrics" }).first().click();
  await page.waitForTimeout(9000);
  await ctx.close();
  const video = join(dir, readdirSync(dir)[0]);
  const palette = join(dir, "palette.png");
  const filters = "fps=12,scale=720:-1:flags=lanczos";
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-ss", String(trim), "-i", video, "-vf", `${filters},palettegen=stats_mode=diff`, palette]);
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-ss", String(trim), "-i", video, "-i", palette, "-lavfi",
    `${filters}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`, join(OUT, "demo.gif")]);
  rmSync(dir, { recursive: true, force: true });
}

// The launch scan has to finish first, or its progress strip sits on every shot.
for (;;) {
  const jobs = await (await fetch(`${BASE}/api/jobs?t=${TOKEN}`)).json();
  if (jobs.some((j) => j.kind === "scan" && j.state === "done")
      && !jobs.some((j) => j.state === "running" || j.state === "queued")) break;
  await new Promise((r) => setTimeout(r, 500));
}

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
try {
  for (const scheme of ["light", "dark"]) {
    for (const [i, title] of HERO.entries()) {
      await shot(browser, `hero-${i + 1}`, PHONE, scheme, async (page) => {
        await play(page, title, 60);
        await nowPlaying(page);
      });
    }
    await shot(browser, "library", DESKTOP, scheme, async (page) => {
      await play(page, "Dunes at Night", 60);
      await open(page, "/library");
    });
    await shot(browser, "lyrics", DESKTOP, scheme, async (page) => {
      await play(page, "Crazy Glue", 47.5);
      await nowPlaying(page, "Lyrics");
    });
    await shot(browser, "radio", DESKTOP, scheme, async (page) => {
      await play(page, "Lafayette", 60);
      await open(page, "/muse");
    });
    await shot(browser, "edit", DESKTOP, scheme, async (page) => {
      await play(page, "Golden Sunrise", 60);
      await open(page, "/library");
      await page.getByRole("button", { name: "Songs" }).click();
      await page.getByRole("button", { name: "More for Golden Sunrise" }).first().click();
      await page.getByRole("menuitem", { name: "Edit details" }).click();
    });
  }
  // The mini window is a remote that mirrors the main window over Tauri events.
  // Stand in for that bridge with the state the main window would send.
  const songs = await (await fetch(`${BASE}/api/library/songs?t=${TOKEN}`)).json();
  const pick = (Array.isArray(songs) ? songs : songs.results).find((t) => t.track_name === "Dunes at Night");
  const now = { track: { ...pick, has_art: true }, playing: true };
  for (const scheme of ["light", "dark"]) {
    await shot(browser, "mini", { width: 400, height: 96 }, scheme, async (page) => {
      await page.addInitScript((state) => {
        let send = null;
        window.__TAURI__ = {
          core: { invoke: async () => {} },
          event: {
            listen: async (_name, cb) => { send = cb; return () => {}; },
            emitTo: async (_target, name) => { if (name === "muse:hello") send?.({ payload: state }); },
          },
          window: { getCurrentWindow: () => ({ close: async () => {} }) },
        };
      }, now);
      await page.goto(`${BASE}/mini?t=${TOKEN}`);
      await page.locator(".mini-title", { hasText: "Dunes at Night" }).waitFor();
      return page.locator(".mini");
    });
  }
  await demo(browser);
} finally {
  await browser.close();
}
