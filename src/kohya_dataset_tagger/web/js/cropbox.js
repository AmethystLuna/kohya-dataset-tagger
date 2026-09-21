/**
 * Pure crop-box geometry for the crop panel (section 4.17).
 *
 * Everything a crop tool has to get right is arithmetic: which rectangle a handle drag
 * produces when an aspect ratio is locked, what "the box stops at the image edge" means
 * for the anchor, what happens to the box when the next image has a different resolution,
 * and what "normalize the box area to the target size" rounds to. None of it needs the DOM,
 * so all of it lives here and is exercised by test/fixtures/crop_harness.mjs directly.
 *
 * Two coordinate systems meet in this file and they must not be confused:
 *   - **display coordinates** are the source image's pixels with EXIF orientation applied -
 *     what the dialog draws and what the API's crop rectangle means (core/scaler.py works in
 *     the same space);
 *   - **CSS pixels** are what the browser lays out, and the dialog converts between the two
 *     with the image element's own size. Nothing in this module knows about CSS.
 *
 * `clampRect` deliberately mirrors `core.scaler.CropRect.clamped`: the server clamps rather
 * than rejects, so the dialog must produce exactly the same rectangle for the same input or
 * the preview and the applied result could disagree.
 */

//: The eight drag handles, clockwise from the top-left.
export const CROP_HANDLES = Object.freeze(["nw", "n", "ne", "e", "se", "s", "sw", "w"]);

/**
 * The aspect-ratio presets.
 *
 * Numeric labels are the numerals everyone reads ("1:1" is "1:1" in every language), so they
 * are literals rather than locale keys; only the two named entries carry copy. `ratio: null`
 * means "not locked" - "free" leaves the current box shape alone and "source" resolves to the
 * image's own ratio at use time (see ratioOf).
 */
export const ASPECT_PRESETS = Object.freeze([
  { id: "free", labelKey: "crop.aspectFree", ratio: null },
  { id: "source", labelKey: "crop.aspectSource", ratio: null },
  { id: "1:1", label: "1:1", ratio: 1 },
  { id: "3:2", label: "3:2", ratio: 3 / 2 },
  { id: "2:3", label: "2:3", ratio: 2 / 3 },
  { id: "4:3", label: "4:3", ratio: 4 / 3 },
  { id: "3:4", label: "3:4", ratio: 3 / 4 },
  { id: "5:4", label: "5:4", ratio: 5 / 4 },
  { id: "4:5", label: "4:5", ratio: 4 / 5 },
  { id: "16:9", label: "16:9", ratio: 16 / 9 },
  { id: "9:16", label: "9:16", ratio: 9 / 16 },
]);

/** A frame is just the display size of one image: {width, height}. */
export function frameOf(width, height) {
  return { width: Math.max(1, Math.round(width)), height: Math.max(1, Math.round(height)) };
}

/** The locked ratio for a preset: null for free, the frame's own ratio for "source". */
export function ratioOf(presetId, frame) {
  const preset = ASPECT_PRESETS.find((item) => item.id === presetId);
  if (!preset || preset.ratio === null) {
    if (presetId === "source" && frame && frame.height > 0) return frame.width / frame.height;
    return null;
  }
  return preset.ratio;
}

/** The whole image as a rectangle. */
export function wholeFrame(frame) {
  return { x: 0, y: 0, width: frame.width, height: frame.height };
}

/**
 * Move the box inside the frame and shrink it when it is too large - one axis at a time,
 * exactly like the server's `CropRect.clamped`.
 */
export function clampRect(rect, frame) {
  const fw = Math.max(1, Math.round(frame.width));
  const fh = Math.max(1, Math.round(frame.height));
  const width = Math.min(Math.max(1, Math.round(rect.width)), fw);
  const height = Math.min(Math.max(1, Math.round(rect.height)), fh);
  const x = Math.min(Math.max(0, Math.round(rect.x)), fw - width);
  const y = Math.min(Math.max(0, Math.round(rect.y)), fh - height);
  return { x, y, width, height };
}

/**
 * Shrink the box **uniformly** until it fits, keeping its centre and its shape, then move it
 * inside. This is the one to use where the aspect ratio matters (a normalized box, a box
 * inherited from another resolution): clamping each axis on its own would silently change
 * the ratio, which is the one thing an aspect lock promises not to do.
 */
export function fitInside(rect, frame) {
  const fw = Math.max(1, Math.round(frame.width));
  const fh = Math.max(1, Math.round(frame.height));
  const scale = Math.min(1, fw / Math.max(1, rect.width), fh / Math.max(1, rect.height));
  const width = Math.max(1, Math.round(rect.width * scale));
  const height = Math.max(1, Math.round(rect.height * scale));
  const centreX = rect.x + rect.width / 2;
  const centreY = rect.y + rect.height / 2;
  const x = Math.min(Math.max(0, Math.round(centreX - width / 2)), fw - width);
  const y = Math.min(Math.max(0, Math.round(centreY - height / 2)), fh - height);
  return { x, y, width, height };
}

