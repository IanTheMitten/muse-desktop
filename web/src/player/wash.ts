import { withToken } from "../token";
/* The record wash engine. A cover becomes a 4-colour palette; each colour is
 * scaled into a luminance band so text contrast holds by construction, and the
 * accent is pushed to >= 3.2:1 against the worst (np) band. Art is same-origin,
 * so the canvas is never tainted. */

export type Palette = { colors: number[][]; vivid: number[] };
type Where = "np" | "card" | "app";

const lum = ([r, g, b]: number[]) => {
  const f = (c: number) => ((c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
const contrast = (a: number, b: number) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
const hex = (c: number[]) =>
  "#" + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
const sat = ([r, g, b]: number[]) => {
  const mx = Math.max(r, g, b);
  return mx === 0 ? 0 : (mx - Math.min(r, g, b)) / mx;
};
const mix = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);

export const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};

/** Calls `cb` when the system scheme or the data-theme attribute changes. */
export function onThemeChange(cb: () => void): () => void {
  const mq = matchMedia("(prefers-color-scheme: dark)");
  const mo = new MutationObserver(cb);
  mq.addEventListener("change", cb);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => {
    mq.removeEventListener("change", cb);
    mo.disconnect();
  };
}

const GRAY: Palette = {
  colors: [[60, 60, 66], [40, 40, 46], [70, 70, 76], [30, 30, 36]],
  vivid: [90, 90, 96],
};
const PAL = new Map<number, Palette>();

export function paletteFor(key: number, src: string): Promise<Palette> {
  const hit = PAL.get(key);
  if (hit) return Promise.resolve(hit);
  return new Promise((res) => {
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = c.height = 32;
        const x = c.getContext("2d", { willReadFrequently: true })!;
        x.drawImage(img, 0, 0, 32, 32);
        const d = x.getImageData(0, 0, 32, 32).data;
        const buckets = new Map<number, { n: number; r: number; g: number; b: number }>();
        for (let i = 0; i < d.length; i += 4) {
          const k = ((d[i] >> 4) << 8) | ((d[i + 1] >> 4) << 4) | (d[i + 2] >> 4);
          const b = buckets.get(k) ?? { n: 0, r: 0, g: 0, b: 0 };
          b.n++; b.r += d[i]; b.g += d[i + 1]; b.b += d[i + 2];
          buckets.set(k, b);
        }
        const cand = [...buckets.values()]
          .map((b) => {
            const col = [b.r / b.n, b.g / b.n, b.b / b.n];
            return { col, score: b.n * (0.35 + sat(col)) };
          })
          .sort((a, b) => b.score - a.score);
        const picked: number[][] = [];
        for (const c2 of cand) {
          if (picked.every((p) => Math.hypot(...p.map((v, i) => v - c2.col[i])) > 48)) picked.push(c2.col);
          if (picked.length === 4) break;
        }
        while (picked.length < 4) picked.push(picked[0] ?? [80, 80, 80]);
        const out = { colors: picked, vivid: [...picked].sort((a, b) => sat(b) - sat(a))[0] };
        PAL.set(key, out);
        res(out);
      } catch {
        res(GRAY);
      }
    };
    img.onerror = () => res(GRAY);
    img.src = withToken(src);
  });
}

const BAND = {
  np: { dark: 0.11, light: 0.36 },
  card: { dark: 0.09, light: 0.45 },
  app: { dark: 0.06, light: 0.55 },
};
const bandL = (where: Where) => BAND[where][isDark() ? "dark" : "light"];

function fit(c: number[], where: Where) {
  const t = bandL(where);
  let k = c;
  if (isDark()) for (let i = 0; i < 80 && lum(k) > t; i++) k = k.map((v) => v * 0.95);
  else for (let i = 0; i < 80 && lum(k) < t; i++) k = mix(k, [255, 255, 255], 0.06);
  return k;
}

function accentFor(p: Palette, bgL: number) {
  const dark = isDark();
  let c = p.vivid;
  if (sat(c) < 0.18) c = dark ? [233, 230, 225] : [43, 40, 48];
  for (let i = 0; i < 40 && contrast(lum(c), bgL) < 3.2; i++) c = mix(c, dark ? [255, 255, 255] : [0, 0, 0], 0.08);
  const on = contrast(lum(c), lum([18, 17, 23])) >= contrast(lum(c), 1) ? "#121117" : "#ffffff";
  return { accent: hex(c), on };
}

export function meshCss(cols: number[][], where: Where): string {
  // push chroma away from grey before banding, so the room reads as the record
  const vivid = (c: number[]) => {
    const g = (c[0] + c[1] + c[2]) / 3;
    return c.map((v) => Math.max(0, Math.min(255, g + (v - g) * 1.5)));
  };
  const f = cols.map((c) => fit(vivid(c), where));
  const c = f.map(hex);
  const base = hex(mix(f[0], isDark() ? [0, 0, 0] : [255, 255, 255], 0.15));
  return `radial-gradient(at 12% 18%, ${c[0]} 0, transparent 62%), radial-gradient(at 88% 12%, ${c[1]} 0, transparent 58%), radial-gradient(at 82% 88%, ${c[2]} 0, transparent 64%), radial-gradient(at 8% 92%, ${c[3]} 0, transparent 58%), ${base}`;
}

/** null restores the token defaults. */
export function applyAccent(p: Palette | null): void {
  const s = document.documentElement.style;
  if (!p) {
    s.removeProperty("--accent");
    s.removeProperty("--on-accent");
    return;
  }
  const { accent, on } = accentFor(p, bandL("np")); // np is the worst background: variant A is always on
  s.setProperty("--accent", accent);
  s.setProperty("--on-accent", on);
}
