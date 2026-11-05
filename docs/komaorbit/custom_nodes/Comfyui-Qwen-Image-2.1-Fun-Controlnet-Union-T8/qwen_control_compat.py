"""Qwen Image 2.1 Fun ControlNet compatibility node.

Derived from ComfyUI / Comfy-Org PR #16519 by kijai and ComfyUI contributors.
Source: https://github.com/Comfy-Org/ComfyUI/pull/16519
License: GPL-3.0-only; full text is in LICENSE-GPL-3.0.txt.
Modification: extracted as a custom-node fallback when core does not export
QwenImage21FunControlNetApply; no ComfyUI core file is changed.
"""
import comfy.utils
import comfy.latent_formats
import comfy.model_management
import comfy.model_prefetch
import comfy.patcher_extension
from comfy_api.latest import io
import torch
import torch.nn.functional as F

class QwenImage21FunControlPatch:
    def __init__(self, model_patch, vae, control_image, inpaint_image, mask, strength, injection_layers, sigma_start, sigma_end):
        self.model_patch = model_patch
        self.vae = vae
        self.control_image = control_image
        self.inpaint_image = inpaint_image
        self.mask = mask
        self.strength = strength
        self.injection_layers = injection_layers
        self.sigma_start = sigma_start
        self.sigma_end = sigma_end
        self.active = False
        self.control = None
        self.stream = None
        self.pristine = None

    def prepare(self, h, w):
        # 129 channels per target token: control latents | keep mask | masked-image latents, zeros where not given
        if self.control is not None and self.control.shape[-2:] == (h, w):
            return
        width, height = w * self.vae.spacial_compression_encode(), h * self.vae.spacial_compression_encode()
        latent_format = comfy.latent_formats.QwenImage21()
        loaded_models = comfy.model_management.loaded_models(only_currently_used=True)
        try:
            control = torch.zeros(1, latent_format.latent_channels, h, w)
            if self.control_image is not None:
                image = comfy.utils.common_upscale(self.control_image[:1].movedim(-1, 1), width, height, "bicubic", "disabled")
                control = latent_format.process_in(self.vae.encode(image.movedim(1, -1))).float().cpu()
            regen = torch.ones(1, 1, height, width)
            if self.mask is not None:
                mask = self.mask.reshape(-1, 1, *self.mask.shape[-2:])[:1].float().cpu()
                regen = (comfy.utils.common_upscale(mask, width, height, "bilinear", "disabled") >= 0.5).float()
            inpaint = torch.zeros_like(control)
            if self.inpaint_image is not None:
                # regenerated pixels at mid-gray, the zero of the VAE's [-1, 1] input
                image = comfy.utils.common_upscale(self.inpaint_image[:1].movedim(-1, 1), width, height, "bicubic", "disabled").float().cpu()
                inpaint = latent_format.process_in(self.vae.encode((image * (1 - regen) + 0.5 * regen).movedim(1, -1))).float().cpu()
            keep = 1 - F.interpolate(regen, size=(h, w), mode="nearest")
        finally:
            comfy.model_management.load_models_gpu(loaded_models)
        self.control = torch.cat([control, keep, inpaint], dim=1)

    def diffusion_model_wrapper(self, executor, x, timestep, context, ref_latents, image_slots, transformer_options, **kwargs):
        sigma = float(timestep.flatten()[0])
        self.active = self.sigma_end <= sigma <= self.sigma_start
        if self.active:
            with comfy.model_prefetch.pause_malloc_graph():
                self.prepare(*x.shape[-2:])
        else:
            # outside the range the block patches are dropped so the model can use its prefix cache
            dit = transformer_options.get("patches_replace", {}).get("dit", {})
            dit = {k: p.previous if isinstance(p, QwenImage21FunControlBlockPatch) and p.control_patch is self else p for k, p in dit.items()}
            dit = {k: p for k, p in dit.items() if p is not None}
            transformer_options = {**transformer_options, "patches_replace": {**transformer_options["patches_replace"], "dit": dit}}
        try:
            return executor(x, timestep, context, ref_latents, image_slots, transformer_options, **kwargs)
        finally:
            self.stream = None
            self.pristine = None

    def before_block(self, block_index, args):
        if self.active and block_index == self.injection_layers[0]:
            # the base block updates its input in place
            self.pristine = args["img"].clone()

    def after_block(self, block_index, args, out):
        if not self.active:
            return out
        model = self.model_patch.model
        index = self.injection_layers.index(block_index)
        if index == 0:
            self.control = self.control.to(out["img"].device, out["img"].dtype)
            self.stream = model.init_stream(self.pristine, self.control.flatten(2).transpose(1, 2), args["prefix_len"])
            self.pristine = None
        self.stream, skip = model.step(index, self.stream, args["mod"], args["pe"], args["attn_fn"], args["prefix_len"], args["transformer_options"])
        out["img"].add_(skip, alpha=self.strength)
        return out

    def to(self, device_or_dtype):
        if isinstance(device_or_dtype, torch.device):
            if self.control is not None:
                self.control = self.control.to(device_or_dtype)
            self.stream = None
        return self

    def cleanup(self):
        self.control = None
        self.stream = None
        self.pristine = None
        self.active = False

    def models(self):
        return [self.model_patch]

    def register(self, model):
        model.add_wrapper(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, self.diffusion_model_wrapper)
        blocks_replace = model.model_options.get("transformer_options", {}).get("patches_replace", {}).get("dit", {})
        for block_index in self.injection_layers:
            previous = blocks_replace.get(("single_block", block_index))
            model.set_model_patch_replace(QwenImage21FunControlBlockPatch(self, block_index, previous), "dit", "single_block", block_index)


