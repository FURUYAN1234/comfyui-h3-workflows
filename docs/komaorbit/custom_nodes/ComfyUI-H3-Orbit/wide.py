"""Build a masked wide canvas and restore known pixels after background generation."""
import torch
import torch.nn.functional as F

from .core import validate_geometry

WIDE_PROMPT = (
    "Outpaint the scene. Fill every gray area surrounding the central picture with a natural "
    "continuation of its background. Continue the same ground, buildings, sky, perspective, "
    "lighting, drawing style and colors. Keep the central picture at its current position and size. "
    "The center is the existing artwork; only its surroundings are missing. Do not enlarge, "
    "move or duplicate the central subjects. Do not add people, text, borders or a second panel. "
    "Make one continuous full-frame environment, with no gray canvas, black void or empty border."
)


def canvas_parts(image, geometry):
    if image.ndim != 4 or image.shape[0] != 1 or image.shape[-1] != 3:
        raise ValueError("背景補完には準備済みのRGB画像を一枚接続してください。")
    g = geometry
    validate_geometry(g, image.shape[2], image.shape[1], g['source_width'], g['source_height'])
    width, height = max(1, round(g['content_width'] / 2)), max(1, round(g['content_height'] / 2))
    left = g['left'] + (g['content_width'] - width) // 2
    top = g['top'] + (g['content_height'] - height) // 2
    content = image[:, g['top']:g['top']+g['content_height'], g['left']:g['left']+g['content_width']]
    center = F.interpolate(content.movedim(-1, 1), size=(height, width), mode='bicubic',
                           align_corners=False, antialias=True).clamp(0, 1).movedim(1, -1)
    return center, left, top, width, height


class H3OrbitWideCanvas:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'image': ('IMAGE',), 'geometry': ('H3_ORBIT_GEOMETRY',)}}
    RETURN_TYPES = ('IMAGE', 'MASK', 'STRING', 'H3_ORBIT_GEOMETRY', 'FLOAT')
    RETURN_NAMES = ('canvas', 'outside_mask', 'prompt', 'geometry', 'frame_basis_zoom')
    FUNCTION = 'prepare'
    CATEGORY = 'MiniMax H3/Orbit'

    def prepare(self, image, geometry):
        center, left, top, width, height = canvas_parts(image, geometry)
        canvas = torch.full_like(image, .5)
        mask = torch.ones(image.shape[:3], dtype=image.dtype, device=image.device)
        canvas[:, top:top+height, left:left+width] = center
        mask[:, top:top+height, left:left+width] = 0
        g = dict(geometry, frame_basis_zoom=.5, background_completion='masked_qwen_outpaint')
        return canvas, mask, WIDE_PROMPT, g, .5


class H3OrbitWideComposite:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'generated': ('IMAGE',), 'original': ('IMAGE',),
                             'geometry': ('H3_ORBIT_GEOMETRY',)}}
    RETURN_TYPES = ('IMAGE', 'H3_ORBIT_GEOMETRY', 'FLOAT')
    RETURN_NAMES = ('wide_image', 'geometry', 'frame_basis_zoom')
    FUNCTION = 'compose'
    CATEGORY = 'MiniMax H3/Orbit'

    def compose(self, generated, original, geometry):
        if (generated.ndim != 4 or generated.shape[:3] != original.shape[:3]
                or generated.shape[-1] not in (3, 4) or not torch.isfinite(generated).all()):
            raise ValueError("補完画像の寸法・画素が元のキャンバスと一致しません。")
        if generated.shape[-1] == 4:
            # Qwen 2.1's VAE decodes RGBA. Flatten alpha before the RGB orbit.
            alpha = generated[..., 3:].clamp(0, 1)
            generated = generated[..., :3] * alpha + (1-alpha)
        center, left, top, width, height = canvas_parts(original, geometry)
        h, w = original.shape[1:3]
        edge = F.pad(center.movedim(-1, 1), (left, w-left-width, top, h-top-height),
                     mode='replicate').movedim(1, -1)
        y = torch.arange(h, device=original.device)
        x = torch.arange(w, device=original.device)
        dx = torch.maximum((left-x).clamp_min(0), (x-(left+width-1)).clamp_min(0))
        dy = torch.maximum((top-y).clamp_min(0), (y-(top+height-1)).clamp_min(0))
        distance = torch.maximum(dy[:, None], dx[None, :])
        # Feather only OUTSIDE the known image; preserve its entire center exactly.
        alpha = (1-distance.to(original.dtype)/8).clamp(0, 1)[None, ..., None]
        result = generated.clamp(0, 1) * (1-alpha) + edge * alpha
        g = dict(geometry, frame_basis_zoom=.5, background_completion='masked_qwen_outpaint')
        return result, g, .5
