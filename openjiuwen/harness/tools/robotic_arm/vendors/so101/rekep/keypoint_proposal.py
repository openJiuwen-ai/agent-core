# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DINOv2 + MobileSAM keypoint proposal (ReKep paper appendix A.5).

Ported from a user-supplied ReKep-on-SO101 reference implementation
(``run_record_skills/core/keypoint_proposal.py``); logic unchanged, ``print``
calls replaced with ``tool_logger``, and the heavy ML imports (torch,
transformers, scikit-learn, mobile_sam) moved from module scope into
:meth:`KeypointProposer.__init__`/methods so importing this module never
requires the ``robotic-arm-so101-rekep`` extra -- only *constructing*
``KeypointProposer`` does.

Pipeline: RGB -> DINOv2 dense patch features -> MobileSAM object masks ->
per-mask PCA(3) + k-means -> project candidate pixels to 3D via an RGB-D
point array -> MeanShift merge -> workspace filter -> numbered overlay for
the constraint-generation VLM.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from openjiuwen.core.common.logging import tool_logger

_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]
_PATCH = 14  # DINOv2 ViT-S/14 patch size

# Fixed palette (cycled by mask index) for the segmentation overlay -- deterministic
# across calls, unlike random colors, so the same object gets a stable color run to run.
_MASK_COLORS = [
    (255, 99, 71),
    (60, 179, 113),
    (65, 105, 225),
    (238, 130, 238),
    (255, 215, 0),
    (0, 206, 209),
    (255, 140, 0),
    (147, 112, 219),
]


