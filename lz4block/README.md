# lz4block

Decompression of raw [LZ4 blocks](https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md)
(no frame header; the uncompressed size is known from elsewhere, e.g. a bundle's file table).
Python 3.9+, no dependencies.

```python
import lz4block

data = lz4block.DEFAULT(block, uncompressed_size)
data = lz4block.decompressor("builtin")(block, uncompressed_size)
```

Two interchangeable decompressors, each `(block, uncompressed size) -> bytes`, raising `ValueError`
on bad data or a size mismatch:

| Name | What it is |
|---|---|
| `python-lz4` | The [lz4](https://pypi.org/project/lz4/) package (`pip install lz4`), in C. |
| `builtin` | Pure Python, about 100x slower (roughly 0.6 s for 15 MB). |

`DEFAULT` is python-lz4 when it's installed, else the built-in one. `decompressor(name)` raises
`ValueError` for an unknown name, or for python-lz4 when it isn't installed.