class QwenImage21FunControlBlockPatch:
    def __init__(self, control_patch, block_index, previous):
        self.control_patch = control_patch
        self.block_index = block_index
        self.previous = previous

    def __call__(self, args, extra_args):
        # control state stays outside the base block's allocation scope
        with comfy.model_prefetch.pause_malloc_graph():
            self.control_patch.before_block(self.block_index, args)
        out = extra_args["original_block"](args) if self.previous is None else self.previous(args, extra_args)
        with comfy.model_prefetch.pause_malloc_graph():
            return self.control_patch.after_block(self.block_index, args, out)

    def to(self, device_or_dtype):
        self.control_patch.to(device_or_dtype)
        if hasattr(self.previous, "to"):
            self.previous = self.previous.to(device_or_dtype)
        return self

    def cleanup(self):
        self.control_patch.cleanup()
        if hasattr(self.previous, "cleanup"):
            self.previous.cleanup()

    def models(self):
        models = self.control_patch.models()
        if hasattr(self.previous, "models"):
            models += self.previous.models()
        return models


class QwenImage21FunControlNetApply(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="QwenImage21FunControlNetApply",
            display_name="Apply Qwen Image 2.1 Fun ControlNet",
            search_aliases=["qwen controlnet", "fun controlnet", "controlnet union", "inpaint"],
            category="model/patch/qwen",
            inputs=[
                io.Model.Input("model"),
                io.ModelPatch.Input("model_patch"),
                io.Vae.Input("vae"),
                io.Float.Input("strength", default=1.0, min=-10.0, max=10.0, step=0.01),
                io.Float.Input("start_percent", default=0.0, min=0.0, max=1.0, step=0.001, advanced=True),
                io.Float.Input("end_percent", default=1.0, min=0.0, max=1.0, step=0.001, advanced=True),
                io.Image.Input("control_image", optional=True, tooltip="Control image: canny, depth, pose, lineart, HED, MLSD, scribble or grayscale."),
                io.Image.Input("inpaint_image", optional=True, tooltip="Image to repaint; only read together with a mask."),
                io.Mask.Input("mask", optional=True, tooltip="1 marks the region to regenerate."),
            ],
            outputs=[io.Model.Output()],
        )

    @classmethod
    def execute(cls, model, model_patch, vae, strength, start_percent=0.0, end_percent=1.0, control_image=None, inpaint_image=None, mask=None) -> io.NodeOutput:
        if strength == 0 or (control_image is None and mask is None):
            return io.NodeOutput(model)
        model_patched = model.clone()
        num_base = len(model.get_model_object("diffusion_model").transformer_blocks)
        num_control = len(model_patch.model.control_blocks)
        control_image = control_image[..., :3] if control_image is not None else None
        inpaint_image = inpaint_image[..., :3] if mask is not None and inpaint_image is not None else None
        model_sampling = model.get_model_object("model_sampling")
        patch = QwenImage21FunControlPatch(model_patch, vae, control_image, inpaint_image, mask, strength, list(range(0, num_base, num_base // num_control)),
                                           float(model_sampling.percent_to_sigma(start_percent)), float(model_sampling.percent_to_sigma(end_percent)))
        patch.register(model_patched)
        return io.NodeOutput(model_patched)
