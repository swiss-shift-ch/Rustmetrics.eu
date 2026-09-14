/* RustMetrics — Skin Checker: turns a skin's 2D item icon into something
   the 3D preview can wear. The icon is a product render, not a texture
   atlas, so it can't wrap a mesh "correctly" — instead we (a) tint the
   whole garment with the icon's average colour and (b) print the icon
   itself on the garment's front via the shell's cylindrical UVs (see
   skin-shells.js). Honest stand-in, clearly labelled in the UI.
   Icons must be same-origin (/skins/icons/…, mirrored by
   backfill_skin_icons.py) — Steam's CDN sends no CORS headers, so a
   cross-origin image would taint the canvas and getImageData() throws.
   Depends on three.js (r128) and skin-shell-math.js. */
(() => {
  "use strict";

  const M = window.SkinShellMath;
  const TEXTURE_SIZE = 512;
  const SAMPLE_SIZE = 32;     // downscale for the colour average — plenty for a mean
  const iconCache = new Map(); // url -> Promise<HTMLImageElement|null>

  function loadIcon(url) {
    if (!url) return Promise.resolve(null);
    if (iconCache.has(url)) return iconCache.get(url);
    const p = new Promise((resolve) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = url;
    });
    iconCache.set(url, p);
    return p;
  }

  /** Average opaque colour of an icon as a THREE.Color (sRGB → linear for the material). */
  function dominantColor(img) {
    const c = document.createElement("canvas");
    c.width = SAMPLE_SIZE; c.height = SAMPLE_SIZE;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
    let rgb;
    try { rgb = M.dominantColor(ctx.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE).data); }
    catch (e) { rgb = M.dominantColor(new Uint8ClampedArray(0)); } // tainted canvas → grey
    const color = new THREE.Color(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255);
    return color.convertSRGBToLinear();
  }

  /**
   * Square canvas texture: flat tint everywhere, the icon printed in the
   * u/v window given by `print` ({ width: u-span centred on 0.5, v0, v1 }).
   * @returns {THREE.CanvasTexture}
   */
  function buildTexture(img, tintCss, print) {
    const c = document.createElement("canvas");
    c.width = TEXTURE_SIZE; c.height = TEXTURE_SIZE;
    const ctx = c.getContext("2d");
    ctx.fillStyle = tintCss;
    ctx.fillRect(0, 0, TEXTURE_SIZE, TEXTURE_SIZE);
    if (img && print) {
      const w = print.width * TEXTURE_SIZE;
      const h = (print.v1 - print.v0) * TEXTURE_SIZE;
      const x = (0.5 - print.width / 2) * TEXTURE_SIZE;
      const y = (1 - print.v1) * TEXTURE_SIZE; // canvas y grows downward, v grows upward
      ctx.drawImage(img, x, y, w, h);
    }
    const tex = new THREE.CanvasTexture(c);
    tex.encoding = THREE.sRGBEncoding;
    tex.wrapS = THREE.ClampToEdgeWrapping;
    tex.wrapT = THREE.ClampToEdgeWrapping;
    tex.anisotropy = 4;
    return tex;
  }

  window.SkinIconTexture = { loadIcon, dominantColor, buildTexture };
})();
