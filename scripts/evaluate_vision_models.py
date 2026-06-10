#!/usr/bin/env python3
import argparse
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from PIL import Image


DEFAULT_LEARNING_ROOT = Path.home() / ".local/share/chiaki-remote-gateway/learning"
DEFAULT_MODELS = [
    "openai/clip-vit-base-patch32",
    "openai/clip-vit-large-patch14",
    "google/siglip-base-patch16-224",
]


def normalize_label(value: str) -> str:
    value = value.strip().lower().replace("_", "-")
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def filename_label(path: Path) -> str | None:
    stem = path.stem
    if not stem.startswith("sets_"):
        return None
    label = stem[len("sets_") :]
    label = re.sub(r"_\d{4}-\d{2}-\d{2}t.*$", "", label, flags=re.IGNORECASE)
    label = re.sub(r"_\d{4}-\d{2}-\d{2}T.*$", "", label)
    label = re.sub(r"_20\d\d-.*$", "", label)
    return normalize_label(f"sets-{label}") if label else None


def sidecar_label(data: dict[str, Any]) -> str | None:
    label = data.get("label")
    page = data.get("page")
    for value in (label, page):
        if isinstance(value, str) and value.strip() and value.strip().lower() != "unknown":
            return normalize_label(value)
    return None


def load_records(root: Path, include_filename_labels: bool) -> list[dict[str, str]]:
    screenshots_root = root / "screenshots"
    records: dict[Path, str] = {}

    for sidecar in screenshots_root.glob("*/*.json"):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        label = sidecar_label(data)
        if not label:
            continue
        screenshot = data.get("screenshot")
        candidates = []
        if isinstance(screenshot, str):
            candidates.append(root / screenshot)
        candidates.append(sidecar.with_suffix(".png"))
        for candidate in candidates:
            if candidate.exists():
                records[candidate.resolve()] = label
                break

    if include_filename_labels:
        for png in screenshots_root.glob("*/*.png"):
            label = filename_label(png)
            if label:
                records.setdefault(png.resolve(), label)

    return [
        {"path": str(path), "label": label}
        for path, label in sorted(records.items(), key=lambda item: str(item[0]))
    ]


class ImageEmbedder:
    def __init__(self, model_name: str, device: str):
        import torch
        from transformers import AutoImageProcessor, AutoModel, CLIPImageProcessor

        self.torch = torch
        self.device = device
        if model_name.startswith("openai/clip-"):
            self.processor = CLIPImageProcessor()
        else:
            self.processor = AutoImageProcessor.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name).to(device)
        self.model.eval()

    def embed_batch(self, paths: list[Path], batch_size: int) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(paths), batch_size):
            batch = paths[start : start + batch_size]
            images = [Image.open(path).convert("RGB") for path in batch]
            inputs = self.processor(images=images, return_tensors="pt")
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            with self.torch.inference_mode():
                if hasattr(self.model, "get_image_features"):
                    output = self.model.get_image_features(**inputs)
                else:
                    model_output = self.model(**inputs)
                    output = getattr(model_output, "pooler_output", None)
                    if output is None:
                        output = model_output.last_hidden_state[:, 0]
                if hasattr(output, "pooler_output"):
                    output = output.pooler_output
                output = output.float()
                output = output / output.norm(dim=-1, keepdim=True).clamp_min(1e-12)
            vectors.extend([[round(float(value), 7) for value in row] for row in output.cpu()])
        return vectors


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def evaluate(records: list[dict[str, str]], vectors: list[list[float]]) -> dict[str, Any]:
    label_counts = Counter(record["label"] for record in records)
    eligible = [idx for idx, record in enumerate(records) if label_counts[record["label"]] >= 2]
    by_label: dict[str, list[bool]] = defaultdict(list)
    mistakes = []
    top_scores = []
    same_scores = []
    other_scores = []
    margins = []
    correct = 0

    for idx in eligible:
        label = records[idx]["label"]
        sims = []
        for other_idx, other_record in enumerate(records):
            if idx == other_idx:
                continue
            score = dot(vectors[idx], vectors[other_idx])
            sims.append((score, other_record["label"], other_record["path"]))
        sims.sort(reverse=True, key=lambda item: item[0])
        same = [item for item in sims if item[1] == label]
        other = [item for item in sims if item[1] != label]
        if not sims or not same:
            continue
        pred_score, pred_label, pred_path = sims[0]
        is_correct = pred_label == label
        correct += int(is_correct)
        by_label[label].append(is_correct)
        top_scores.append(pred_score)
        same_scores.append(same[0][0])
        if other:
            other_scores.append(other[0][0])
            margins.append(same[0][0] - other[0][0])
        if not is_correct and len(mistakes) < 12:
            mistakes.append(
                {
                    "label": label,
                    "predicted": pred_label,
                    "score": round(pred_score, 4),
                    "file": Path(records[idx]["path"]).name,
                    "nearest": Path(pred_path).name,
                }
            )

    label_accuracy = {
        label: sum(values) / len(values)
        for label, values in sorted(by_label.items())
    }
    return {
        "records": len(records),
        "labels": len(label_counts),
        "eligible_records": len(eligible),
        "eligible_labels": sum(1 for count in label_counts.values() if count >= 2),
        "top1_accuracy": round(correct / len(eligible), 4) if eligible else None,
        "macro_accuracy": round(statistics.mean(label_accuracy.values()), 4) if label_accuracy else None,
        "mean_top_score": round(statistics.mean(top_scores), 4) if top_scores else None,
        "mean_same_label_score": round(statistics.mean(same_scores), 4) if same_scores else None,
        "mean_nearest_other_score": round(statistics.mean(other_scores), 4) if other_scores else None,
        "mean_margin": round(statistics.mean(margins), 4) if margins else None,
        "label_accuracy": {label: round(value, 4) for label, value in label_accuracy.items()},
        "mistakes": mistakes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate vision embedding models on Chiaki learning screenshots.")
    parser.add_argument("--learning-root", type=Path, default=DEFAULT_LEARNING_ROOT)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--no-filename-labels", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    records = load_records(args.learning_root, include_filename_labels=not args.no_filename_labels)
    if not records:
        print(json.dumps({"ok": False, "error": "no labeled screenshots found"}, indent=2))
        return 2

    payload: dict[str, Any] = {
        "ok": True,
        "learning_root": str(args.learning_root),
        "dataset": {
            "records": len(records),
            "labels": len(set(record["label"] for record in records)),
            "label_counts": dict(Counter(record["label"] for record in records).most_common()),
        },
        "models": {},
    }

    for model_name in args.models:
        started = time.time()
        try:
            embedder = ImageEmbedder(model_name, args.device)
            vectors = embedder.embed_batch([Path(record["path"]) for record in records], args.batch_size)
            result = evaluate(records, vectors)
            result["seconds"] = round(time.time() - started, 2)
            result["embedding_dim"] = len(vectors[0]) if vectors else 0
            payload["models"][model_name] = result
        except Exception as exc:
            payload["models"][model_name] = {
                "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - started, 2),
            }

    ranked = [
        (name, result)
        for name, result in payload["models"].items()
        if isinstance(result.get("top1_accuracy"), (int, float))
    ]
    ranked.sort(
        key=lambda item: (
            item[1].get("top1_accuracy") or 0,
            item[1].get("macro_accuracy") or 0,
            item[1].get("mean_margin") or -999,
        ),
        reverse=True,
    )
    if ranked:
        payload["best_model"] = ranked[0][0]
        payload["ranking"] = [name for name, _ in ranked]

    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
