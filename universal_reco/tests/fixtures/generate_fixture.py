"""Regenerates sample.wav: a 3-second 440Hz mono sine wave at 44.1kHz.

Not a music clip -- just real PCM audio data to exercise the load/resample/
chunk pipeline without needing a copyrighted or license-encumbered file.
"""

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44100
DURATION_SECONDS = 3
FREQUENCY_HZ = 440

def main():
    out_path = Path(__file__).parent / "sample.wav"
    n_samples = SAMPLE_RATE * DURATION_SECONDS
    frames = bytearray()
    for i in range(n_samples):
        t = i / SAMPLE_RATE
        value = int(32767 * 0.5 * math.sin(2 * math.pi * FREQUENCY_HZ * t))
        frames += struct.pack("<h", value)

    with wave.open(str(out_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(bytes(frames))

if __name__ == "__main__":
    main()