/** The largest rectangle with this ratio that fits the frame, centred. Without a ratio: the whole frame. */
export function largestWithRatio(frame, ratio) {
  const fw = Math.max(1, Math.round(frame.width));
  const fh = Math.max(1, Math.round(frame.height));
  if (!ratio || ratio <= 0) return { x: 0, y: 0, width: fw, height: fh };
  let width = fw;
  let height = Math.round(width / ratio);
  if (height > fh) {
    height = fh;
    width = Math.round(height * ratio);
  }
  width = Math.min(fw, Math.max(1, width));
  height = Math.min(fh, Math.max(1, height));
  return {
    x: Math.round((fw - width) / 2),
    y: Math.round((fh - height) / 2),
    width,
    height,
  };
}

/**
 * "One click: the crop box area becomes the target size" (the button PS does not have).
 *
 * The box keeps its ratio (the locked one, or its own when unlocked), keeps its centre, and
 * its area becomes `targetArea` = target_width x target_height. Combined with the area
 * normalization in the pipeline this is what makes the scale factor 1.0: the crop box already
 * has the target area, so the pixels are neither enlarged nor shrunk on the way out.
 *
 * When the frame cannot hold that area the box is shrunk to the largest one that fits, keeping
 * the ratio - and `clamped` says so, because "it did not take" is information the dialog owes
 * the user rather than something to discover later.
 */
export function normalizeArea(rect, ratio, targetArea, frame) {
  const area = Math.max(1, Math.round(targetArea));
  const aspect = ratio && ratio > 0 ? ratio : rect.height > 0 ? rect.width / rect.height : 1;
  const height = Math.sqrt(area / aspect);
  const width = height * aspect;
  const rounded = {
    x: Math.round(rect.x + rect.width / 2 - Math.round(width) / 2),
    y: Math.round(rect.y + rect.height / 2 - Math.round(height) / 2),
    width: Math.max(1, Math.round(width)),
    height: Math.max(1, Math.round(height)),
  };
  const fitted = fitInside(rounded, frame);
  return {
    rect: fitted,
    // A one-pixel rounding difference is not "clamped": only report a real reduction.
    clamped: fitted.width < rounded.width - 1 || fitted.height < rounded.height - 1,
  };
}

/**
 * One drag of one handle.
 *
 * The rule the whole interaction follows: **the opposite edge (or corner) does not move**.
 * With a ratio locked the box keeps that ratio - an edge drag grows the perpendicular axis
 * symmetrically around the centre (the Photoshop behaviour), a corner drag drives whichever
 * axis the user moved further and derives the other. The box never leaves the frame: the
 * allowed size is what fits between the anchor and the border, and a drag beyond it simply
 * stops instead of flipping the box around.
 */
export function resizeByHandle(rect, handle, dx, dy, options) {
  const opts = options || {};
  const frame = opts.frame || wholeFrame({ width: rect.width, height: rect.height });
  const ratio = opts.ratio && opts.ratio > 0 ? opts.ratio : null;
  const minSize = Math.max(1, Math.round(opts.minSize || 8));
  const minW = Math.min(minSize, frame.width);
  const minH = Math.min(minSize, frame.height);
  const movingW = handle.indexOf("w") >= 0;
  const movingE = handle.indexOf("e") >= 0;
  const movingN = handle.indexOf("n") >= 0;
  const movingS = handle.indexOf("s") >= 0;

  let left = rect.x;
  let top = rect.y;
  let right = rect.x + rect.width;
  let bottom = rect.y + rect.height;
  if (movingW) left += dx;
  if (movingE) right += dx;
  if (movingN) top += dy;
  if (movingS) bottom += dy;

  // A drag past the opposite edge stops at the minimum size instead of inverting the box.
  if (movingW) left = Math.min(left, right - minW);
  if (movingE) right = Math.max(right, left + minW);
  if (movingN) top = Math.min(top, bottom - minH);
  if (movingS) bottom = Math.max(bottom, top + minH);

  const anchorX = movingW ? right : left;
  const anchorY = movingN ? bottom : top;
  const centreX = (left + right) / 2;
  const centreY = (top + bottom) / 2;
  let width = right - left;
  let height = bottom - top;

  if (ratio) {
    const verticalEdge = (movingN || movingS) && !(movingW || movingE);
    const horizontalEdge = (movingW || movingE) && !(movingN || movingS);
    if (verticalEdge) {
      width = height * ratio;
    } else if (horizontalEdge) {
      height = width / ratio;
    } else if (width / ratio >= height) {
      height = width / ratio;
    } else {
      width = height * ratio;
    }
  }

  // How far the box may grow from the anchor before it would leave the frame. An edge drag
  // grows the perpendicular axis around the centre, so that axis is limited symmetrically.
  const limitW = movingW
    ? anchorX
    : movingE
      ? frame.width - anchorX
      : 2 * Math.min(centreX, frame.width - centreX);
  const limitH = movingN
    ? anchorY
    : movingS
      ? frame.height - anchorY
      : 2 * Math.min(centreY, frame.height - centreY);
  const scale = Math.min(1, limitW / width, limitH / height);
  if (scale < 1) {
    width *= scale;
    height *= scale;
  }
  if (ratio) {
    if (height < minH) {
      height = minH;
      width = height * ratio;
    }
    if (width < minW) {
      width = minW;
      height = width / ratio;
    }
  } else {
    width = Math.max(minW, width);
    height = Math.max(minH, height);
  }

  const x = movingW ? anchorX - width : movingE ? anchorX : centreX - width / 2;
  const y = movingN ? anchorY - height : movingS ? anchorY : centreY - height / 2;
  return fitInside(
    {
      x: Math.round(x),
      y: Math.round(y),
      width: Math.max(1, Math.round(width)),
      height: Math.max(1, Math.round(height)),
    },
    frame,
  );
}

