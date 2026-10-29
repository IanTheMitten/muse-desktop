"""content_embed's public surface: `from content_embed import embed`.

`embed` is resolved lazily here (PEP 562 module `__getattr__`) rather than
via a plain `from .model import embed` at the top of this file. `model.py`
pulls in numpy/torch/torchaudio/transformers, and `python -m content_embed
.batch` imports this *package* -- running this file -- before `batch.py`'s
own `main()` runs. A top-level import here would drag that whole chain in
ahead of `main()`'s fd-1 swap (see `batch._seize_stdout_for_the_protocol`)
regardless of how carefully `batch.py` itself defers its own import, which
is exactly the gap that let a print at `model.py`'s top level reach real
stdout. `import content_embed` alone must not import `content_embed.model`;
only actually touching `embed` should.
"""

__all__ = ["embed"]


def __getattr__(name: str):
    if name == "embed":
        from .model import embed

        return embed
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
