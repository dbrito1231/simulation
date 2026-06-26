# SPEC 03 — World Rendering (Canvas)

**Build target.** Adds world rendering to `index.html`. No agents yet. Gate: GATE C.

## Canvas setup

| Property | Value |
|----------|-------|
| Canvas size | 1280 × 720 px |
| World area | 1000 × 720 px (left side) |
| UI panel | 280 px wide (right side) — drawn in Spec 05, leave blank for now |

Top-of-file comment (include verbatim):

```html
<!-- HOW TO RUN:
  1. Start server.py first
  2. Open http://127.0.0.1:5001 in Chrome or Firefox
  3. No build step needed
-->
```

> Note: the page is served by Flask (`GET /`), not opened from a `file://` URL —
> the relative `fetch("/agent/think")` only resolves over `http://`.

## Zones (draw exactly these — no more, no fewer)

All terrain is drawn with Canvas 2D primitives. **No image files. The uploaded background image is NOT used.**

| Zone | Color | Approx region (within 1000×720) | Resource |
|------|-------|----------------------------------|----------|
| Ocean | `#3daee9` | Left strip, x 0–150 | none |
| Beach | `#f5e6a3` | x 150–320, full height | none (travel) |
| Farm | `#7ec850` | x 380–640, y 40–220 | food |
| Forest | `#2d6a2d` | x 700–960, y 40–260 | wood |
| Village | `#d4a96a` | x 420–700, y 280–460 | none |
| Market | `#c8874a` | small square inside village, ~x 560–660, y 360–440 | trade |
| Cave/Mine | `#555555` | x 720–940, y 520–680 | gold |
| Path | `#c8a87a` | thin connectors between zones | travel |

## How to draw each element

| Element | Method |
|---------|--------|
| Zone backgrounds | `fillRect()` with the zone color |
| Ocean waves | a few wavy `beginPath()` + `quadraticCurveTo()` light-blue lines |
| Trees (forest) | dark-green `arc()` circles, ~12px radius, scattered |
| Buildings (village) | small brown `fillRect()` with a darker `fillRect()` roof on top |
| Market marker | an orange square outline with a tiny "M" label |
| Cave entrance | a dark `arc()` semicircle on the gray region |
| Paths | thin tan `fillRect()` strips connecting zone centers |
| Zone labels | white 12px text centered in each zone |

## Zone center coordinates (used later for agent movement — define them now)

```javascript
const ZONE_CENTERS = {
  farm:    { x: 500, y: 120 },
  forest:  { x: 820, y: 140 },
  village: { x: 550, y: 360 },
  market:  { x: 600, y: 400 },
  beach:   { x: 230, y: 360 },
  cave:    { x: 820, y: 580 },
  ocean:   { x: 80,  y: 360 }
};
```

## getZone(x, y) function

Returns the zone name string for any canvas coordinate, using simple boundary checks (if/else on x and y ranges). Defaults to `"path"` if no zone matches.

## Render function

Write `drawWorld(ctx)` that paints all zones, terrain decorations, paths, and labels. It is called once per frame (the loop comes in Spec 05). For this gate, call it once on page load so the world is visible.

## Gate C pass condition

- World renders in the browser.
- All 8 zones visible and labeled.
- No console errors.
- No agents present yet (that is Spec 04).
