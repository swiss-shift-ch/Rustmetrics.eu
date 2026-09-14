/* RustMetrics — Skin Checker: builds the wearable "shell" meshes for the
   3D outfit preview at runtime, straight from the body model.

   The CC4 body in base_mesh.glb ships as skin PIECES (torso_full,
   arms_shoulders_hands, underwear, legs_upper_lower_tummy, feet, head …),
   not as garments. Instead of modelling clothes in Blender we take the
   body pieces a garment would cover, copy their triangles, push them a
   few millimetres out along the vertex normals and re-bind the copy to
   the same skeleton — a hoodie that fits the body exactly, by
   construction. The shells get cylindrical UVs so one square item icon
   can be printed on the front (see skin-icon-texture.js).
   Depends on three.js (r128, global THREE) and skin-shell-math.js. */
(() => {
  "use strict";

  const M = window.SkinShellMath;
  const SKIN_STRIDE = 4;      // skinIndex / skinWeight components per vertex
  const DEFAULT_ROUGHNESS = 0.88;

  // Backpack: the body has no back-mounted piece, so this is a plain box
  // sat on the upper back (model units are metres, feet at y=0).
  const BACKPACK_SIZE = { x: 0.34, y: 0.42, z: 0.17 };
  const BACKPACK_POS  = { x: 0, y: 1.27, z: -0.19 };

  function nonIndexedFloat(geometry, name) {
    const attr = geometry.getAttribute(name);
    if (!attr) return null;
    return attr.array instanceof Float32Array ? attr.array : Float32Array.from(attr.array);
  }

  /** Concatenates the given skinned meshes into one non-indexed triangle soup. */
  function mergeSources(meshes) {
    const parts = meshes.map((m) => {
      const g = m.geometry.index ? m.geometry.toNonIndexed() : m.geometry;
      if (!g.getAttribute("normal")) g.computeVertexNormals();
      return {
        pos: nonIndexedFloat(g, "position"),
        nrm: nonIndexedFloat(g, "normal"),
        si:  nonIndexedFloat(g, "skinIndex"),
        sw:  nonIndexedFloat(g, "skinWeight"),
      };
    });
    const concat = (key, stride) => {
      const total = parts.reduce((n, p) => n + (p[key] ? p[key].length : 0), 0);
      const out = new Float32Array(total);
      let off = 0;
      for (const p of parts) { if (p[key]) { out.set(p[key], off); off += p[key].length; } }
      return out;
    };
    return { pos: concat("pos", 3), nrm: concat("nrm", 3), si: concat("si", SKIN_STRIDE), sw: concat("sw", SKIN_STRIDE) };
  }

  function makeMaterial() {
    return new THREE.MeshStandardMaterial({
      color: 0x888888,
      roughness: DEFAULT_ROUGHNESS,
      metalness: 0,
      skinning: true,   // r128: SkinnedMesh materials must opt in explicitly
    });
  }

  /**
   * @param {object} spec
   * @param {THREE.SkinnedMesh[]} spec.sources  body pieces the garment covers
   * @param {number} spec.inflate               metres to push out along normals
   * @param {(x:number,y:number,z:number)=>boolean} [spec.keepVertex]  region filter
   * @param {number} [spec.facing]              +1 print faces +z (front), -1 faces -z
   * @returns {THREE.SkinnedMesh|null}
   */
  function buildShell(spec) {
    const sources = (spec.sources || []).filter(Boolean);
    if (!sources.length) return null;
    const merged = mergeSources(sources);
    const inflated = M.inflatePositions(merged.pos, merged.nrm, spec.inflate);
    const kept = M.keptTriangleIndices(inflated, spec.keepVertex || (() => true));
    if (!kept.length) return null;

    const pos = M.gatherVertices(inflated, kept, 3);
    const nrm = M.gatherVertices(merged.nrm, kept, 3);
    const uv  = M.cylindricalUVs(pos, M.bounds(pos), spec.facing || 1);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geometry.setAttribute("normal", new THREE.BufferAttribute(nrm, 3));
    geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
    geometry.setAttribute("skinIndex", new THREE.BufferAttribute(M.gatherVertices(merged.si, kept, SKIN_STRIDE), SKIN_STRIDE));
    geometry.setAttribute("skinWeight", new THREE.BufferAttribute(M.gatherVertices(merged.sw, kept, SKIN_STRIDE), SKIN_STRIDE));

    const ref = sources[0];
    const shell = new THREE.SkinnedMesh(geometry, makeMaterial());
    shell.position.copy(ref.position);
    shell.quaternion.copy(ref.quaternion);
    shell.scale.copy(ref.scale);
    shell.bind(ref.skeleton, ref.bindMatrix);
    shell.frustumCulled = false; // skinned bounds are stale in rest pose; cheap to always draw
    return shell;
  }

  /**
   * Static positioned mesh from a primitive geometry, UV'd cylindrically so a
   * square item icon prints on the `facing` side. Used for the backpack and
   * headgear: the body has no matching geometry to shell there, and the model
   * never animates, so a fixed-position primitive over the head/back is enough
   * and cheaper than skinning.
   */
  function buildStatic(geometry, pos, facing) {
    const ni = geometry.index ? geometry.toNonIndexed() : geometry;
    const p = nonIndexedFloat(ni, "position");
    ni.setAttribute("uv", new THREE.BufferAttribute(M.cylindricalUVs(p, M.bounds(p), facing || 1), 2));
    const mesh = new THREE.Mesh(ni, new THREE.MeshStandardMaterial({ color: 0x888888, roughness: DEFAULT_ROUGHNESS, metalness: 0 }));
    if (pos) mesh.position.set(pos.x, pos.y, pos.z);
    return mesh;
  }

  /** Static box on the upper back; UV front faces -z so the print shows from behind. */
  function buildBackpack() {
    return buildStatic(new THREE.BoxGeometry(BACKPACK_SIZE.x, BACKPACK_SIZE.y, BACKPACK_SIZE.z, 2, 2, 2), BACKPACK_POS, -1);
  }

  /** Hat / helmet: upper part of a sphere sitting on the crown, icon on the front. */
  function buildDome(o) {
    return buildStatic(new THREE.SphereGeometry(o.radius, 28, 18, 0, Math.PI * 2, 0, o.thetaLength), o.position, 1);
  }

  /** Mask: a front arc of a cylinder wall over the face, centred on +z. */
  function buildFacePlate(o) {
    return buildStatic(new THREE.CylinderGeometry(o.radius, o.radius, o.height, 28, 4, true, -o.arc / 2, o.arc), o.position, 1);
  }

  window.SkinShells = { buildShell, buildBackpack, buildDome, buildFacePlate };
})();
