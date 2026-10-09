import cv2


def blurred_thumbnail(frame, person_boxes, annotate_boxes=(), width=640) -> bytes:
    out = frame.copy()
    H, W = out.shape[:2]
    for x1, y1, x2, y2 in person_boxes:
        x1, y1, x2, y2 = max(0, int(x1)), max(0, int(y1)), min(W, int(x2)), min(H, int(y2))
        hy2 = y1 + int(0.28 * (y2 - y1))
        roi = out[y1:hy2, x1:x2]
        if roi.size:
            k = max(9, ((x2 - x1) // 5) | 1)
            out[y1:hy2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)
    for x1, y1, x2, y2 in annotate_boxes:
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), (79, 70, 229), 2)
    scale = width / out.shape[1]
    out = cv2.resize(out, (width, int(out.shape[0] * scale)))
    _ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 60])
    return buf.tobytes()
