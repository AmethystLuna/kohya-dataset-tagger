"""A8: shape / value / channel order / padding criteria for `preprocess`.

The criteria come from the A8 row of p0-spec §5 + §3.6 + §1.2, **not** copied from the
implementation. The three easiest to get wrong:

1. **Channel order must be verified with a square image.** §1.2 measured pitfall 1: once a
   100x300 pure red image is padded to a square, 2/3 of it is white padding, and the B channel
   mean becomes 170.4 instead of 0. So BGR is asserted with a 64x64 square image, and a
   rectangular image is used separately to verify the padding value.
2. **No division by 255.** `max` must be 255.0, not 1.0.
3. **Padding is 255 (white), not 0 (black).** Padding with 0 would activate dark-background tags.

Everything uses synthetic images: no disk reads and no onnxruntime.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from kohya_dataset_tagger.autotag.wd14 import WD14_INPUT_SIZE, preprocess

SIZE = 448


def _rgb(size: tuple[int, int], colour: tuple[int, int, int]) -> Image.Image:
    return Image.new("RGB", size, colour)


# --------------------------------------------------------------------------
# Contract: shape / dtype / value range
# --------------------------------------------------------------------------


def test_shape_dtype_and_raw_scale() -> None:
    """shape (448,448,3) / float32 / max 255.0 (not divided by 255) / NHWC."""
    image = Image.new("RGB", (640, 480), (20, 20, 20))
    image.paste((255, 255, 255), (0, 0, 320, 240))
    image.paste((255, 0, 0), (320, 0, 640, 240))

    arr = preprocess(image)

    assert arr.shape == (SIZE, SIZE, 3)
    assert arr.dtype == np.float32
    assert arr.max() == 255.0, "preprocess must not divide by 255"
    assert arr.min() >= 0.0
    # NHWC: the last axis is the channel
    assert arr.shape[2] == 3


def test_default_size_matches_the_frozen_constant() -> None:
    assert WD14_INPUT_SIZE == 448
    assert preprocess(_rgb((64, 64), (0, 0, 0))).shape == (448, 448, 3)


def test_size_parameter_is_honoured() -> None:
    assert preprocess(_rgb((64, 64), (10, 20, 30)), size=224).shape == (224, 224, 3)
    assert preprocess(_rgb((1024, 1024), (10, 20, 30)), size=224).shape == (224, 224, 3)


# --------------------------------------------------------------------------
# Channel order: BGR. A square image must be used (no padding)
# --------------------------------------------------------------------------


def test_bgr_order_uses_a_square_red_image() -> None:
    """Pure red 64x64 (square => no padding) => B=0, G=0, R=255."""
    arr = preprocess(_rgb((64, 64), (255, 0, 0)))
    assert arr[:, :, 0].max() == 0.0, "ch0 is B: pure red must have B == 0 (if this were RGB it would be 255)"
    assert arr[:, :, 1].max() == 0.0
    assert arr[:, :, 2].min() == 255.0, "ch2 is R: pure red must have R == 255"


def test_bgr_order_square_blue_complement() -> None:
    """Pure blue 64x64 => ch0=255, ch2=0 (red and blue swapped, so the RGB order cannot be guessed right by luck)."""
    arr = preprocess(_rgb((64, 64), (0, 0, 255)))
    assert arr[:, :, 0].min() == 255.0
    assert arr[:, :, 2].max() == 0.0


def test_rectangular_red_image_shows_why_square_is_required() -> None:
    """Regression test for §1.2 pitfall 1: a 100x300 pure red image padded to 300x300 is 2/3 white padding.

    The B channel mean is therefore ~170 rather than 0. This test **deliberately** records that
    "a rectangular image cannot verify channel order", so nobody later changes it to "should equal 0".
    """
    arr = preprocess(_rgb((300, 100), (255, 0, 0)))
    blue_mean = float(arr[:, :, 0].mean())
    assert 164.0 <= blue_mean <= 177.0, "white padding is 2/3 => B mean ~170, measured %.1f" % blue_mean
    assert float(arr[:, :, 2].mean()) > 250.0


# --------------------------------------------------------------------------
# Padding: 255 (white) + centred
# --------------------------------------------------------------------------


def test_pad_is_white_and_exactly_centred() -> None:
    """Use `size == the padded side length` so the resize becomes 1:1, making it possible to assert
    the padding geometry **column by column**.

    A 101x200 image (width 101, height 200) padded to 200x200: pad_x = 99 => left 49 / right 50.
    """
    arr = preprocess(_rgb((101, 200), (255, 0, 0)), size=200)
    assert arr.shape == (200, 200, 3)

    white = np.array([255.0, 255.0, 255.0], dtype=np.float32)
    red = np.array([0.0, 0.0, 255.0], dtype=np.float32)

    assert np.array_equal(arr[:, 0:49], np.broadcast_to(white, (200, 49, 3))), "left padding 49 columns"
    assert np.array_equal(arr[:, 49:150], np.broadcast_to(red, (200, 101, 3))), "original image 101 columns"
    assert np.array_equal(arr[:, 150:200], np.broadcast_to(white, (200, 50, 3))), "right padding 50 columns"


def test_pad_is_255_not_black() -> None:
    """The padding value must be 255. Padding with 0 (black) would activate dark-background tags."""
    arr = preprocess(_rgb((300, 100), (255, 0, 0)))
    corner = arr[0, 0].tolist()
    assert corner == [255.0, 255.0, 255.0], "padding must be white 255, measured %r" % (corner,)
    # White padding is 255 in the B channel; with 0 padding this would drop to 0.
    assert float(arr[:, :, 0].mean()) > 100.0


def test_top_and_bottom_padding_geometry() -> None:
    """Vertical padding: a 200x101 image (width 200, height 101) padded to 200x200 => top 49 / bottom 50."""
    arr = preprocess(_rgb((200, 101), (0, 255, 0)), size=200)
    white = np.array([255.0, 255.0, 255.0], dtype=np.float32)
    assert np.array_equal(arr[0:49, :], np.broadcast_to(white, (49, 200, 3)))
    assert np.array_equal(arr[150:200, :], np.broadcast_to(white, (50, 200, 3)))
    assert arr[49:150, :, 1].min() == 255.0  # the green channel is in the middle


# --------------------------------------------------------------------------
# alpha -> white background
# --------------------------------------------------------------------------


def test_alpha_is_composited_onto_white() -> None:
    """Semi-transparent red composited onto white => (255,127,127) => BGR (127,127,255)."""
    arr = preprocess(Image.new("RGBA", (64, 64), (255, 0, 0, 128)))
    pixel = arr[0, 0].tolist()
    assert abs(pixel[0] - 127.0) <= 1.0, "B = the blend of white background and red, measured %r" % (pixel,)
    assert abs(pixel[1] - 127.0) <= 1.0
    assert pixel[2] == 255.0


def test_fully_transparent_becomes_white() -> None:
    arr = preprocess(Image.new("RGBA", (64, 64), (255, 0, 0, 0)))
    assert arr.min() == 255.0, "pixels with alpha=0 should become pure white background"


def test_la_mode_is_composited() -> None:
    arr = preprocess(Image.new("LA", (64, 64), (0, 0)))
    assert arr.min() == 255.0


def test_palette_transparency_in_info_is_composited() -> None:
    """P mode + info["transparency"]: this takes the `transparency in image.info` branch."""
    image = Image.new("P", (64, 64), 0)
    image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    image.info["transparency"] = 0
    assert "transparency" in image.info
    arr = preprocess(image)
    assert arr.shape == (448, 448, 3)
    assert arr.min() == 255.0, "palette index 0 is fully transparent => all white"


def test_opaque_rgba_keeps_colour() -> None:
    arr = preprocess(Image.new("RGBA", (64, 64), (255, 0, 0, 255)))
    assert arr[:, :, 0].max() == 0.0
    assert arr[:, :, 2].min() == 255.0


# --------------------------------------------------------------------------
# Other modes
# --------------------------------------------------------------------------


def test_grayscale_becomes_three_identical_channels() -> None:
    arr = preprocess(Image.new("L", (64, 64), 128))
    assert arr.shape == (448, 448, 3)
    assert arr[:, :, 0].min() == 128.0
    assert np.array_equal(arr[:, :, 0], arr[:, :, 1])
    assert np.array_equal(arr[:, :, 1], arr[:, :, 2])


def test_cmyk_is_converted() -> None:
    arr = preprocess(Image.new("CMYK", (64, 64), (0, 255, 255, 0)))
    assert arr.shape == (448, 448, 3)
    assert arr.dtype == np.float32


# --------------------------------------------------------------------------
# The two resize branches & no side effects
# --------------------------------------------------------------------------


def test_upscale_branch_preserves_pure_colour() -> None:
    """Padded side length < 448 => upscale (sd-scripts goes LANCZOS). 1x1 is the most extreme example."""
    arr = preprocess(_rgb((1, 1), (255, 0, 0)))
    assert arr.shape == (448, 448, 3)
    assert arr[:, :, 0].max() == 0.0
    assert arr[:, :, 2].min() == 255.0


def test_downscale_branch_preserves_pure_colour() -> None:
    """Side length >= 448 => downscale (sd-scripts goes cv2.INTER_AREA; this implementation uses PIL BOX)."""
    arr = preprocess(_rgb((1024, 1024), (255, 0, 0)))
    assert arr.shape == (448, 448, 3)
    assert arr[:, :, 0].max() == 0.0
    assert arr[:, :, 2].min() == 255.0


def test_exactly_448_square_is_a_no_op_resize() -> None:
    arr = preprocess(_rgb((448, 448), (255, 0, 0)))
    assert arr.shape == (448, 448, 3)
    assert arr[:, :, 0].max() == 0.0
    assert arr[:, :, 2].min() == 255.0


def test_input_image_is_not_mutated() -> None:
    image = _rgb((101, 200), (12, 34, 56))
    before = np.array(image).copy()
    preprocess(image)
    assert image.mode == "RGB"
    assert image.size == (101, 200)
    assert np.array_equal(np.array(image), before)


def test_returns_a_writable_contiguous_array() -> None:
    """ONNX takes this array as input, so it must be writable and contiguous (not a read-only PIL view)."""
    arr = preprocess(_rgb((64, 64), (1, 2, 3)))
    assert arr.flags["C_CONTIGUOUS"]
    assert arr.flags["WRITEABLE"]
