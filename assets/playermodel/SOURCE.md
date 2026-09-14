# Playermodel base mesh — provenance

- **File:** `base_mesh.blend`
- **Source:** https://opengameart.org/content/low-poly-male-base-mesh
- **Downloaded from:** https://opengameart.org/sites/default/files/HumanBaseMesh.blend
- **License:** CC0 1.0 Universal (Public Domain) — verified on the OpenGameArt
  page (license badge + "CC0 Collections" tag) on 2026-09-14.
- **Why this one:** deliberately NOT Facepunch's Rust player model (proprietary,
  redistribution forbidden under the Rust EULA and Facepunch modding/UGC
  guidelines — see project discussion) and NOT any third-party site's
  compiled item/model database. A CC0 stand-in base mesh we can legally
  re-texture per outfit slot.
- **Rigged/UV-mapped:** done 2026-09-14 in Blender (armature `PlayerRig`,
  20 bones, automatic weights capped at 4 influences/vertex for three.js).
  Split into 7 named sub-meshes with dedicated UV space and material each:
  `body_hoodie`, `body_pants`, `body_boots`, `head_mask`, `head_hat`,
  `back_backpack`, plus `body_skin` (hands/neck, not a slot). Exported to
  `static/playermodel/base_mesh.glb` (the app's HTTP server only serves
  `static/`, not this `assets/` source directory — the `.blend` stays here
  for editing, only the GLB export is web-servable). UVs are
  Smart-UV-Project islands (fine for flat
  colors/baked textures, not laid out for hand-painting). Slot region
  boundaries are cut by face height and are jagged where no edge loop sits
  at that height — visible as a stepped hem on hoodie/pants and mask/hat.
  `back_backpack` is a synthetic offset shell, not real backpack geometry;
  replace with a proper CC0 pack mesh before final art pass.
- **3D viewer:** built 2026-09-14 — `static/skin-viewer.js` loads the GLB
  (via vendored three.js in `static/vendor/three/`) and tints each slot
  mesh to show which of the 6 outfit slots are filled.
- **Real per-skin textures on the mesh: tried, reverted.** `skin_items.image_url`
  got populated (real Steam icons, mirrored locally — see
  `backfill_skin_icons.py`) and briefly applied as a texture map per slot
  mesh. Looked broken in practice: the Smart-UV-Project islands (see
  above) are auto-generated and disconnected, so projecting one small
  square product-icon photo across them gives each island an unrelated
  crop of that image — a mottled torso next to a flat-colored sleeve,
  not a coherent garment. Fixing this for real needs a hand-unwrapped UV
  layout (seams placed so a texture actually wraps around each body part
  coherently), not just swapping the image source — that's future work,
  not attempted here. Real icons are shown as flat 2D thumbnails in the
  Skin Checker's item/outfit lists instead (`static/app.js`), where they
  render correctly with no UV involved.
