"""Keep model input dtype consistent across supported ComfyUI versions."""

class DtypeSafeUpscaler:
    def __init__(self, model):
        self.model = model

    def __getattr__(self, name):
        return getattr(self.model, name)

    def __call__(self, image):
        return self.model(image.to(dtype=self.model.dtype))


def upscale_image(model, image):
    from comfy_extras.nodes_upscale_model import ImageUpscaleWithModel
    return ImageUpscaleWithModel().upscale(DtypeSafeUpscaler(model), image)[0]
