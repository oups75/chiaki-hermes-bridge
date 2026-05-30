#!/usr/bin/env python3
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any


DEFAULT_STATE_ROOT = Path(
    os.environ.get(
        "CHIAKI_LEARNING_HOME",
        str(Path.home() / ".local/share/chiaki-remote-gateway/learning"),
    )
)


class SceneLearningError(RuntimeError):
    pass


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unnamed"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[index]


def update_timing(timing: dict[str, Any] | None, transition_ms: float) -> dict[str, Any]:
    timing = dict(timing or {})
    samples = list(timing.get("samples_ms", []))
    samples.append(round(float(transition_ms), 3))
    samples = samples[-50:]
    count = int(timing.get("sample_count", 0)) + 1
    old_avg = float(timing.get("avg_transition_ms", transition_ms))
    avg = old_avg + (float(transition_ms) - old_avg) / count
    timing.update(
        {
            "sample_count": count,
            "avg_transition_ms": round(avg, 3),
            "p95_transition_ms": round(percentile(samples, 95), 3),
            "samples_ms": samples,
        }
    )
    return timing


class TorchvisionEmbedder:
    """Embedder using openai/clip-vit-base-patch32 for image classification.

    Produces 512-dimensional normalized embeddings. Falls back gracefully
    if CLIP dependencies are missing.
    """

    def __init__(self):
        try:
            import torch
            from PIL import Image
            from transformers import CLIPModel, CLIPProcessor
        except Exception as exc:
            raise SceneLearningError(f"CLIP dependencies unavailable: {exc}") from exc

        self.torch = torch
        self.Image = Image
        model_name = "openai/clip-vit-base-patch32"
        self.model = CLIPModel.from_pretrained(model_name)
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.model.eval()

    def embed(self, image_path: Path) -> list[float]:
        pil_image = self.Image.open(str(image_path)).convert("RGB")
        inputs = self.processor(images=pil_image, return_tensors="pt")
        with self.torch.inference_mode():
            output = self.model.get_image_features(**inputs)
            # CLIPModel.get_image_features returns BaseModelOutputWithPooling;
            # use .image_embeds for the projected embedding.
            if hasattr(output, "image_embeds"):
                vector = output.image_embeds
            elif hasattr(output, "pooler_output"):
                vector = output.pooler_output
            else:
                vector = output.squeeze(0)
            vector = vector.squeeze(0).float()
            vector = vector / vector.norm().clamp_min(1e-12)
        return [round(float(value), 7) for value in vector.tolist()]


