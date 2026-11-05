ENGINES = {"背景用 FL2VA（73枚）": ("loop", 28, 73), "従来比較 Ref2VA＋ORB360（124枚）": ("ref", 20, 124)}


class H3OrbitBackend:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "engine": (list(ENGINES),),
            "ref_model": ("MODEL", {"lazy": True}),
            "ref_positive": ("CONDITIONING", {"lazy": True}),
            "ref_latent": ("LATENT", {"lazy": True}),
            "loop_model": ("MODEL", {"lazy": True}),
            "loop_positive": ("CONDITIONING", {"lazy": True}),
            "loop_latent": ("LATENT", {"lazy": True}),
        }}

    RETURN_TYPES = ("MODEL", "CONDITIONING", "LATENT", "INT", "INT")
    RETURN_NAMES = ("model", "positive", "latent", "steps", "frame_count")
    FUNCTION = "select"
    CATEGORY = "MiniMax H3/Orbit"

    def check_lazy_status(self, engine, **inputs):
        if engine not in ENGINES:
            raise ValueError("生成方式を選び直してください。")
        prefix = ENGINES[engine][0]
        return [name for name in (prefix + "_model", prefix + "_positive", prefix + "_latent")
                if inputs.get(name) is None]

    def select(self, engine, ref_model=None, ref_positive=None, ref_latent=None,
               loop_model=None, loop_positive=None, loop_latent=None):
        if engine not in ENGINES:
            raise ValueError("生成方式を選び直してください。")
        prefix, steps, frames = ENGINES[engine]
        values = (loop_model, loop_positive, loop_latent) if prefix == "loop" else (ref_model, ref_positive, ref_latent)
        if any(value is None for value in values):
            raise ValueError("選択した生成方式のモデル・条件・Latentを接続してください。")
        return (*values, steps, frames)
