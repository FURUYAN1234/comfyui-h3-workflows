# Model files / モデルの取得と配置

Model weights are not included. Read each publisher's terms before downloading. These four files total approximately 40.44 GB (decimal), excluding LM Studio and generated data. / モデル重みは同梱しません。取得前に各配布者の条件を確認してください。4ファイルで約40.44GB（十進表記）あり、LM Studio用モデルや中間データは別です。

Use the pinned links below to reproduce the recorded revision. `models.json` also keeps the upstream `main` links used by ComfyUI's download metadata; `main` may change. Match the size and SHA-256 when verifying a download. / 下記は記録した版を固定したリンクです。ワークフローの取得案内には上流のmainリンクも使っていますが、将来内容が変わる可能性があるため、サイズとハッシュを照合します。

## diffusion_models / 映像生成モデル

- File / ファイル: `minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors`
- Destination / 配置先: `ComfyUI/models/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors`
- [Download pinned file / 固定版を取得](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/8a8dffaa0cd99c6184833ae0a3b4e9b0089c17b3/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors) · [Publisher / 配布元](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot)
- Size / バイト数: `20980178976`
- SHA-256: `4262e4e9963c553fa00016bbe83961407a4fc0a888be95fd836c8d4f2304e48b`
- Revision / 固定版: `8a8dffaa0cd99c6184833ae0a3b4e9b0089c17b3`

## text_encoders / H3テキストエンコーダー

- File / ファイル: `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- Destination / 配置先: `ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- [Download pinned file / 固定版を取得](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/a98869194787969724c7425d95d0ed73ce9202af/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) · [Publisher / 配布元](https://huggingface.co/Comfy-Org/MiniMax-H3)
- Size / バイト数: `15687142551`
- SHA-256: `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6`
- Revision / 固定版: `a98869194787969724c7425d95d0ed73ce9202af`

## vae / 映像VAE

- File / ファイル: `minimax_h3_video_vae_int8_convrot.safetensors`
- Destination / 配置先: `ComfyUI/models/vae/minimax_h3_video_vae_int8_convrot.safetensors`
- [Download pinned file / 固定版を取得](https://huggingface.co/Kijai/MiniMax-H3-experimental/resolve/f4cac997f880e93cf6940af61ee8d58ef31ff7f3/minimax_h3_video_vae_int8_convrot.safetensors) · [Publisher / 配布元](https://huggingface.co/Kijai/MiniMax-H3-experimental)
- Size / バイト数: `3171670912`
- SHA-256: `9bb2d96f218c76babd85e0611b85ca8fb330a90546c01a0005e8a58a59593410`
- Revision / 固定版: `f4cac997f880e93cf6940af61ee8d58ef31ff7f3`

## vae / 音声VAE

- File / ファイル: `minimax_h3_audio_vae_fp32.safetensors`
- Destination / 配置先: `ComfyUI/models/vae/minimax_h3_audio_vae_fp32.safetensors`
- [Download pinned file / 固定版を取得](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/a98869194787969724c7425d95d0ed73ce9202af/vae/minimax_h3_audio_vae_fp32.safetensors) · [Publisher / 配布元](https://huggingface.co/Comfy-Org/MiniMax-H3)
- Size / バイト数: `605254808`
- SHA-256: `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48`
- Revision / 固定版: `a98869194787969724c7425d95d0ed73ce9202af`

## After downloading / 取得後

Check that the browser has finished downloading (no `.part` or `.crdownload` file). Keep the exact filename and extension. Create the model subfolder if missing. Restart ComfyUI or refresh its model list, then select all four files in their corresponding loader nodes. Do not place models in `custom_nodes/` or `workflows/`. / ダウンロード完了を確認し、ファイル名と拡張子を変えずに配置します。必要なら保存フォルダーを作り、ComfyUIの一覧更新または再起動後に各ローダーで選択します。

The H3 text encoder is not the LM Studio GGUF model. Download a separate suitable LM Studio model only if using the top prompt field. For I2V/Ref2V conversion, it must accept images. / H3のテキストエンコーダーとLM Studio用GGUFは別物です。上欄の変換を使う場合だけ別途用意し、画像経路では画像対応を確認します。

Some download endpoints may require acceptance of terms or an account. A failed download or absent license label does not permit bypassing restrictions. See [license scope](../LICENSES_AND_NOTICES.md). / 利用条件の同意やアカウントが必要な場合は配布元の案内に従い、制限を回避しないでください。

## Optional SHA-256 check / ハッシュ確認

With Python available, replace the path and run this read-only streaming check. Compare the full output with the matching SHA-256 above. / Pythonのある環境では、パスを実ファイルに置き換えて検査できます。出力全体が上表と一致することを確認します。

```bash
python -c "import hashlib; p=input('Model path: '); h=hashlib.sha256(); f=open(p,'rb'); [None for b in iter(lambda:f.read(8*1024*1024),b'') if h.update(b)]; f.close(); print(h.hexdigest())"
```

Use the ComfyUI interpreter path instead of `python` when required. This reads the complete model and can take time. / 必要に応じてComfyUIのPythonのパスへ置き換えます。全ファイルを読み取るため時間がかかります。