class DeepInfraClipClassifier:
    """Zero-shot CLIP classifier via DeepInfra API.

    Calls openai/clip-vit-base-patch32 on DeepInfra for zero-shot image
    classification against a set of candidate labels. Used as a fallback
    when local scene matching does not find a known scene.

    Requires DEEPINFRA_API_KEY in the environment.
    """

    DEEPINFRA_BASE = "https://api.deepinfra.com/v1/inference/openai/clip-vit-base-patch32"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("DEEPINFRA_API_KEY", "")
        if not self.api_key:
            raise SceneLearningError(
                "DEEPINFRA_API_KEY not set — set it in ~/.hermes/.env or "
                "export DEEPINFRA_API_KEY"
            )

    def classify(
        self,
        image_path: Path,
        candidate_labels: list[str],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        import base64

        import requests

        image_b64 = base64.b64encode(image_path.read_bytes()).decode()
        payload = {
            "input": {
                "image": image_b64,
                "candidate_labels": candidate_labels,
            }
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                self.DEEPINFRA_BASE,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise SceneLearningError(f"DeepInfra CLIP request failed: {exc}") from exc

        data = resp.json()
        scores: list[float] = data.get("scores", [])
        labels: list[str] = data.get("labels", candidate_labels)

        best_idx = max(range(len(scores)), key=lambda i: scores[i]) if scores else -1
        best_label = labels[best_idx] if best_idx >= 0 else "unknown"
        best_score = scores[best_idx] if best_idx >= 0 else 0.0

        return {
            "label": best_label,
            "score": round(best_score, 6),
            "all_scores": [
                {"label": lbl, "score": round(sc, 6)}
                for lbl, sc in zip(labels, scores)
            ],
        }


class TorchvisionExporter:
    """Export learned scenes to a torchvision-compatible dataset.

    Produces:
      - dataset.pt      — PyTorch tensor file with embeddings and label indices
      - labels.json     — label index → scene label mapping
      - metadata.json   — full export metadata (source, timestamp, scene count)

    The output can be loaded with torchvision.datasets.VisionDataset or
    directly with torch.load() for training pipelines.
    """

    def __init__(self, store: "LearningStore"):
        self.store = store

    def export(self, output_dir: Path, include_embeddings: bool = True) -> dict[str, Any]:
        import json as _json

        try:
            import torch
        except ImportError:
            raise SceneLearningError("torch not available for export") from None

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        scenes = self.store.scenes()
        if not scenes:
            return {"status": "empty", "count": 0, "output_dir": str(output_dir)}

        # Build label index
        unique_labels: list[str] = []
        label_index: dict[str, int] = {}
        for scene in scenes:
            lbl = scene["label"]
            if lbl not in label_index:
                label_index[lbl] = len(unique_labels)
                unique_labels.append(lbl)

        # Build tensors
        embedding_dim = len(scenes[0].get("embedding", []))
        embeddings_tensor = torch.zeros(len(scenes), embedding_dim)
        labels_tensor = torch.zeros(len(scenes), dtype=torch.long)
        ids_list: list[str] = []

        for i, scene in enumerate(scenes):
            emb = scene.get("embedding", [])
            if len(emb) == embedding_dim:
                embeddings_tensor[i] = torch.tensor(emb, dtype=torch.float32)
            labels_tensor[i] = label_index[scene["label"]]
            ids_list.append(scene.get("id", ""))

        dataset = {
            "embeddings": embeddings_tensor,
            "labels": labels_tensor,
            "scene_ids": ids_list,
        }

        # Save
        torch.save(dataset, output_dir / "dataset.pt")
        (output_dir / "labels.json").write_text(
            _json.dumps(label_index, indent=2) + "\n"
        )
        (output_dir / "metadata.json").write_text(
            _json.dumps(
                {
                    "source": str(self.store.root),
                    "exported_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                    "embedding_dim": embedding_dim,
                    "model": "openai/clip-vit-base-patch32",
                    "scene_count": len(scenes),
                    "label_count": len(unique_labels),
                },
                indent=2,
            )
            + "\n"
        )

        return {
            "status": "ok",
            "count": len(scenes),
            "label_count": len(unique_labels),
            "output_dir": str(output_dir),
            "files": ["dataset.pt", "labels.json", "metadata.json"],
        }


class LearningBuffer:
    """Background scene-learning buffer.

    Queues screenshots as PNG files. When the buffer reaches the configured
    threshold, flush() processes them in a subprocess so the gateway stays
    responsive. The optimum volume (threshold) should be tuned so that
    learning does not fire too often nor let the buffer grow unbounded.
    """

    DEFAULT_THRESHOLD = 5

    def __init__(self, root: Path | None = None):
        base = Path(root) if root else Path(DEFAULT_STATE_ROOT).expanduser()
        self.buffer_dir = base / "buffer"
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.threshold = int(
            os.environ.get("CHIAKI_BUFFER_THRESHOLD", str(self.DEFAULT_THRESHOLD))
        )

    def queue(self, png_path: Path) -> Path:
        """Copy a PNG screenshot into the buffer, return the queued path."""
        dest = self.buffer_dir / f"{int(time.time() * 1000)}.png"
        dest.write_bytes(png_path.read_bytes())
        return dest

    def size(self) -> int:
        return len(list(self.buffer_dir.glob("*.png")))

    def pop_all(self) -> list[Path]:
        """Return all queued PNG paths and empty the buffer."""
        paths = sorted(
            self.buffer_dir.glob("*.png"), key=lambda p: p.stat().st_mtime
        )
        result = []
        for p in paths:
            result.append(p)
        return result

    def clear(self) -> int:
        """Delete all buffered PNGs. Returns count removed."""
        paths = self.pop_all()
        count = 0
        for p in paths:
            p.unlink(missing_ok=True)
            count += 1
        return count

    def ready(self) -> bool:
        return self.size() >= self.threshold


class LearningStore:
    """Namespaced learning store — one model per game/application.

    Each namespace gets its own subdirectory under the learning root,
    allowing per-game scene embeddings, tasks, and feedback to stay
    isolated.  Shared data between related games (e.g. NHL common UI)
    lives in a separate namespace (e.g. 'nhl-common').

    Default namespace is 'ps' for general PlayStation system UI.
    """

    DEFAULT_NAMESPACE = "ps"

    def __init__(self, root: Path = DEFAULT_STATE_ROOT, namespace: str | None = None):
        self.root = root.expanduser()
        self.namespace = namespace or os.environ.get(
            "CHIAKI_LEARNING_NAMESPACE", self.DEFAULT_NAMESPACE
        )
        self.ns_root = self.root / self.namespace
        self.ns_root.mkdir(parents=True, exist_ok=True)
        self.scenes_path = self.ns_root / "scenes.json"
        self.tasks_path = self.ns_root / "tasks.json"
        self.pending_path = self.ns_root / "pending-learning.json"
        self.last_action_path = self.ns_root / "last-action.json"
        self.feedback_path = self.ns_root / "feedback.json"
        # Buffer is still shared across namespaces to avoid splitting
        # the threshold across namespaces; flush-learn routes to the
        # correct namespace via --namespace.
        self.buffer = LearningBuffer(root)

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise SceneLearningError(f"bad learning json: {path}: {exc}") from exc

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    @classmethod
    def namespaces(cls, root: Path = DEFAULT_STATE_ROOT) -> list[str]:
        """List all available namespace directories under the learning root."""
        root = root.expanduser()
        if not root.exists():
            return []
        return sorted(
            d.name
            for d in root.iterdir()
            if d.is_dir() and not d.name.startswith(".") and d.name != "buffer"
        )

    def scenes(self) -> list[dict[str, Any]]:
        return self._read_json(self.scenes_path, [])

    def tasks(self) -> dict[str, Any]:
        return self._read_json(self.tasks_path, {})

    def feedback(self) -> list[dict[str, Any]]:
        return self._read_json(self.feedback_path, [])

    def add_scene(self, label: str, embedding: list[float], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        scenes = self.scenes()
        scene_id = f"{slugify(label)}-{int(time.time())}"
        scene = {
            "id": scene_id,
            "label": label,
            "embedding": embedding,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "namespace": self.namespace,
            "metadata": metadata or {},
        }
        scenes.append(scene)
        self._write_json(self.scenes_path, scenes)
        return scene

    def match_multi(
        self,
        embedding: list[float],
        threshold: float = 0.88,
        extra_namespaces: list[str] | None = None,
    ) -> dict[str, Any]:
        """Match against this namespace + extra namespaces.

        For games that share a common UI (e.g. nhl-common for nhl26/nhl25),
        pass `extra_namespaces=['nhl-common']` to include shared scenes.
        Returns the best match across all searched namespaces.
        """
        best = self.match_scene(embedding, threshold)
        if best["matched"]:
            return best

        for ns in (extra_namespaces or []):
            other = LearningStore(self.root, namespace=ns)
            ns_match = other.match_scene(embedding, threshold)
            if ns_match["matched"] and ns_match["score"] > best["score"]:
                best = ns_match

        return best

    def match_scene(self, embedding: list[float], threshold: float = 0.88) -> dict[str, Any]:
        best: dict[str, Any] | None = None
        best_score = 0.0
        for scene in self.scenes():
            score = cosine(embedding, scene.get("embedding", []))
            if score > best_score:
                best = scene
                best_score = score
        return {
            "matched": best_score >= threshold and best is not None,
            "score": round(best_score, 6),
            "scene": {
                "id": best.get("id"),
                "label": best.get("label"),
                "namespace": best.get("namespace"),
                "metadata": best.get("metadata", {}),
            }
            if best
            else None,
        }

    def save_task(self, goal: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
        tasks = self.tasks()
        key = slugify(goal)
        task = {
            "goal": goal,
            "key": key,
            "namespace": self.namespace,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "steps": steps,
        }
        tasks[key] = task
        self._write_json(self.tasks_path, tasks)
        return task

    def get_task(self, goal: str) -> dict[str, Any] | None:
        return self.tasks().get(slugify(goal))

    def write_pending(self, payload: dict[str, Any]) -> None:
        self._write_json(self.pending_path, payload)

    def read_pending(self) -> dict[str, Any] | None:
        return self._read_json(self.pending_path, None)

    def clear_pending(self) -> None:
        if self.pending_path.exists():
            self.pending_path.unlink()

    def write_last_action(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._write_json(self.last_action_path, payload)

    def read_last_action(self) -> dict[str, Any] | None:
        return self._read_json(self.last_action_path, None)

    def add_feedback(
        self,
        sentiment: str,
        embedding: list[float],
        match: dict[str, Any],
        note: str = "",
    ) -> dict[str, Any]:
        items = self.feedback()
        item = {
            "id": f"{sentiment}-{int(time.time())}",
            "sentiment": sentiment,
            "namespace": self.namespace,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_action": self.read_last_action(),
            "match": match,
            "embedding": embedding,
            "note": note,
        }
        items.append(item)
        self._write_json(self.feedback_path, items[-500:])
        return item
