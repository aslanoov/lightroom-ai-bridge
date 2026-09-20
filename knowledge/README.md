# `knowledge/` - the reference inbox

Drop raw reference material here and ask the agent to ingest it into the
knowledge base: articles and newsletters, exported presets (`.xmp`,
`.lrtemplate`), before/after pairs, award-winner collections, PDFs, your own
notes. Anything that carries photographic judgment worth keeping.

How it flows:

1. You drop a file at the **top level** of `knowledge/`.
2. The agent reads it and rewrites the affected `kb/` cards with what it learned
   (see `kb/README.md`, section 5).
3. The agent then **moves the source into `knowledge/processed/`**, never
   deletes it, so the top level always shows only what still needs ingesting.

Your own exported Lightroom presets are the highest-value drop: they are your
taste expressed in numbers, and the agent can read them directly.

> Contents of this folder are git-ignored by default (they are your material,
> often large and often copyrighted). Only this README and the folder structure
> are tracked.
