# M5 Task 2 preflight: yt-dlp binary + m4a path

Date: 2026-10-30. Host: linux (x86_64, `yt-dlp_linux`). Command: `uv run python scripts/ytdlp_preflight.py`.

```json
{
  "sha256_ok": true,
  "version": "2026.10.26",
  "probe_rc": 0,
  "probe_warnings": [
    "WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; to use another runtime add  --js-runtimes RUNTIME[:PATH]  to your command/config. YouTube extraction without a JS runtime has been deprecated, and some formats may be missing. See  https://github.com/yt-dlp/yt-dlp/wiki/EJS  for details on installing one"
  ],
  "entry": {"id": true, "title": true, "duration": true, "thumbnail": true},
  "entry_id": "dQw4w9WgXcQ",
  "audio_formats": [
    ["sb3", "mhtml", "none"], ["sb2", "mhtml", "none"], ["sb1", "mhtml", "none"], ["sb0", "mhtml", "none"],
    ["233", "mp4", null], ["234", "mp4", null], ["139", "m4a", "mp4a.40.5"],
    ["249", "webm", "opus"], ["250", "webm", "opus"], ["140", "m4a", "mp4a.40.2"], ["251", "webm", "opus"]
  ],
  "download_rc": 0,
  "download_stderr_last": "",
  "file_size": 3447031,
  "probe_duration": 213,
  "mp4_length": 213.0895238095238,
  "remux_delta_s": 0.08952380952379713,
  "title_readback": ["T"]
}
```

Verdict: `PREFLIGHT-OK`

(JSON above is the script output with the `audio_formats` / `entry` arrays collapsed onto fewer lines; values are verbatim.)

Findings:
- m4a available: yes, itag 140 (m4a, mp4a.40.2) and 139 are listed and `bestaudio[ext=m4a]` downloaded 3,447,031 bytes with rc 0, no JS runtime installed.
- JS-runtime warning (probe only, kept on purpose): `WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; ... YouTube extraction without a JS runtime has been deprecated, and some formats may be missing.` Works today but is flagged as deprecated.
- Remux: `remux_m4a` accepted the file; MP4 length 213.0895 s vs probe duration 213 s, delta +0.09 s; `tag_m4a` wrote and `\xa9nam` read back `["T"]`.
