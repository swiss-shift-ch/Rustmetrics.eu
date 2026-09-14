// node --test test_skin_shell_math.mjs — unit tests for static/skin-shell-math.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const M = createRequire(import.meta.url)("./static/skin-shell-math.js");

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} !~ ${b}`);

test("bounds returns per-axis min and max of a flat xyz array", () => {
  const b = M.bounds(new Float32Array([0, 1, 2, -1, 5, 0, 3, -2, 1]));
  assert.deepEqual(b.min, [-1, -2, 0]);
  assert.deepEqual(b.max, [3, 5, 2]);
});

test("inflatePositions moves every vertex along its normal by the given amount", () => {
  const out = M.inflatePositions(new Float32Array([1, 0, 0]), new Float32Array([0, 1, 0]), 0.5);
  assert.deepEqual(Array.from(out), [1, 0.5, 0]);
});

test("cylindricalUVs puts the front-facing vertex at u=0.5 and the back at the seam", () => {
  const pos = new Float32Array([0, 0, 1,  0, 1, -1,  1, 0.5, 0,  -1, 0.5, 0]); // symmetric about the axis
  const uv = M.cylindricalUVs(pos, M.bounds(pos), 1);
  near(uv[0], 0.5); near(uv[1], 0);        // front, bottom
  near(Math.abs(uv[2] - 0.5), 0.5); near(uv[3], 1); // back (u=0 or 1), top
  near(uv[4], 0.75); near(uv[5], 0.5);     // right side, mid height
});

test("cylindricalUVs with facing=-1 turns the -z vertex into the front", () => {
  const pos = new Float32Array([0, 0, 1,  0, 0, -1]);
  const uv = M.cylindricalUVs(pos, M.bounds(pos), -1);
  near(uv[2], 0.5);
});

test("keptTriangleIndices drops triangles where any vertex fails the predicate", () => {
  // two triangles: first all y<1, second has one vertex at y=2
  const pos = new Float32Array([0,0,0, 1,0,0, 0,0,1,   0,0,0, 1,2,0, 0,0,1]);
  const kept = M.keptTriangleIndices(pos, (x, y) => y < 1);
  assert.deepEqual(kept, [0, 1, 2]);
});

test("gatherVertices copies interleaved records for the given indices", () => {
  const src = new Float32Array([0,0,0,0, 1,1,1,1, 2,2,2,2]);
  assert.deepEqual(Array.from(M.gatherVertices(src, [2, 0], 4)), [2,2,2,2, 0,0,0,0]);
});

test("dominantColor averages only opaque pixels", () => {
  const rgba = new Uint8ClampedArray([200, 0, 0, 255,  0, 200, 0, 255,  0, 0, 255, 10]);
  assert.deepEqual(M.dominantColor(rgba), [100, 100, 0]);
});

test("dominantColor falls back to grey when every pixel is transparent", () => {
  assert.deepEqual(M.dominantColor(new Uint8ClampedArray([9, 9, 9, 0])), [128, 128, 128]);
});
