/* RustMetrics — Skin Checker 3D outfit preview.
   Loads the rigged/UV-split playermodel GLB and shows which of the 6
   outfit slots are filled. When an item has image_url (a locally-mirrored
   Steam icon — see backfill_skin_icons.py; Steam's own CDN has no CORS
   headers so a direct cross-origin texture load silently fails), that
   icon is applied as the slot's texture; otherwise the slot just tints
   orange so coverage is still visible before icons are backfilled.
   Vanilla JS, no build step. Depends on
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

  const COLOR_EMPTY  = 0x555555; // slot not equipped
  const COLOR_FILLED = 0xe85a3e; // var(--accent-bright) — slot has an item
  const COLOR_SKIN   = 0xc79b74; // body_skin, not an outfit slot
  const COLOR_BG     = 0x1d1d1d; // var(--bg-elevated)

  let renderer = null;
  let scene = null;
  let camera = null;
  let controls = null;
  let textureLoader = null;
  let slotMeshes = {};    // slot name -> THREE.Mesh
  let textureCache = {};  // image_url -> THREE.Texture
  let ready = false;
  let pendingOutfit = null;

  function tintSlot(mesh, isFilled) {
    mesh.material.map = null;
    mesh.material.color.setHex(isFilled ? COLOR_FILLED : COLOR_EMPTY);
    mesh.material.emissive?.setHex(isFilled ? COLOR_FILLED : 0x000000);
    mesh.material.emissiveIntensity = isFilled ? 0.15 : 0;
    mesh.material.needsUpdate = true;
  }

  function applyTexture(mesh, url) {
    const cached = textureCache[url];
    if (cached) {
      mesh.material.map = cached;
      mesh.material.color.setHex(0xffffff);
      mesh.material.emissive?.setHex(0x000000);
      mesh.material.emissiveIntensity = 0;
      mesh.material.needsUpdate = true;
      return;
    }
    // Show the plain "filled" tint while the texture loads, then swap in.
    tintSlot(mesh, true);
    textureLoader.load(
      url,
      (tex) => {
        tex.encoding = THREE.sRGBEncoding;
        textureCache[url] = tex;
        // Slot may have changed again while this was loading.
        if (mesh.material.__pendingUrl === url) applyTexture(mesh, url);
      },
      undefined,
      () => { /* same-origin mirror missing/broken — keep the tint fallback */ }
    );
  }

  function applySlotState(slot, item) {
    const mesh = slotMeshes[slot];
    if (!mesh || !mesh.material) return;
    mesh.material.__pendingUrl = item?.image_url || null;
    if (item && item.image_url) applyTexture(mesh, item.image_url);
    else tintSlot(mesh, Boolean(item));
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
    camera.position.set(1.8, 1.5, 2.4);

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    onResize(canvas);

    textureLoader = new THREE.TextureLoader();

    scene.add(new THREE.HemisphereLight(0xffffff, 0x2a2a2a, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(2, 4, 3);
    scene.add(key);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.95, 0);
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
        gltf.scene.traverse((obj) => {
          if (!obj.isMesh) return;
          for (const [slot, meshName] of Object.entries(SLOT_MESH)) {
            if (obj.name === meshName) slotMeshes[slot] = obj;
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

  window.SkinViewer = { init, setOutfit };
})();
