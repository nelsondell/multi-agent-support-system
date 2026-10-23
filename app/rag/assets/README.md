# Tokenizer assets

`cl100k_base.tiktoken.gz` is tiktoken's `cl100k_base` merge table (MIT, via tiktoken), from
`https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken`, gzipped. Unpacked, its
SHA-256 is `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`, which tiktoken
checks on load. `app/rag/chunk.py` unpacks it, so chunking never downloads it.