class KeypointProposer:
    @staticmethod
    def _select_device(device: Optional[str], torch_module: Any) -> str:
        """Pick a torch device: explicit override, else CUDA, else MPS, else CPU.

        Takes the already-imported ``torch`` module as a parameter so this
        selection logic can be unit-tested with a fake module, without
        requiring torch to be installed to import this file.
        """
        if device is not None:
            return device
        if torch_module.cuda.is_available():
            return "cuda"
        if torch_module.backends.mps.is_available():
            return "mps"
        return "cpu"

    def __init__(
        self,
        *,
        sam_checkpoint_path: str,
        device: Optional[str] = None,
        dino_model: str = "facebook/dinov2-with-registers-small",
        k_per_mask: int = 5,
        meanshift_bandwidth_m: float = 0.03,
        target_long_side: int = 756,
        min_mask_pixels: int = 300,
        workspace_bounds: Optional[tuple[Sequence[float], Sequence[float]]] = None,
    ) -> None:
        try:
            import torch
            import torchvision.transforms as torch_transforms
            from mobile_sam import SamAutomaticMaskGenerator, sam_model_registry
            from transformers import AutoModel
        except ImportError as e:
            raise ImportError(
                "torch/transformers/mobile_sam are not installed; run "
                f"`pip install 'openjiuwen[robotic-arm-so101-rekep]'` ({e})"
            ) from e

        self._torch = torch
        self.device = self._select_device(device, torch)
        self.k = k_per_mask
        self.bandwidth_m = meanshift_bandwidth_m
        self.target_long = target_long_side
        self.min_mask_pixels = min_mask_pixels
        self.bounds = workspace_bounds

        tool_logger.info("[KeypointProposer] loading DINOv2 (%s) on %s", dino_model, self.device)
        self.dino = AutoModel.from_pretrained(dino_model).to(self.device).eval()
        self.n_register = getattr(self.dino.config, "num_register_tokens", 0)

        tool_logger.info("[KeypointProposer] loading MobileSAM from %s", sam_checkpoint_path)
        sam = sam_model_registry["vit_t"](checkpoint=sam_checkpoint_path)
        sam.to("cpu")
        sam.eval()
        self.sam_generator = SamAutomaticMaskGenerator(sam)

        self._tf = torch_transforms.Compose(
            [
                torch_transforms.ToTensor(),
                torch_transforms.Normalize(mean=_IMAGENET_MEAN, std=_IMAGENET_STD),
            ]
        )

    # -- 1. DINOv2 dense features ---------------------------------------------

    def _get_features(self, rgb: np.ndarray) -> np.ndarray:
        """rgb [H,W,3] uint8 -> features [H,W,D] float32 (upsampled)."""
        torch = self._torch
        h0, w0 = rgb.shape[:2]
        scale = self.target_long / max(h0, w0)
        h = max(_PATCH, int(round(h0 * scale)) // _PATCH * _PATCH)
        w = max(_PATCH, int(round(w0 * scale)) // _PATCH * _PATCH)

        img = Image.fromarray(rgb).resize((w, h), Image.BILINEAR)
        x = self._tf(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            out = self.dino(x).last_hidden_state[0]  # [1 + R + N, D]

        n_skip = 1 + self.n_register
        patch_tokens = out[n_skip:]
        gh, gw = h // _PATCH, w // _PATCH
        feat = patch_tokens.reshape(gh, gw, -1).permute(2, 0, 1).unsqueeze(0)
        feat = torch.nn.functional.interpolate(feat, size=(h0, w0), mode="bilinear", align_corners=False)
        return feat[0].permute(1, 2, 0).float().cpu().numpy()

    # -- 2. SAM masks ----------------------------------------------------------

    def _get_masks(self, rgb: np.ndarray) -> list[np.ndarray]:
        results = self.sam_generator.generate(rgb)
        masks = []
        for r in results:
            m = np.asarray(r["segmentation"]).astype(bool)
            if m.sum() >= self.min_mask_pixels:
                masks.append(m)
        return masks

    # -- 3. per-mask PCA + k-means -> candidate pixels --------------------------

    def _cluster(self, features: np.ndarray, masks: list[np.ndarray]) -> list[tuple[int, int, int]]:
        """-> list of (py, px, mask_id)."""
        from sklearn.cluster import KMeans
        from sklearn.decomposition import PCA

        candidates = []
        for mid, m in enumerate(masks):
            ys, xs = np.where(m)
            if len(ys) < self.k:
                continue
            feats = features[ys, xs]

            if feats.shape[0] > 3 and feats.shape[1] > 3:
                feats = PCA(n_components=3).fit_transform(feats)

            k = min(self.k, feats.shape[0])
            km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(feats)

            for c in range(k):
                idx = np.where(km.labels_ == c)[0]
                if len(idx) == 0:
                    continue
                centroid = km.cluster_centers_[c]
                dists = np.linalg.norm(feats[idx] - centroid, axis=1)
                best = idx[np.argmin(dists)]
                candidates.append((int(ys[best]), int(xs[best]), mid))
        return candidates

    # -- 4. project to 3D via RGB-D points array --------------------------------

    @staticmethod
    def _project(
        candidates: list[tuple[int, int, int]], points: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """candidates [(py,px,mid)] + points [H,W,3] -> (pts3d [M,3], px [M,2], mids [M])."""
        pts3d, px, mids = [], [], []
        for py, px_, mid in candidates:
            p = points[py, px_]
            if p is None or np.any(~np.isfinite(p)):
                continue
            pts3d.append(p)
            px.append((px_, py))
            mids.append(mid)
        return np.array(pts3d), np.array(px), np.array(mids)

    # -- 5. workspace filter + MeanShift merge in 3D ----------------------------

    def _merge(self, pts3d: np.ndarray, px: np.ndarray, mids: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        from sklearn.cluster import MeanShift

        if len(pts3d) == 0:
            return pts3d, px, mids

        if self.bounds is not None:
            lo, hi = np.asarray(self.bounds[0]), np.asarray(self.bounds[1])
            keep = np.all((pts3d >= lo) & (pts3d <= hi), axis=1)
            pts3d, px, mids = pts3d[keep], px[keep], mids[keep]
            if len(pts3d) == 0:
                return pts3d, px, mids

        ms = MeanShift(bandwidth=self.bandwidth_m, bin_seeding=True).fit(pts3d)
        labels = ms.labels_
        merged_3d, merged_px, merged_mid = [], [], []
        for lab in np.unique(labels):
            idx = np.where(labels == lab)[0]
            merged_3d.append(pts3d[idx].mean(axis=0))
            merged_px.append(px[idx][len(idx) // 2])
            merged_mid.append(np.bincount(mids[idx]).argmax())
        return np.array(merged_3d), np.array(merged_px), np.array(merged_mid)

    # -- 6a. segmentation overlay for debugging/UI (SAM masks, not sent to the VLM) --

    @staticmethod
    def _segmentation_overlay(rgb: np.ndarray, masks: list[np.ndarray]) -> np.ndarray:
        """Alpha-blend each SAM mask over ``rgb`` in a distinct color from ``_MASK_COLORS``."""
        img = rgb.astype(np.float32).copy()
        alpha = 0.45
        for i, m in enumerate(masks):
            color = np.array(_MASK_COLORS[i % len(_MASK_COLORS)], dtype=np.float32)
            img[m] = img[m] * (1 - alpha) + color * alpha
        return img.astype(np.uint8)

    # -- 6b. numbered overlay for the VLM ----------------------------------------

    @staticmethod
    def _overlay(rgb: np.ndarray, pixels: np.ndarray) -> np.ndarray:
        img = Image.fromarray(rgb.copy())
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
        except Exception:
            font = ImageFont.load_default()
        for i, (x, y) in enumerate(pixels):
            r = 10
            draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 0, 0), outline=(255, 255, 255), width=2)
            draw.text((x + r + 2, y - r), str(i), fill=(255, 255, 0), font=font)
        return np.array(img)

    # -- orchestration -----------------------------------------------------------

    def get_keypoints(self, rgb: np.ndarray, points: np.ndarray, visualize: bool = True) -> dict[str, Any]:
        """
        Args:
            rgb: ``[H,W,3]`` uint8.
            points: ``[H,W,3]`` world-frame XYZ per pixel (from RGB-D backprojection).
            visualize: whether to render the numbered overlay image.

        Returns:
            ``{"keypoints_3d": [K,3], "pixels": [K,2], "mask_ids": [K], "overlay": [H,W,3] | None,
            "segmentation_overlay": [H,W,3] | None}``. ``overlay`` is the numbered-keypoint image sent
            to the VLM; ``segmentation_overlay`` is the SAM masks alpha-blended in distinct colors,
            for debugging/UI only -- the VLM never sees it.
        """
        rgb = np.asarray(rgb)
        max_side = 1280
        h0, w0 = rgb.shape[:2]
        if max(h0, w0) > max_side:
            scale = max_side / max(h0, w0)
            new_h, new_w = int(h0 * scale), int(w0 * scale)
            rgb = np.array(Image.fromarray(rgb).resize((new_w, new_h), Image.BILINEAR))
            tool_logger.info("[KeypointProposer] resized image %sx%s -> %sx%s", w0, h0, new_w, new_h)

        feats = self._get_features(rgb)
        masks = self._get_masks(rgb)
        tool_logger.info("[KeypointProposer] %s masks above size threshold", len(masks))

        candidates = self._cluster(feats, masks)
        tool_logger.info("[KeypointProposer] %s raw candidates from clustering", len(candidates))

        pts3d, px, mids = self._project(candidates, points)
        pts3d, px, mids = self._merge(pts3d, px, mids)
        overlay = self._overlay(rgb, px) if visualize else None
        segmentation_overlay = self._segmentation_overlay(rgb, masks) if visualize else None
        tool_logger.info("[KeypointProposer] %s final keypoints after merge + filter", len(pts3d))
        return {
            "keypoints_3d": pts3d,
            "pixels": px,
            "mask_ids": mids,
            "overlay": overlay,
            "segmentation_overlay": segmentation_overlay,
        }


__all__ = ["KeypointProposer"]
