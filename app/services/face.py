import numpy as np
import cv2
import base64
from cryptography.fernet import Fernet
from insightface.app import FaceAnalysis
from app.core.config import settings

_fernet = Fernet(settings.fernet_key.encode())
_app = FaceAnalysis(name="buffalo_s", providers=["CPUExecutionProvider"])
_app.prepare(ctx_id=-1, det_size=(320, 320))
THRESHOLD = 0.45


def decode(image_b64: str):
    data = base64.b64decode(image_b64.split(",")[-1])
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def embed(img):
    faces = _app.get(img)
    if len(faces) != 1:
        return None
    return faces[0].normed_embedding.astype(np.float32)


def encrypt(e: np.ndarray) -> bytes:
    return _fernet.encrypt(e.tobytes())


def decrypt(b: bytes) -> np.ndarray:
    return np.frombuffer(_fernet.decrypt(b), dtype=np.float32)


def verify(img, stored_enc: bytes):
    e = embed(img)
    if e is None:
        return False, 0.0
    score = float(np.dot(e, decrypt(stored_enc)))
    return score >= THRESHOLD, score