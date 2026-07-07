# Sprite example provenance

The pixel-sprite examples in `examples.json` were derived from **Kenney's
"Tiny Town" asset pack** (https://kenney.nl/assets/tiny-town), licensed
**Creative Commons CC0 1.0 Universal** (public domain — no attribution
required; credited voluntarily).

Derivation (2026-07-06): building compositions were assembled from individual
16×16 tiles, centre-cropped, downscaled to ≤14×14, and colour-quantised to
≤5 colours to match the simulation's blueprint `sprite` format
(`{palette, grid}`, validated by `validate_sprite_block` in
`simulation/server.py`). `preview.png` shows the converted set.

These examples are shown (one per invention-only prompt) as few-shot style
references so the local LLM authors recognisable structure sprites.
