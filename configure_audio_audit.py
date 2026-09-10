"""Configure the installed speech guard with an already downloaded local model."""
import argparse
import json
from pathlib import Path


def configure(comfyui, model):
    root = Path(comfyui).resolve()
    folder = Path(model).resolve()
    target = root / 'custom_nodes/ComfyUI-MiniMax-H3-Long-Video/minimax_h3_long_video/local_audio_audit.json'
    if not (root / 'main.py').is_file() or not target.parent.is_dir():
        raise ValueError('Select the ComfyUI folder containing main.py and the installed Long Video node.')
    if not (folder / 'config.json').is_file() or not (folder / 'preprocessor_config.json').is_file():
        raise ValueError('Select the complete Transformers Whisper model directory, not a GGUF file.')
    if not any(folder.glob('*.safetensors')) and not any(folder.glob('pytorch_model*.bin')):
        raise ValueError('Whisper model weights are missing. This helper does not download models.')
    value = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    value.update(enabled=True, model_path=str(folder))
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if target.exists() and target.read_text(encoding='utf-8') != encoded:
        backup = target.with_suffix('.json.before_setup')
        if not backup.exists():
            backup.write_bytes(target.read_bytes())
    target.write_text(encoded, encoding='utf-8')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfyui', required=True)
    parser.add_argument('--whisper-model', required=True)
    args = parser.parse_args()
    print('Configured:', configure(args.comfyui, args.whisper_model))
