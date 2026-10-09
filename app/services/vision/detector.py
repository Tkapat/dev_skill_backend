import numpy as np
import torch
from torchvision.ops import batched_nms
from ultralytics import YOLO, YOLOWorld

COCO = {"person", "chair", "laptop", "tv", "keyboard", "mouse", "cell phone", "bench", "couch", "dining table", "bottle", "book"}


class Detector:
    def __init__(self, classes: list[str], conf: float = 0.35, tiling: bool = True):
        self.classes, self.conf, self.tiling = classes, conf, tiling
        if all(c in COCO for c in classes):
            self.model = YOLO("yolo11n.pt")
            self.version = "yolo11n"
        else:
            self.model = YOLOWorld("yolov8s-worldv2.pt")
            self.model.set_classes(classes)
            self.version = "yolov8s-worldv2"
        self.names = None

    def _predict(self, img, ox=0, oy=0):
        r = self.model.predict(img, conf=self.conf, verbose=False)[0]
        names = r.names
        out = []
        for b in r.boxes:
            name = names[int(b.cls)]
            if name not in self.classes:
                continue
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            out.append((x1 + ox, y1 + oy, x2 + ox, y2 + oy, float(b.conf), name))
        return out

    def detect(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        dets = self._predict(frame)
        if self.tiling:
            tw, th, ov = int(w * 0.6), int(h * 0.6), 0.2
            for ox in (0, w - tw):
                for oy in (0, h - th):
                    dets += self._predict(frame[oy:oy + th, ox:ox + tw], ox, oy)
        if not dets:
            return []
        boxes = torch.tensor([d[:4] for d in dets])
        scores = torch.tensor([d[4] for d in dets])
        cls_ids = torch.tensor([self.classes.index(d[5]) for d in dets])
        keep = batched_nms(boxes, scores, cls_ids, 0.5).tolist()
        return [dets[i] for i in keep]