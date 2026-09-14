/* RustMetrics — Skin Checker 3D outfit preview.
   Loads the rigged/UV-split playermodel GLB and highlights which of the
   6 outfit slots are currently filled (orange = filled, gray = empty).
   Deliberately NOT texturing the mesh with real skin icons: base_mesh's
   UV islands are Smart-UV-Project auto-generated, not hand-unwrapped, so
   projecting a small square item-icon photo across them fragments into
   disconnected, mismatched patches per island (e.g. a mottled torso next
   to a flat black sleeve) — tried it, looked worse than a plain tint.
   Real icons are shown as flat 2D thumbnails in the item/outfit lists
   instead (see app.js), where they render correctly. Vanilla JS, no
   build step. Depends on
   /vendor/three/{three.min.js,GLTFLoader.js,OrbitControls.js} being
   loaded first (see index.html).
*/
(() => {
  "use strict";

  const MODEL_URL = "/playermodel/base_mesh.glb";

  // outfit slot -> mesh name inside base_mesh.glb (see assets/playermodel/SOURCE.md)
  const SLOT_MESH = {
    hoodie:   "body_hoodie",
    pants:    "body_pants",
    boots:    "body_boots",
    mask:     "head_mask",
    hat:      "head_hat",
    backpack: "back_backpack",
  };

  // Dedicated, cleanly-UV'd overlay mesh per slot for real icon texturing —
  // separate names from SLOT_MESH on purpose. Today's base_mesh.glb has none
  // of these, so scene.getObjectByName() returns null and every slot falls
  // straight back to the plain tint on SLOT_MESH, unchanged. Once
  // base_mesh.blend adds one of these per slot (see assets/playermodel/
  // SOURCE.md — clean non-overlapping UV, sized for one square item icon),
  // it lights up automatically with no further code changes.
  const SLOT_TEXTURE_MESH = {
    hoodie:   "skin_hoodie",
    pants:    "skin_pants",
    boots:    "skin_boots",
    mask:     "skin_mask",
    hat:      "skin_hat",
    backpack: "skin_backpack",
  };

  // Body meshes to hide (clipping-prevention) once a slot is filled — same
  // "doesn't exist yet, safe no-op" story as SLOT_TEXTURE_MESH.
  const SLOT_HIDE_MESHES = {
    hoodie:   ["arms_shoulders_hands", "torso_tshirt"],
    pants:    ["legs_upper_lower", "underwear"],
    boots:    ["feet"],
    mask:     [],
    hat:      [],
    backpack: [],
  };

  const textureLoader = typeof THREE !== "undefined" ? new THREE.TextureLoader() : null;
  const textureCache = new Map(); // image_url -> THREE.Texture

  function loadIconTexture(url) {
    if (!textureLoader || !url) return null;
    if (textureCache.has(url)) return textureCache.get(url);
    const tex = textureLoader.load(url);
    tex.colorSpace = THREE.SRGBColorSpace;
    textureCache.set(url, tex);
    return tex;
  }

  const COLOR_EMPTY  = 0x555555; // slot not equipped
  const COLOR_FILLED = 0xe85a3e; // var(--accent-bright) — slot has an item
  const COLOR_SKIN   = 0xc79b74; // body_skin, not an outfit slot
  const COLOR_BG     = 0x1d1d1d; // var(--bg-elevated)

  const CAMERA_START = { x: 1.8, y: 1.5, z: 2.4 };
  const TARGET_START = { x: 0, y: 0.95, z: 0 };

  let renderer = null;
  let scene = null;
  let camera = null;
  let controls = null;
  let slotMeshes = {};      // slot name -> THREE.Mesh (plain tint, always present today)
  let textureMeshes = {};   // slot name -> THREE.Mesh (dedicated UV overlay, future-only)
  let ready = false;
  let pendingOutfit = null;

  function applySlotState(slot, item) {
    const isFilled = Boolean(item);
    const tintMesh = slotMeshes[slot];
    const texMesh = textureMeshes[slot];
    const tex = texMesh && isFilled ? loadIconTexture(item.image_url) : null;
    const showTex = Boolean(tex);

    if (tintMesh && tintMesh.material) {
      tintMesh.visible = !showTex;
      tintMesh.material.color.setHex(isFilled ? COLOR_FILLED : COLOR_EMPTY);
      tintMesh.material.emissive?.setHex(isFilled ? COLOR_FILLED : 0x000000);
      tintMesh.material.emissiveIntensity = isFilled ? 0.15 : 0;
    }

    if (texMesh) {
      texMesh.visible = showTex;
      if (showTex && texMesh.material) {
        texMesh.material.map = tex;
        texMesh.material.color.setHex(0xffffff);
        texMesh.material.needsUpdate = true;
      }
    }

    for (const name of SLOT_HIDE_MESHES[slot] || []) {
      const hideMesh = scene?.getObjectByName(name);
      if (hideMesh) hideMesh.visible = !isFilled;
    }
  }

  function setOutfit(outfitBySlot) {
    if (!ready) { pendingOutfit = outfitBySlot; return; }
    for (const slot of Object.keys(SLOT_MESH)) {
      applySlotState(slot, outfitBySlot && outfitBySlot[slot]);
    }
  }

  function onResize(canvas) {
    if (!renderer || !camera) return;
    const w = canvas.clientWidth || 1;
    const h = canvas.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function animate() {
    requestAnimationFrame(animate);
    controls?.update();
    renderer.render(scene, camera);
  }

  /**
   * Boots the 3D viewer into `canvas`. Safe to call once; later calls are
   * no-ops. Loading/render errors are logged to the panel's hint element
   * (if given) instead of throwing, since the whole viewer is optional UI.
   * @param {HTMLCanvasElement} canvas
   * @param {HTMLElement} [hintEl]
   */
  function init(canvas, hintEl) {
    if (renderer || !canvas || typeof THREE === "undefined") return;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(COLOR_BG);

    camera = new THREE.PerspectiveCamera(40, 1, 0.05, 50);
    camera.position.set(CAMERA_START.x, CAMERA_START.y, CAMERA_START.z);

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    onResize(canvas);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2a2a, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(2, 4, 3);
    scene.add(key);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(TARGET_START.x, TARGET_START.y, TARGET_START.z);
    controls.enablePan = false;
    controls.minDistance = 1.2;
    controls.maxDistance = 5;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.4;
    controls.update();

    const loader = new THREE.GLTFLoader();
    loader.load(
      MODEL_URL,
      (gltf) => {
        scene.add(gltf.scene);
        slotMeshes = {};
        textureMeshes = {};
        gltf.scene.traverse((obj) => {
          if (!obj.isMesh) return;
          for (const [slot, meshName] of Object.entries(SLOT_MESH)) {
            if (obj.name === meshName) slotMeshes[slot] = obj;
          }
          for (const [slot, meshName] of Object.entries(SLOT_TEXTURE_MESH)) {
            if (obj.name === meshName) { textureMeshes[slot] = obj; obj.visible = false; }
          }
          if (obj.name === "body_skin" && obj.material) {
            obj.material.color.setHex(COLOR_SKIN);
          }
        });
        ready = true;
        if (pendingOutfit !== null) { setOutfit(pendingOutfit); pendingOutfit = null; }
        else setOutfit({});
        if (hintEl) hintEl.textContent = "Drag to rotate · scroll to zoom";
      },
      undefined,
      (err) => {
        if (hintEl) hintEl.textContent = "Couldn't load 3D preview.";
      }
    );

    window.addEventListener("resize", () => onResize(canvas));
    animate();
  }

  function toggleAutoRotate() {
    if (!controls) return false;
    controls.autoRotate = !controls.autoRotate;
    return controls.autoRotate;
  }

  function resetCamera() {
    if (!camera || !controls) return;
    camera.position.set(CAMERA_START.x, CAMERA_START.y, CAMERA_START.z);
    controls.target.set(TARGET_START.x, TARGET_START.y, TARGET_START.z);
    controls.update();
  }

  window.SkinViewer = { init, setOutfit, toggleAutoRotate, resetCamera };
})();
