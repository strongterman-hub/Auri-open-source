# Portrait placeholder assets

This directory intentionally contains **procedurally generated gradient placeholders**, not the real character artwork.

- 12 variants x 2 presentations x 2 sizes (`*.jpg` and `*@540.jpg`).
- The real Qwen-generated scenes stay in the private workspace and are deployed only to the private service.
- Why: the generated portraits are personal likeness assets; the public repository keeps the feature testable without redistributing them.

To build an environment with real artwork, run the private tooling:

```bash
cd tools/portrait-gen
py -3.12 build_variants.py --target <server>/app/static/portrait --force
```

The server-side resolver (`app/persona/portrait.py`) only requires matching file names; it will fall back to `day_gentle` when a variant is missing.
