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
  `base_mesh.glb`. UVs are Smart-UV-Project islands (fine for flat
  colors/baked textures, not laid out for hand-painting). Slot region
  boundaries are cut by face height and are jagged where no edge loop sits
  at that height — visible as a stepped hem on hoodie/pants and mask/hat.
  `back_backpack` is a synthetic offset shell, not real backpack geometry;
  replace with a proper CC0 pack mesh before final art pass.
- **Next step:** wire `base_mesh.glb` into the browser 3D viewer (not yet
  built — `static/` currently only has the flat item-list UI).