/** Move the box by a delta, without resizing it. */
export function moveBy(rect, dx, dy, frame) {
  return clampRect({ x: rect.x + dx, y: rect.y + dy, width: rect.width, height: rect.height }, frame);
}

/**
 * The box carried over to the next image (§4.17): the same **relative** window, the same
 * centre, the same ratio.
 *
 * This is the rule the batch-crop world settled on (Lightroom's copy-settings and Capture
 * One's apply-crop both work this way): a pixel rectangle copied verbatim would land somewhere
 * else entirely on a different resolution, and "the same fraction of the frame" is what
 * somebody cropping a dataset actually means. When the ratio is locked the width fraction
 * drives and the height follows from the ratio, so the lock survives a frame whose aspect
 * ratio differs; without a lock each axis keeps its own fraction.
 *
 * Identical resolutions short-circuit to the same rectangle (the common case - a directory of
 * images from one generator).
 */
export function propagate(rect, ratio, fromFrame, toFrame) {
  if (!fromFrame || !toFrame) return rect;
  const sameWidth = Math.round(fromFrame.width) === Math.round(toFrame.width);
  const sameHeight = Math.round(fromFrame.height) === Math.round(toFrame.height);
  if (sameWidth && sameHeight) return clampRect(rect, toFrame);
  const fractionW = rect.width / Math.max(1, fromFrame.width);
  const fractionH = rect.height / Math.max(1, fromFrame.height);
  const centreX = (rect.x + rect.width / 2) / Math.max(1, fromFrame.width);
  const centreY = (rect.y + rect.height / 2) / Math.max(1, fromFrame.height);
  const width = Math.max(1, Math.round(fractionW * toFrame.width));
  const height = ratio && ratio > 0
    ? Math.max(1, Math.round(width / ratio))
    : Math.max(1, Math.round(fractionH * toFrame.height));
  return fitInside(
    {
      x: Math.round(centreX * toFrame.width - width / 2),
      y: Math.round(centreY * toFrame.height - height / 2),
      width,
      height,
    },
    toFrame,
  );
}

/** The lines the overlay draws and the drags snap to: the two thirds and the centre. */
export function guidesOf(frame) {
  const fw = Math.max(1, Math.round(frame.width));
  const fh = Math.max(1, Math.round(frame.height));
  return {
    vertical: [Math.round(fw / 3), Math.round(fw / 2), Math.round(2 * fw / 3)],
    horizontal: [Math.round(fh / 3), Math.round(fh / 2), Math.round(2 * fh / 3)],
  };
}

/**
 * How far a moving edge should jump to land on one of the targets, or 0 when none is close.
 * Snapping is what makes "crop exactly to the middle" possible with the mouse.
 */
export function snapDelta(value, targets, threshold) {
  let best = 0;
  let bestDistance = Math.abs(threshold) + 1;
  for (const target of targets || []) {
    const distance = Math.abs(target - value);
    if (distance <= Math.abs(threshold) && distance < bestDistance) {
      best = target - value;
      bestDistance = distance;
    }
  }
  return best;
}

/** "4:3" when the reduced terms stay readable, otherwise a decimal - for the readout line. */
export function formatRatio(width, height) {
  const w = Math.max(1, Math.round(width));
  const h = Math.max(1, Math.round(height));
  let a = w;
  let b = h;
  while (b) {
    const t = b;
    b = a % b;
    a = t;
  }
  const rw = Math.round(w / a);
  const rh = Math.round(h / a);
  if (rw <= 64 && rh <= 64) return rw + ":" + rh;
  return (w / h).toFixed(2) + ":1";
}

/** The area of a rectangle, as the normalize button and the readout use it. */
export function areaOf(rect) {
  return Math.max(0, Math.round(rect.width)) * Math.max(0, Math.round(rect.height));
}
