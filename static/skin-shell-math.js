/* RustMetrics — Skin Checker: pure geometry/colour helpers for the 3D
   outfit preview. No three.js, no DOM — plain typed arrays in, plain
   typed arrays out — so the logic is unit-testable under node
   (see test_skin_shell_math.mjs) and the browser side (skin-shells.js,
   skin-icon-texture.js) stays a thin adapter. UMD-style export: node
   gets module.exports, the browser gets window.SkinShellMath. */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SkinShellMath = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const TWO_PI = Math.PI * 2;
  const OPAQUE_ALPHA_MIN = 128;   // icon pixels below this alpha are background
  const FALLBACK_GREY = [128, 128, 128];

  /** Axis-aligned bounds of a flat xyz position array. */
  function bounds(positions) {
    const min = [Infinity, Infinity, Infinity];
    const max = [-Infinity, -Infinity, -Infinity];
    for (let i = 0; i < positions.length; i += 3) {
      for (let a = 0; a < 3; a++) {
        const v = positions[i + a];
        if (v < min[a]) min[a] = v;
        if (v > max[a]) max[a] = v;
      }
    }
    return { min, max };
  }

  /** New position array pushed `amount` along each vertex normal. */
  function inflatePositions(positions, normals, amount) {
    const out = new Float32Array(positions.length);
    for (let i = 0; i < positions.length; i++) out[i] = positions[i] + normals[i] * amount;
    return out;
  }

  /**
   * Cylindrical UVs around the vertical axis through the bounds centre.
   * u = 0.5 faces `facing` (+1 → +z, the model's front; -1 → -z, its back),
   * the seam (u = 0/1) sits on the opposite side. v runs bottom → top.
   */
  function cylindricalUVs(positions, box, facing = 1) {
    const cx = (box.min[0] + box.max[0]) / 2;
    const cz = (box.min[2] + box.max[2]) / 2;
    const height = Math.max(box.max[1] - box.min[1], 1e-6);
    const count = positions.length / 3;
    const uv = new Float32Array(count * 2);
    for (let i = 0; i < count; i++) {
      const x = positions[i * 3] - cx;
      const y = positions[i * 3 + 1] - box.min[1];
      const z = (positions[i * 3 + 2] - cz) * facing;
      uv[i * 2] = 0.5 + Math.atan2(x, z) / TWO_PI;
      uv[i * 2 + 1] = y / height;
    }
    return uv;
  }

  /**
   * Keeps only the triangles of a NON-indexed triangle soup whose three
   * vertices all pass `keepVertex(x, y, z)`. Returns the list of kept
   * vertex indices (3 per triangle) into the original arrays.
   */
  function keptTriangleIndices(positions, keepVertex) {
    const kept = [];
    const triCount = Math.floor(positions.length / 9);
    for (let t = 0; t < triCount; t++) {
      const base = t * 3;
      let ok = true;
      for (let k = 0; k < 3 && ok; k++) {
        const p = (base + k) * 3;
        ok = keepVertex(positions[p], positions[p + 1], positions[p + 2]);
      }
      if (ok) kept.push(base, base + 1, base + 2);
    }
    return kept;
  }

  /** Copies the vertices listed in `indices` out of an interleaved array of `stride` floats. */
  function gatherVertices(source, indices, stride) {
    const out = new Float32Array(indices.length * stride);
    for (let i = 0; i < indices.length; i++) {
      const src = indices[i] * stride;
      for (let k = 0; k < stride; k++) out[i * stride + k] = source[src + k];
    }
    return out;
  }

  /**
   * Average colour of the opaque pixels in an RGBA byte array (an item
   * icon on a transparent background). Falls back to mid-grey when
   * nothing is opaque so a broken icon still yields a usable tint.
   */
  function dominantColor(rgba, alphaMin = OPAQUE_ALPHA_MIN) {
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < rgba.length; i += 4) {
      if (rgba[i + 3] < alphaMin) continue;
      r += rgba[i]; g += rgba[i + 1]; b += rgba[i + 2]; n++;
    }
    if (!n) return FALLBACK_GREY.slice();
    return [Math.round(r / n), Math.round(g / n), Math.round(b / n)];
  }

  return { bounds, inflatePositions, cylindricalUVs, keptTriangleIndices, gatherVertices, dominantColor };
});
