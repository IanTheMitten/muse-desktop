"""Stream-copy YouTube's DASH m4a into a standard m4a with PyAV, no ffmpeg
binary (spec Decision 2). `av` is imported inside the function so startup
never loads it."""
from __future__ import annotations

import os
from pathlib import Path

NO_M4A = "No m4a audio for this video. Try another link."


def remux_m4a(path: Path) -> None:
    import av

    tmp = path.with_name(f"{path.stem}.remux.m4a")
    try:
        with av.open(str(path)) as src:
            if not src.streams.audio:
                raise RuntimeError(NO_M4A)
            stream = src.streams.audio[0]
            with av.open(str(tmp), "w", format="ipod") as dst:
                out = dst.add_stream_from_template(stream)
                for packet in src.demux(stream):
                    if packet.dts is None:
                        continue
                    packet.stream = out
                    dst.mux(packet)
        os.replace(tmp, path)
    except Exception as error:
        tmp.unlink(missing_ok=True)
        if isinstance(error, RuntimeError) and str(error) == NO_M4A:
            raise
        raise RuntimeError(NO_M4A) from error
