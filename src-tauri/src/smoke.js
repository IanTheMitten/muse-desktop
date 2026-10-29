// Smoke driver: injected only when MUSE_SMOKE_REPORT is set. Decodes the fixture songs through the real stream URLs.
document.addEventListener("DOMContentLoaded", async () => {
  // Windows serves the boot page from http://tauri.localhost/, so check the host too.
  if (location.protocol !== "http:" || location.hostname !== "127.0.0.1" || location.pathname !== "/") return;
  const token = window.__MUSE_TOKEN__;
  const headers = { "X-Muse-Token": token };
  const types = {
    mp3: "audio/mpeg",
    flac: "audio/flac",
    m4a: "audio/mp4",
    wav: "audio/wav",
    ogg: 'audio/ogg; codecs="vorbis"',
    opus: 'audio/ogg; codecs="opus"',
  };
  const report = { health: 0, media_session: "mediaSession" in navigator, formats: {} };
  try {
    report.health = (await fetch("/api/health", { headers })).status;
    let songs = [];
    for (let i = 0; i < 60; i++) {
      try {
        const r = await fetch("/api/library/songs", { headers });
        if (r.ok) {
          const body = await r.json();
          songs = Array.isArray(body) ? body : body.songs || body.items || [];
          if (songs.length >= 6) break;
        }
      } catch (e) {}
      await new Promise((res) => setTimeout(res, 500));
    }
    for (const s of songs) {
      const fmt = String(s.format).toLowerCase();
      const audio = new Audio();
      audio.preload = "auto";
      audio.muted = true;
      const result = new Promise((res) => {
        audio.addEventListener("loadeddata", () => res("decoded"));
        audio.addEventListener("error", () => res("error " + (audio.error ? audio.error.code : "?")));
        setTimeout(() => res("timeout"), 10000);
      });
      audio.src = `/api/tracks/${s.track_rowid}/stream?t=${token}`;
      report.formats[fmt] = { can_play_type: audio.canPlayType(types[fmt] || ""), result: await result };
    }
  } catch (e) {
    report.error = String(e);
  }
  window.__TAURI__.core.invoke("smoke_report", { report });
});
