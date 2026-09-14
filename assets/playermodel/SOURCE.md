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

- **Planned: hand-unwrapped texture overlay meshes (2026-09-14, not yet built).**
  `static/skin-viewer.js` already has the consumer side of this wired up and
  waiting — it's a safe no-op against today's GLB (verified: none of the
  mesh names below exist yet, so every slot falls straight back to the
  plain tint, unchanged). To light it up, add to `base_mesh.blend`, per slot:
  1. One additional mesh named `skin_<slot>` (`skin_hoodie`, `skin_pants`,
     `skin_boots`, `skin_mask`, `skin_hat`, `skin_backpack`) — geometry can
     reuse/duplicate the existing `body_<slot>`/`head_<slot>` shape, but
     **hand-unwrap its UVs** (seams placed so the whole mesh maps to one
     contiguous, non-overlapping square region of UV space) so a single
     square item-icon texture wraps it coherently instead of fragmenting.
  2. Keep the old `body_hoodie`/`body_pants`/etc. meshes as-is (the tint
     fallback still uses them for slots with no `skin_*` counterpart, and
     `skin-viewer.js` auto-hides the old mesh whenever the new one is
     showing a texture, so they never render on top of each other).
  3. Add the clipping-prevention meshes `skin-viewer.js` already looks for:
     `arms_shoulders_hands`, `torso_tshirt` (hidden when `hoodie` filled),
     `legs_upper_lower`, `underwear` (hidden when `pants` filled), `feet`
     (hidden when `boots` filled) — optional polish, no-op if absent.
  4. Re-export to `static/playermodel/base_mesh.glb`, same as before.
  No code changes needed on export — `SLOT_TEXTURE_MESH`/`SLOT_HIDE_MESHES`
  in `static/skin-viewer.js` already list these exact names.

- **Base body swapped to a Character Creator 4 export (2026-09-14).** The
  CC0 OpenGameArt body mesh (`body_skin`) is superseded — replaced with a
  higher-quality body generated in Reallusion Character Creator 4 and
  exported as `Model.glb` (confirmed with the user: their CC4 license
  permits redistributing the exported model file, not just using it
  in-app). The CC4 export arrived unrigged with generic mesh-data names
  but its scene-graph node names were already sensible body-part labels
  (`head`, `feet`, `torso_full`, `torso_long_shirt`, `torso_tshirt`,
  `legs_upper_lower`, `legs_upper_lower_tummy`, `underwear`,
  `arms_shoulders_hands`, `hands_full`, `hands_gloves`, `head_eyes`,
  `head_eyes_2`, `head_eye_lashes`) — several match the clipping-prevention
  mesh names `skin-viewer.js` was already looking for, so those light up
  now with no code changes. Workflow: imported into `base_mesh.blend`
  alongside the existing `PlayerRig`, translated up so feet sit at z=0
  (source export's origin wasn't floor-based), temporarily posed
  `upper_arm.L`/`upper_arm.R` down to match the CC4 mesh's arms-down
  idle pose, parented to `PlayerRig` with Automatic Weights (bind
  computed against that matching pose), then cleared the pose back to
  rest — the mesh's arms follow the armature back into the rig's
  T-pose correctly. Old `body_skin` mesh kept in the file but hidden
  (not deleted) rather than removed outright. The 6 outfit-slot tint
  meshes (`body_hoodie`, `body_pants`, `body_boots`, `head_hat`,
  `head_mask`, `back_backpack`) were left as-is and still roughly fit
  now that the new body is aligned to the same scale/pose — not
  re-fitted to the new body's exact proportions, so minor clipping is
  possible and worth a visual check. Re-exported to
  `static/playermodel/base_mesh.glb` (verified: 1 skin, 21 joints, all
  expected mesh nodes present). **Not yet done:** re-fitting the outfit
  slot meshes tightly to the new body shape, and the hand-unwrapped
  `skin_<slot>` overlay meshes from the section above (still fully
  no-op against this export, same as before).

- **Runtime garment shells replace all of the above slot-mesh plans (2026-09-14).**
  Neither the old CC0 tint meshes nor the planned hand-unwrapped `skin_<slot>`
  overlays are used any more — and no further Blender work is needed for
  outfits. `static/skin-shells.js` builds each slot's garment at page load
  from the CC4 body pieces it covers (`torso_full` + `arms_shoulders_hands`
  minus the hands → hoodie; `underwear` + `legs_upper_lower_tummy` → pants;
  `feet` + the bottom of the shins → boots; the top / front of `head` → hat /
  mask; a plain box on the back → backpack): copies their triangles, pushes
  them ~6 mm out along the vertex normals, gives them cylindrical UVs and
  re-binds the copy to `PlayerRig`, so every garment fits the body exactly by
  construction. `static/skin-icon-texture.js` tints the shell with the item
  icon's average colour and prints the icon on the shell's front. The legacy
  `body_*`/`head_*`/`back_backpack` meshes and the duplicate CC4 alternates
  (`hands_gloves`, `torso_tshirt`, `torso_long_shirt`, `legs_upper_lower`) are
  hidden at load (`static/skin-viewer.js`), not deleted from the GLB, so an
  older export still loads. Note the GLB's inverse bind matrices equal the
  rig's rest pose, so with no animation the body renders in the CC4 bind
  pose (arms down) — that is expected, not a skinning bug, and it matches
  the item icons better than a T-pose would. Pure geometry/colour maths lives
  in `static/skin-shell-math.js` (unit-tested with `node --test
  test_skin_shell_math.mjs`).
