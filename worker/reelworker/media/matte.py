"""Person mask for presenter shots, so big words can sit BEHIND the presenter (as in the reference reels).

On the Mac it uses Apple's Vision person segmentation (free, built into macOS 12+, runs on the Neural Engine): one grey frame
per video frame, white = person, written as an mp4 the compositor reads next to the picture. When Vision is not available
(pyobjc missing, older macOS, Linux tests) make() returns None and the words are drawn in front instead, nothing breaks.
"""
import subprocess
from pathlib import Path
import cv2, numpy as np


def _vision():
    try:
        import Vision, Quartz  # noqa: F401  (pyobjc-framework-Vision, macOS only)
        return Vision, Quartz
    except Exception:  # noqa: BLE001
        return None


def _mask_vision(Vision, Quartz, bgr):
    import objc  # noqa: F401
    h, w = bgr.shape[:2]
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    prov = Quartz.CGDataProviderCreateWithData(None, rgba.tobytes(), rgba.nbytes, None)
    img = Quartz.CGImageCreate(w, h, 8, 32, w * 4, cs, Quartz.kCGImageAlphaPremultipliedLast, prov, None, False, Quartz.kCGRenderingIntentDefault)
    R = Vision.VNGeneratePersonSegmentationRequest.alloc()
    try:                                   # macOS 26 marks plain init unavailable; the completion-handler init works everywhere
        req = R.initWithCompletionHandler_(None)
    except Exception:  # noqa: BLE001
        req = R.init()
    req.setQualityLevel_(Vision.VNGeneratePersonSegmentationRequestQualityLevelAccurate)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok or not req.results():
        return None
    buf = req.results()[0].pixelBuffer()
    Quartz.CVPixelBufferLockBaseAddress(buf, 1)
    try:
        bw, bh, stride = Quartz.CVPixelBufferGetWidth(buf), Quartz.CVPixelBufferGetHeight(buf), Quartz.CVPixelBufferGetBytesPerRow(buf)
        raw = Quartz.CVPixelBufferGetBaseAddress(buf).as_buffer(stride * bh)
        m = np.frombuffer(raw, np.uint8).reshape(bh, stride)[:, :bw].copy()
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(buf, 1)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


def make(src: Path, out: Path, segment=None):
    """Grey person-mask video for src (same size and frame count). segment(bgr) -> uint8 mask; defaults to Apple Vision."""
    if out.exists() and out.stat().st_size > 0:
        return out
    if segment is None:
        v = _vision()
        if not v:
            return None
        segment = lambda fr: _mask_vision(*v, fr)  # noqa: E731
    cap = cv2.VideoCapture(str(src)); fps = cap.get(cv2.CAP_PROP_FPS) or 60
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    prev, n = None, 0
    try:
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            m = segment(fr)
            if m is None:
                raise RuntimeError("segmentation failed")
            if prev is not None:                       # a little temporal smoothing stops the edge from shimmering
                m = cv2.addWeighted(m, 0.7, prev, 0.3, 0)
            prev = m; n += 1
            enc.stdin.write(m.astype(np.uint8).tobytes())
    except Exception:  # noqa: BLE001
        enc.stdin.close(); enc.wait(); out.unlink(missing_ok=True)
        return None
    enc.stdin.close(); enc.wait()
    return out if enc.returncode == 0 and n else None


def available():
    """True when the person mask can be made here (Apple Vision on the Mac)."""
    return _vision() is not None


def _face_vision(Vision, Quartz, bgr):
    """Largest face as (x0, y0, x1, y1) fractions of the frame (top-left origin), or None."""
    h, w = bgr.shape[:2]
    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    prov = Quartz.CGDataProviderCreateWithData(None, rgba.tobytes(), rgba.nbytes, None)
    img = Quartz.CGImageCreate(w, h, 8, 32, w * 4, cs, Quartz.kCGImageAlphaPremultipliedLast, prov, None, False, Quartz.kCGRenderingIntentDefault)
    R = Vision.VNDetectFaceRectanglesRequest.alloc()
    try:
        req = R.initWithCompletionHandler_(None)
    except Exception:  # noqa: BLE001
        req = R.init()
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, err = handler.performRequests_error_([req], None)
    res = list(req.results() or []) if ok else []
    if not res:
        return None
    bb = max((r.boundingBox() for r in res), key=lambda b: b.size.width * b.size.height)
    x0, y0, bw, bh = bb.origin.x, 1 - bb.origin.y - bb.size.height, bb.size.width, bb.size.height     # Vision: origin bottom-left
    return (x0, y0, x0 + bw, y0 + bh)


def faces(src: Path, windows, detect=None, samples=4):
    """Face box per presenter window: {id: (x0, y0, x1, y1)} as fractions of the presenter frame, the union over a few frames
    of the window, widened for hair and head movement (Vision's box runs from the brows to the chin). windows =
    [(id, clip_t0, clip_t1)]. Returns None when face detection is not available here (Linux): the layout then uses a
    conservative default box."""
    if detect is None:
        v = _vision()
        if not v:
            return None
        detect = lambda fr: _face_vision(*v, fr)  # noqa: E731
    cap = cv2.VideoCapture(str(src)); fps = cap.get(cv2.CAP_PROP_FPS) or 30; n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = {}
    try:
        for wid, a, b in windows:
            box = None
            for k in range(samples):
                t = a + (b - a) * (k + 0.5) / samples
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(n - 1, int(t * fps)))); ok, fr = cap.read()
                f = detect(fr) if ok else None
                if f:
                    box = f if box is None else (min(box[0], f[0]), min(box[1], f[1]), max(box[2], f[2]), max(box[3], f[3]))
            if box:
                bw, bh = box[2] - box[0], box[3] - box[1]
                out[wid] = (max(0.0, box[0] - 0.18 * bw), max(0.0, box[1] - 0.45 * bh), min(1.0, box[2] + 0.18 * bw), min(1.0, box[3] + 0.12 * bh))
    finally:
        cap.release()
    return out
