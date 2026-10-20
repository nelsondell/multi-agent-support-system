# Tokenizer assets

`9b5ad71b2ce5302211f9c61530b329a4922fc6a4` is tiktoken's `cl100k_base` merge table, from
`https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` (MIT, via tiktoken).
Its name is the SHA-1 of that URL, which is how tiktoken looks a cached file up; its SHA-256 is
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`, which tiktoken checks on load.
`app/rag/chunk.py` points tiktoken at this directory, so chunking never downloads it.
