---
name: chiaki-clip-embedder
description: Reference for the CLIP ViT-B/32 embedder used by the Chiaki gateway skill for PlayStation scene detection and learned navigation.
---

# Chiaki CLIP Embedder Reference

## Embedding Model

`openai/clip-vit-base-patch32` via Hugging Face `transformers`.

## Setup

```bash
pip3 install --break-system-packages transformers torch
```

The CLIP model downloads on first use from Hugging Face Hub (~600 MB cached under
`~/.cache/huggingface/`).

## Dimension

CLIP ViT-B/32 produces **512-dimensional** normalized embeddings.

## Compatibility Breaking Change

The previous embedder (`torchvision.models.resnet50`) produced **2048-dimensional**
embeddings. After switching to CLIP, all existing stored scene embeddings in
`~/.local/share/chiaki-remote-gateway/learning/scenes.json` are incompatible.
Re-learn all scenes with `remember-scene` and rebuild task routes with
`learn-task`.

## Integration

Used by `scripts/scene_learning.py` via the `TorchvisionEmbedder` class
(name retained for API compatibility despite the backend change).
The `.embed(image_path) -> list[float]` interface is unchanged.

## Dependency Check

```bash
python3 -c "
from transformers import CLIPModel, CLIPProcessor
model = CLIPModel.from_pretrained('openai/clip-vit-base-patch32')
print('CLIP OK — dim:', model.config.projection_dim)
"
```
