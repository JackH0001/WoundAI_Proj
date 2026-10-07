# Lite face-screening asset

`haarcascade_frontalface_default.xml` is copied unmodified from OpenCV 4.13.0,
commit `fe38fc608f6acb8b68953438a62305d8318f4fcd`:
https://github.com/opencv/opencv/blob/fe38fc608f6acb8b68953438a62305d8318f4fcd/data/haarcascades/haarcascade_frontalface_default.xml

SHA-256: `0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0`
Size: 930127 bytes. The original Intel copyright, redistribution conditions,
and disclaimer are retained at the top of the XML and must remain distributed
with it. This is a server asset, not an additional iOS bundled model.

OpenCV 5.0.0.93's wheel is missing the cascade data files; see
https://github.com/opencv/opencv-python/issues/1244 . The app uses this explicit
versioned asset instead of depending on `cv2.data`. It verifies the digest and
refuses the image request on missing/corrupt assets or detector errors. The
public Lite profile forbids disabling the check. No runtime download occurs.

The classifier only provides limited frontal-face screening. It can miss faces,
small/background faces, occlusions, profiles, text, IDs, tattoos and other
identifiers. Passing this check does not certify deidentification. This change
does not implement face blurring, OCR redaction or validate detection accuracy.
Tests use synthetic images; actual image-container availability is a separate
release gate via `probe_lite_container.py`.
