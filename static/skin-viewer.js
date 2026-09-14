/* RustMetrics — Skin Checker 3D outfit preview.
   Loads the rigged playermodel GLB and dresses it per outfit slot.
   Garments are NOT modelled in the GLB: each slot's shell is built at
   runtime from the body pieces it covers (skin-shells.js), tinted with
   the picked skin's icon colour and printed with the icon on its front
   (skin-icon-texture.js). That is an honest stand-in — Rust's real skin
   textures are Facepunch's, so this never claims to be the in-game art.
   Vanilla JS, no build step. Load order (see index.html):
   three.min.js, GLTFLoader.js, OrbitControls.js, skin-shell-math.js,
   skin-shells.js, skin-icon-texture.js, then this file. */
(() => {
  "use strict";

  const MODEL_URL = "/playermodel/base_mesh.glb";

  // Body pieces from the CC4 export (names verified against the GLB, see
  // assets/playermodel/SOURCE.md). Hands sit at y<1.04, |x|>0.40 in the
  // arms-down bind pose — a hoodie's sleeves stop at the wrist, so those
  // triangles are cut out of the hoodie shell.
  const HAND_MAX_Y = 1.04;
  const HAND_MIN_ABS_X = 0.40;
  const HAT_MIN_Y = 1.67;      // above the hairline
  const MASK_MIN_Y = 1.49;     // chin
  const MASK_MIN_Z = -0.03;    // front half of the head only
  const BOOT_MAX_Y = 0.30;     // ankle-high boots: feet plus the bottom of the shins
  const SHELL_INFLATE = 0.006; // metres; enough to clear the skin without floating
  const BOOT_INFLATE = 0.010;  // boots sit outside the pants shell at the ankle

  const SLOT_SHELLS = {
    hoodie: {
      sources: ["torso_full", "arms_shoulders_hands"],
      keepVertex: (x, y) => !(y < HAND_MAX_Y && Math.abs(x) > HAND_MIN_ABS_X),
      print: { width: 0.50, v0: 0.04, v1: 0.98 },
    },
    pants: {
      sources: ["underwear", "legs_upper_lower_tummy"],
      print: { width: 0.60, v0: 0.03, v1: 0.98 },
    },
    boots: {
      sources: ["feet", "legs_upper_lower_tummy"],
      keepVertex: (x, y) => y < BOOT_MAX_Y,
      inflate: BOOT_INFLATE,
      print: { width: 0.50, v0: 0.08, v1: 0.95 },
    },
    hat: {
      sources: ["head"],
      keepVertex: (x, y) => y >= HAT_MIN_Y,
      print: { width: 0.50, v0: 0.05, v1: 0.95 },
    },
    mask: {
      sources: ["head"],
      keepVertex: (x, y, z) => y >= MASK_MIN_Y && y < HAT_MIN_Y && z > MASK_MIN_Z,
      print: { width: 0.55, v0: 0.05, v1: 0.95 },
    },
    backpack: {
      backpack: true,
      print: { width: 0.60, v0: 0.10, v1: 0.90 },
    },
  };

  // Placeholder slot meshes from the old CC0 body — superseded by the
  // runtime shells, kept in the GLB only so older exports still load.
  const LEGACY_HIDDEN = ["body_hoodie", "body_pants", "body_boots", "head_hat", "head_mask", "back_backpack"];
  // CC4 alternates that duplicate other pieces (z-fight when all shown).
  const DUPLICATE_HIDDEN = ["hands_gloves", "torso_tshirt", "torso_long_shirt", "legs_upper_lower"];

  const COLOR_LOADING = 0x6b6b6b; // shell tint while the icon is still loading
  const COLOR_BG      = 0x1d1d1d; // var(--bg-elevated)
  const CAMERA_START  = { x: 1.8, y: 1.5, z: 2.4 };
  const TARGET_START  = { x: 0, y: 0.95, z: 0 };

  let renderer = null;
  let scene = null;
  let camera = null;
  let controls = null;
  let shells = {};           // slot -> THREE.Mesh
  let ready = false;
  let pendingOutfit = null;
  const slotToken = {};      // slot -> counter, drops stale async icon loads
  const dressedItemId = {};  // slot -> item_id currently worn (null = bare)

  function findMeshes(root, names) {
    // Not getObjectByName(): bones and meshes can share a name ("head").
    const byName = new Map();
    root.traverse((o) => { if (o.isMesh && !byName.has(o.name)) byName.set(o.name, o); });
    return names.map((n) => byName.get(n)).filter(Boolean);
  }

  function buildShells(root) {
    const out = {};
    for (const [slot, spec] of Object.entries(SLOT_SHELLS)) {
      const mesh = spec.backpack
        ? window.SkinShells.buildBackpack()
        : window.SkinShells.buildShell({
            sources: findMeshes(root, spec.sources),
            inflate: spec.inflate || SHELL_INFLATE,
            keepVertex: spec.keepVertex,
            facing: 1,
          });
      if (!mesh) continue;
      mesh.name = `shell_${slot}`;
      mesh.visible = false;
      (spec.backpack ? root : findMeshes(root, spec.sources)[0].parent).add(mesh);
      out[slot] = mesh;
    }
    return out;
  }

  function hideByName(root, names) {
    for (const mesh of findMeshes(root, names)) mesh.visible = false;
  }

  function disposeMap(material) {
    if (material.map) { material.map.dispose(); material.map = null; }
  }

  async function dressSlot(slot, item) {
    const shell = shells[slot];
    if (!shell) return;
    // setOutfit() re-sends the whole outfit on every change; re-dressing an
    // unchanged slot would flash it grey and rebuild its texture for nothing.
    const nextId = item ? item.item_id : null;
    if (dressedItemId[slot] === nextId) return;
    dressedItemId[slot] = nextId;
    const token = (slotToken[slot] = (slotToken[slot] || 0) + 1);
    if (!item) {
      shell.visible = false;
      disposeMap(shell.material);
      return;
    }
    shell.visible = true;
    disposeMap(shell.material);
    shell.material.color.setHex(COLOR_LOADING);
    shell.material.needsUpdate = true;

    const img = await window.SkinIconTexture.loadIcon(item.image_url);
    if (slotToken[slot] !== token) return; // slot changed while loading
    if (!img) return;                      // no icon → plain tint stays
    const tint = window.SkinIconTexture.dominantColor(img);
    const tintCss = `#${tint.clone().convertLinearToSRGB().getHexString()}`;
    shell.material.color.copy(tint);
    shell.material.map = window.SkinIconTexture.buildTexture(img, tintCss, SLOT_SHELLS[slot].print);
    shell.material.needsUpdate = true;
  }

  function setOutfit(outfitBySlot) {
    if (!ready) { pendingOutfit = outfitBySlot; return; }
    for (const slot of Object.keys(SLOT_SHELLS)) {
      dressSlot(slot, outfitBySlot && outfitBySlot[slot]);
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

  function onModelLoaded(gltf, hintEl) {
    scene.add(gltf.scene);
    hideByName(gltf.scene, LEGACY_HIDDEN);
    hideByName(gltf.scene, DUPLICATE_HIDDEN);
    shells = buildShells(gltf.scene);
    ready = true;
    setOutfit(pendingOutfit || {});
    pendingOutfit = null;
    if (hintEl) hintEl.textContent = "Drag to rotate · scroll to zoom";
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
    if (!window.SkinShells || !window.SkinIconTexture || !window.SkinShellMath) {
      if (hintEl) hintEl.textContent = "Couldn't load 3D preview.";
      return;
    }

    scene = new THREE.Scene();
    scene.background = new THREE.Color(COLOR_BG);

    camera = new THREE.PerspectiveCamera(40, 1, 0.05, 50);
    camera.position.set(CAMERA_START.x, CAMERA_START.y, CAMERA_START.z);

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = THREE.sRGBEncoding; // GLB colour maps are sRGB-tagged
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

    new THREE.GLTFLoader().load(
      MODEL_URL,
      (gltf) => {
        try { onModelLoaded(gltf, hintEl); }
        catch (e) {
          console.error("[skin-viewer] shell build failed", e);
          if (hintEl) hintEl.textContent = "Couldn't build 3D preview.";
        }
      },
      undefined,
      () => { if (hintEl) hintEl.textContent = "Couldn't load 3D preview."; }
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

  window.SkinViewer = { init, setOutfit, toggleAutoRotate, resetCamera, _scene: () => scene };
})();
