#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path
from typing import Any


BACKEND = "paddleocr-subprocess"
OCR_CACHE: dict[tuple[str, str], Any] = {}

# PaddleOCR 3.x + PaddlePaddle 3.3.x can hit a Windows CPU oneDNN/PIR
# NotImplementedError. Keep the OCR subprocess on the plain CPU path.
os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")


def _add_local_nvidia_dll_dirs() -> list[str]:
    site_packages = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not site_packages.exists():
        return []
    added: list[str] = []
    for dll in site_packages.rglob("*.dll"):
        directory = str(dll.parent)
        if directory in added:
            continue
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(directory)
            except OSError:
                continue
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
        added.append(directory)
    return added


LOCAL_NVIDIA_DLL_DIRS = _add_local_nvidia_dll_dirs()


def _normalise(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def _result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return _normalise(result)
    for attr in ("res", "dict"):
        if hasattr(result, attr):
            value = getattr(result, attr)
            if callable(value):
                value = value()
            if isinstance(value, dict):
                return _normalise(value)
    if hasattr(result, "json"):
        value = result.json
        if callable(value):
            value = value()
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return _normalise(parsed)
            except Exception:
                pass
        if isinstance(value, dict):
            return _normalise(value)
    return {"raw_type": type(result).__name__, "raw": str(result)}


def _bbox_from_box(box: Any) -> list[float] | None:
    box = _normalise(box)
    if not box:
        return None
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        return [float(v) for v in box]
    points: list[tuple[float, float]] = []
    if isinstance(box, list):
        for item in box:
            if isinstance(item, list) and len(item) >= 2 and isinstance(item[0], (int, float)) and isinstance(item[1], (int, float)):
                points.append((float(item[0]), float(item[1])))
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _extract_items(results: list[Any], max_items: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page_index, result in enumerate(results):
        if isinstance(result, list):
            for index, row in enumerate(result):
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    continue
                bbox = _bbox_from_box(row[0])
                text_payload = row[1]
                if isinstance(text_payload, (list, tuple)) and text_payload:
                    text = text_payload[0]
                    score = text_payload[1] if len(text_payload) > 1 else None
                else:
                    text = text_payload
                    score = None
                if text is None or str(text).strip() == "":
                    continue
                try:
                    confidence = float(score) if score is not None else None
                except Exception:
                    confidence = None
                items.append(
                    {
                        "text": str(text),
                        "confidence": confidence,
                        "bbox": bbox,
                        "page_index": page_index,
                    }
                )
                if len(items) >= max_items:
                    return items
            continue
        data = _result_to_dict(result)
        texts = data.get("rec_texts") or data.get("texts") or data.get("text") or []
        scores = data.get("rec_scores") or data.get("scores") or []
        boxes = data.get("rec_boxes") or data.get("dt_polys") or data.get("text_det_polys") or data.get("boxes") or []
        if isinstance(texts, str):
            texts = [texts]
        for index, text in enumerate(texts):
            if text is None or str(text).strip() == "":
                continue
            score = scores[index] if isinstance(scores, list) and index < len(scores) else None
            try:
                confidence = float(score) if score is not None else None
            except Exception:
                confidence = None
            bbox = _bbox_from_box(boxes[index]) if isinstance(boxes, list) and index < len(boxes) else None
            items.append(
                {
                    "text": str(text),
                    "confidence": confidence,
                    "bbox": bbox,
                    "page_index": page_index,
                }
            )
            if len(items) >= max_items:
                return items
    return items


def status() -> dict[str, Any]:
    import cv2
    import paddle
    import paddleocr

    return {
        "ready": True,
        "backend": BACKEND,
        "python": sys.executable,
        "paddle": getattr(paddle, "__version__", "unknown"),
        "paddleocr": getattr(paddleocr, "__version__", "unknown"),
        "cv2": getattr(cv2, "__version__", "unknown"),
        "compiled_cuda": bool(paddle.device.is_compiled_with_cuda()),
        "local_nvidia_dll_dirs": len(LOCAL_NVIDIA_DLL_DIRS),
    }


def _get_ocr(lang: str, device: str) -> Any:
    key = (lang or "ch", device or "")
    if key in OCR_CACHE:
        return OCR_CACHE[key]
    from paddleocr import PaddleOCR
    import paddleocr

    version = str(getattr(paddleocr, "__version__", ""))
    if version.startswith("2."):
        kwargs: dict[str, Any] = {
            "lang": key[0],
            "use_angle_cls": False,
            "show_log": False,
        }
        if key[1]:
            kwargs["use_gpu"] = key[1].lower().startswith("gpu")
    else:
        kwargs = {
            "lang": key[0],
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        }
        if key[1]:
            kwargs["device"] = key[1]
    ocr = PaddleOCR(**kwargs)
    OCR_CACHE[key] = ocr
    return ocr


def recognise(image_path: str, *, lang: str, max_items: int, device: str) -> dict[str, Any]:
    path = Path(image_path)
    if not path.exists():
        return {"ready": False, "backend": BACKEND, "error": f"image not found: {image_path}"}

    with contextlib.redirect_stdout(sys.stderr):
        ocr = _get_ocr(lang or "ch", device.strip())
        if hasattr(ocr, "predict"):
            results = ocr.predict(str(path))
        else:
            results = ocr.ocr(str(path), cls=False)

    if not isinstance(results, list):
        results = [results]
    items = _extract_items(results, max_items=max_items)
    return {
        "ready": True,
        "backend": BACKEND,
        "image_path": str(path),
        "lang": lang or "ch",
        "device": device or "default",
        "item_count": len(items),
        "items": items,
        "cache_size": len(OCR_CACHE),
    }


def handle_request(req: dict[str, Any]) -> dict[str, Any]:
    cmd = str(req.get("cmd") or "recognize")
    if cmd == "status":
        payload = status()
        payload["cache_size"] = len(OCR_CACHE)
        return payload
    if cmd == "recognize":
        return recognise(
            str(req.get("image") or ""),
            lang=str(req.get("lang") or "ch"),
            max_items=max(1, int(req.get("max_items") or 40)),
            device=str(req.get("device") or os.environ.get("GUI_OCR_DEVICE", "") or "").strip(),
        )
    if cmd == "exit":
        return {"ready": True, "backend": BACKEND, "exiting": True}
    return {"ready": False, "backend": BACKEND, "error": f"unknown cmd: {cmd}"}


def serve() -> int:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
            payload = handle_request(req if isinstance(req, dict) else {})
        except Exception as exc:
            payload = {
                "ready": False,
                "backend": BACKEND,
                "error": str(exc),
                "exception_type": type(exc).__name__,
            }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if payload.get("exiting"):
            return 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PaddleOCR JSON runner for gui_automation_mcp")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--image")
    parser.add_argument("--lang", default="ch")
    parser.add_argument("--device", default=os.environ.get("GUI_OCR_DEVICE", ""))
    parser.add_argument("--max-items", type=int, default=40)
    args = parser.parse_args()

    try:
        if args.serve:
            return serve()
        if args.status:
            payload = status()
        else:
            if not args.image:
                payload = {"ready": False, "backend": BACKEND, "error": "--image is required"}
            else:
                payload = recognise(args.image, lang=args.lang, max_items=max(1, args.max_items), device=args.device.strip())
    except Exception as exc:
        payload = {
            "ready": False,
            "backend": BACKEND,
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if payload.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
