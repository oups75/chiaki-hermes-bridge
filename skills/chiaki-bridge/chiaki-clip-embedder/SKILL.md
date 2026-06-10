---
name: chiaki-clip-embedder
description: Reference for the CLIP ViT-L/14 embedder used by the Chiaki gateway skill for PlayStation scene detection and learned navigation.
---

# Chiaki CLIP Embedder Reference

## Embedding Model

`openai/clip-vit-large-patch14` via Hugging Face `transformers`.

Local evaluation on saved Chiaki screenshots selected this model: ViT-L/14 scored
`0.8261` top-1, ahead of SigLIP base at `0.7826` and CLIP ViT-B/32 at `0.7391`.

## Setup

```bash
pip3 install --break-system-packages transformers torch
```

The CLIP model downloads on first use from Hugging Face Hub and is cached under
`~/.cache/huggingface/`.

## Dimension

CLIP ViT-L/14 produces **768-dimensional** normalized embeddings.

## Compatibility Breaking Change

The previous embedder (`torchvision.models.resnet50`) produced **2048-dimensional**
embeddings. After switching models, stored scene embeddings with different
dimensions in `~/.local/share/chiaki-remote-gateway/learning/scenes.json` are
incompatible.
Re-learn all scenes with `remember-scene` and rebuild task routes with
`learn-task`.

## Transformers 5.9.0 Breaking Change — CLIP Feature Extraction

In transformers 5.x (confirmed on 5.9.0), `CLIPModel.get_image_features()` and
`get_text_features()` no longer return raw tensors. They return a
`BaseModelOutputWithPooling` dict-like object requiring explicit extraction:

```python
# WRONG — will raise AttributeError: 'BaseModelOutputWithPooling' object has no attribute 'norm'
image_features = model.get_image_features(**inputs).norm(dim=-1, keepdim=True)

# WRONG — .image_embeds does not exist either
image_features = model.get_image_features(**inputs).image_embeds

# CORRECT — extract pooler_output from the returned dict
out = model.get_image_features(**inputs)
image_features = out['pooler_output']
image_features = image_features / image_features.norm(dim=-1, keepdim=True)

# Same fix for text features
t_out = model.get_text_features(**text_inputs)
t_features = t_out['pooler_output']
t_features = t_features / t_features.norm(dim=-1, keepdim=True)
```

Both image and text branches return the same dict type. For CLIP cross-encoder use
(contrastive classification), always use `['pooler_output']`. `['last_hidden_state']`
returns the sequence output (patch tokens), not the pooled embedding used for CLIP
similarity.

## Integration

Used by `scripts/scene_learning.py` via the `TorchvisionEmbedder` class
(name retained for API compatibility despite the backend change).

The `.embed(image_path) -> list[float]` interface is unchanged. Ensure any
`.embed()` implementation uses `'pooler_output'` as shown above.

## Dependency Check

```bash
python3 -c "
from transformers import CLIPModel, CLIPProcessor
model = CLIPModel.from_pretrained('openai/clip-vit-large-patch14')
print('CLIP OK — dim:', model.config.projection_dim)
"
```
