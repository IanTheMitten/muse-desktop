"""PyAV decode to mono float32 PCM. The frozen worker has no ffmpeg on PATH,
so this replaces the ffmpeg/ffprobe subprocesses. `av` and `numpy` are
imported inside the functions, never at module import."""

from pathlib import Path


class AudioDecodeError(RuntimeError):
    pass


def decode_mono(
    path: Path | str, sample_rate: int, start: float = 0.0,
    duration: float | None = None,
) -> "np.ndarray":
    import av
    import numpy as np

    resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
    chunks = []
    try:
        with av.open(str(path)) as container:
            if start > 0:
                container.seek(int(start * av.time_base))
            stream = container.streams.audio[0]
            for packet in container.demux(stream):
                try:
                    frames = packet.decode()
                except av.error.InvalidDataError:
                    continue
                for frame in frames:
                    chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    except (av.error.FFmpegError, IndexError) as e:
        raise AudioDecodeError(f"No audio decoded from {path}: {e}") from e
    if not chunks:
        raise AudioDecodeError(f"No audio decoded from {path}")
    samples = np.concatenate(chunks).astype(np.float32)
    if duration is not None:
        samples = samples[: int(round(duration * sample_rate))]
    if samples.size == 0:
        raise AudioDecodeError(f"No audio decoded from {path}")
    return samples


def duration_seconds(path: Path | str) -> float:
    import av

    try:
        with av.open(str(path)) as container:
            if container.duration:
                return container.duration / av.time_base
            stream = container.streams.audio[0]
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except (av.error.FFmpegError, IndexError) as e:
        raise AudioDecodeError(f"No audio decoded from {path}: {e}") from e
    return len(decode_mono(path, 16_000)) / 16_000
