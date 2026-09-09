# ComfyUI H3 Workflows

Version / バージョン: **v1.1.0**

Portable T2V, I2V and Ref2V workflows for MiniMax H3. The distribution contains the five required custom-node packages, four-step video generation, two-step audio refinement, variable duration, and optional Japanese-to-English conversion through a locally started LM Studio server.

MiniMax H3で文章・開始画像・参照画像から音声付き動画を作る可搬版です。必要な5カスタムノードを収録し、映像4ステップ、音声再精錬2ステップ、可変尺、日本語のLM Studio変換、完成英語の直接入力に対応します。

## Distribution / 配布内容

| Workflow | Input | Purpose |
| --- | --- | --- |
| `T2V_4step_…_v1.1.0.json` | text | Generate video and sound without an image. / 画像なしで映像と音声を生成。 |
| `I2V_4step_…_v1.1.0.json` | text + first frame | Animate from a starting image. / 開始画像から動かす。 |
| `Ref2V_4step_…_v1.1.0.json` | text + 1–5 references | Use reference appearance in a new scene. / 参照画像の人物や服装を新しい場面で使う。 |

The ZIP contains the three workflows, all five custom-node folders, model metadata, licenses, validation notes, an offline verifier, and a SHA-256 manifest. It does not contain model weights, images, generated media, credentials, private dictionaries, PC-specific paths, or startup scripts.

ZIPには3ワークフロー、5カスタムノード、モデル情報、ライセンス、検証範囲、オフライン検査器、SHA-256マニフェストを収録します。モデル本体、画像、生成物、認証情報、私用辞書、個人PCのパスや起動スクリプトは収録しません。

## Installation / 導入

1. Prepare a CUDA-capable ComfyUI version that supports MiniMax H3, ResolutionSelector and the V3 node API.
2. Copy all five folders from `custom_nodes/` into ComfyUI's `custom_nodes/`; do not create nested duplicate folders.
3. Install `requirements.txt` with ComfyUI's own Python, add the four named model files to the listed model directories, then restart ComfyUI.
4. Open one workflow. I2V and Ref2V require your own image before queueing. For Japanese conversion, start LM Studio and load the configured model first.

詳しい導入・操作は [README_JA.md](README_JA.md)、検証範囲は [VALIDATION.md](VALIDATION.md)、変更履歴は [CHANGELOG.md](CHANGELOG.md)、ライセンスは [LICENSES_AND_NOTICES.md](LICENSES_AND_NOTICES.md) を参照してください。

## Verification / 検証

Run `python verify_package.py` after extracting the ZIP. It checks the three graphs, mandatory routes, the 4+2 configuration, model metadata, empty private inputs, package licenses, Python syntax, and the manifest. This is an offline package check, not a claim of GPU generation on every PC.

## Requirements / 動作環境

Use a ComfyUI version supporting MiniMax H3, ResolutionSelector, the V3 node API and the selected quantized models. CUDA PyTorch and compatible Triton are required for this SLA configuration. The original package author reported Windows/WSL2 Ubuntu, RTX 5080 16 GB, ComfyUI 0.34.0 and frontend 1.51.9. These are reference conditions, not guaranteed minimum requirements; this publication revision has not been rendered on a GPU.

H3・ResolutionSelector・V3ノードAPI・選択モデルに対応するComfyUI、CUDA版PyTorch、対応Tritonが必要です。元配布物の検証記録はWindows/WSL2 Ubuntu、RTX 5080 16GB、ComfyUI 0.34.0、フロントエンド1.51.9です。最低要件や全環境での保証ではなく、今回の公開用改訂ではGPU生成を行っていません。

The four H3 files total approximately 40.44 GB. Allow additional space for an optional LM Studio model, cached latents and output videos. Resolution, duration and simultaneously loaded models affect RAM and VRAM requirements. Four sampling steps do not make the weights smaller.

H3用4ファイルは合計約40.44GBです。LM Studio用モデル、中間潜在データ、動画の保存領域も必要です。画素数・尺・同時ロード数によりRAM/VRAM使用量が変わり、4ステップでもモデル自体の容量は減りません。

## Installation / 導入

### Choose your environment / 最初に環境を選ぶ

The recorded GPU runs used WSL2 Ubuntu. Native Windows commands below explain folder and interpreter handling, but do not constitute a native-Windows success report. If you already have a working ComfyUI, keep it intact and prepare a separate installation for this package when possible. / 元のGPU検証はWSL2 Ubuntuです。以下のWindowsコマンドは配置とPythonの使い分けの説明で、Windowsネイティブの動作確認報告ではありません。既存の稼働環境は残し、可能なら別のComfyUIで導入してください。

- **Windows Portable:** obtain the NVIDIA Portable build from the [official Windows guide](https://docs.comfy.org/installation/comfyui_portable_windows), extract it, and confirm that the outer folder contains `python_embeded` and the inner `ComfyUI/main.py`. / 公式のNVIDIA用Portable版を展開し、外側にPython、内側に本体があることを確認します。
- **WSL/Linux:** prepare NVIDIA GPU support following your OS/driver instructions, then follow [ComfyUI's manual installation](https://docs.comfy.org/installation/manual_install). Keep the virtual-environment Python used by that installation. / GPUドライバーとWSL側のGPU認識を確認し、公式の手動導入手順で本体と仮想環境を用意します。

Start unmodified ComfyUI once and check that its normal interface opens before adding nodes. If the base installation cannot start or reports CUDA unavailable, resolve that first. This package does not install drivers, replace PyTorch or configure WSL automatically. / ノード追加前にComfyUI単体を一度起動し、通常画面が開くことを確認します。本体の起動やCUDA認識に失敗する場合は先に解決してください。本配布物はドライバーやWSLを自動設定しません。

### Add this package / 配布物の導入

1. Prepare ComfyUI using the [official instructions](https://docs.comfy.org/installation/system_requirements). / 公式手順でComfyUIを用意します。
2. Extract the distribution. Copy only `comfyui-h3-standard-prompt` from its `custom_nodes/` into ComfyUI's `custom_nodes/`. Avoid duplicate nested folders. / ZIP内の自作ノード1フォルダーをComfyUIの`custom_nodes/`へコピーします。
3. Follow [DEPENDENCIES.md](docs/DEPENDENCIES.md) to obtain the four pinned upstream nodes and apply the corresponding patches. Existing installations should be preserved before replacement. / 依存4ノードは固定コミットを取得し、指定パッチを適用します。既存環境は退避してから作業します。
4. Install `requirements.txt` with **ComfyUI's own Python**. / ComfyUI自身のPythonへ依存ライブラリを入れます。
5. Obtain the four model files using [MODELS.md](docs/MODELS.md), then restart ComfyUI and select them in the loaders. / モデルを指定位置へ配置し、再起動後にローダーで選びます。
6. Import one workflow JSON. For I2V/Ref2V, supply your own image before queuing. / JSONを読み込み、画像が必要な経路では自分の画像を入れて実行します。

Windows Portable example, from its root / Windows Portableのルートからの例:

```powershell
.\python_embeded\python.exe -m pip install -r "C:\path\to\ComfyUI_H3_Workflows\requirements.txt"
```

Linux/WSL example, from ComfyUI / ComfyUIフォルダーからの例:

```bash
.venv/bin/python -m pip install -r /path/to/ComfyUI_H3_Workflows/requirements.txt
```

Use a Triton build matching your OS and PyTorch; Windows users should consult [triton-windows compatibility](https://github.com/woct0rdho/triton-windows). Verify CUDA/Triton with the same Python: `python -c "import torch,triton; print(torch.__version__, torch.cuda.is_available(), triton.__version__)"`.

TritonはOS・PyTorchとの互換性を確認します。Windowsは上記のtriton-windows案内を参照し、同じPythonでCUDAとTritonが認識されることを確認してください。

### Start and open a workflow / 起動と読み込み

On Portable Windows, run the supplied `run_nvidia_gpu.bat` from the Portable root. On a manual WSL/Linux installation, use the same environment you prepared, for example `.venv/bin/python main.py` from `ComfyUI/`. Keep the terminal open; it contains the startup and generation error logs. Open the local address printed in that terminal (normally `http://127.0.0.1:8188`). / Portable版は同梱の`run_nvidia_gpu.bat`、WSL/Linuxの手動導入例はComfyUI内で`.venv/bin/python main.py`を使います。端末を閉じず、表示されたローカルURLを開いてください。

Drag `workflows/T2V_4step.json` into ComfyUI or use its Open command. No red missing-node boxes should remain. Select the four named model files in their loaders. If an error appears, read the startup log before queueing. Proceed to the short direct-input example below; it checks H3 without needing LM Studio. After that succeeds, configure LM Studio and try Japanese instructions. / 最初はT2Vを開き、赤い不足ノードがなく、4モデルが選択できることを確認します。下の5秒の直接入力例ならLM Studioを切り離して確認できます。成功後に日本語入力へ進めてください。

## Prompt fields / 入力欄

| Field / 欄 | Behavior / 動作 |
| --- | --- |
| Top: instruction / ①上欄 | Nonempty text is converted by LM Studio, including connected images. Takes precedence over the bottom field. / 文章があると画像とともにLM Studioへ渡して変換。下欄より優先。 |
| Middle: conversion rules / ②中央 | Used only for LM conversion. / LM変換時だけ適用する基本ルール。 |
| Bottom: completed H3 prompt / ③下欄 | Used directly only when the top is empty; LM Studio and middle rules are bypassed. / 上欄が空のとき完成英語を直接使用。LMと中央ルールを省略。 |

Both input fields empty causes an error. The final-prompt and plan displays are outputs, not editable inputs for the next run. Direct mode must contain its own audio, dialogue and reference instructions.

上下が両方空ならエラーになります。「H3最終プロンプト」と実行計画の表示は結果確認用で、次回の入力欄ではありません。直接入力では音・台詞・参照画像の条件も本文へ含めます。

## LM Studio / 日本語変換

Start [LM Studio](https://lmstudio.ai/) manually, load a vision-capable model for I2V/Ref2V, and enable a server supporting `/api/v1/chat`. Match the workflow model ID to the server's loaded model. The source guide uses Qwen3.5-9B GGUF as an example; the alias `qwen-prompt-ja` is not a model download name. Default connection: `http://127.0.0.1:1234/v1`; for WSL-to-Windows use a reachable host address.

LM Studioを手動起動し、I2V/Ref2Vでは画像対応モデルをロードして、`/api/v1/chat`対応サーバーを有効にします。ワークフローのモデル識別子をロード済みモデルと一致させます。元の案内ではQwen3.5-9B GGUFを例にしています。`qwen-prompt-ja`は任意の別名です。WSLからWindowsへ接続するときは到達可能なホストアドレスを指定してください。

The client assumes a trusted local server without required API authentication; `Bearer lm-studio` is a fixed placeholder, not a personal credential. Token-required servers are unsupported in this revision. Instructions and reference images are sent to the selected reachable LM Studio server; the client also probes local/WSL fallback addresses. Do not expose this service to the public internet. CPU model loading can reduce competition with H3 for VRAM but increases conversion time.

認証不要の信頼できるローカルサーバーを前提とします。固定ヘッダー`Bearer lm-studio`は個人キーではなく、認証必須サーバーはこの版では非対応です。指示と画像は到達可能なLM Studioへ送信され、ローカル・WSLの候補アドレスも探索します。インターネットへのポート公開は不要です。CPUロードはH3とのVRAM競合を減らせますが変換が遅くなります。

## Duration, images and sound / 尺・画像・音

- **Duration / 尺:** default 15 seconds, overridden by explicit `Duration: 30 seconds` or `全体30秒`. Conflicting declarations select the longest; keep one declaration. Long outputs use continuation segments. / 明示尺が数値欄より優先。複数指定は最長になるため1つに統一します。
- **Progression / 展開:** add timed shots such as `[Shot 2] At 00:15.000, ...` for long outputs. Inspect `duration_seconds`, `segment_count`, `windows` and warnings. / 長尺は時系列の展開を書き、実行計画で尺と区間を確認します。
- **Resolution / 解像度:** change `aspect_ratio` and `megapixels`; defaults are 16:9, 0.4 MP, multiple 32. Higher settings cost more memory/time. / 比率・画素数で調整し、multipleは32から始めます。
- **References / 参照:** Ref2V slots 2–5 start disabled. Enable needed slots with Ctrl+B and upload images in order. I2V uses one first frame; cropping may occur when aspect ratios differ. / 必要な追加参照だけ有効化し、順番に画像を入れます。
- **BGM:** request it in the top field or supply `non_diegetic_music:` in direct mode. `N/A` means no BGM. H3 generates music; there is no separate music-file mixer. / BGMはH3が生成し、既存楽曲を後付けする機能ではありません。
- **Dialogue / 台詞:** use stable speaker IDs such as `(S1)` and `<d>[Japanese]こんにちは。</d>`. Specify readings where needed and listen to the output. Two-step audio refinement is not a guarantee of exact pronunciation or volume. / 読みが必要なら明示し、実音声を聴いて確認してください。

### Direct-input example / 直接入力例

Clear the top field, then place this in the bottom field of T2V. / T2Vの上欄を空にして下欄へ入力します。

```text
Duration: 5 seconds
integrated_multimodal_description:
[Shot 1] A paper windmill turns gently on a sunny balcony. One continuous shot, natural daylight, no people and no on-screen text.
overall_soundscape: Gentle breeze, no speech.
non_diegetic_music: N/A
```

## Output and troubleshooting / 保存・トラブル対応

The save node's `filename_prefix` is relative to `ComfyUI/output/`. Intermediate segments, prompts and manifests use `output/h3_long_video/` and the selected `cache_name`. Keep default resume/reroll settings for an initial run.

完成動画の`filename_prefix`は`ComfyUI/output/`からの相対パスです。区間データ・文章・manifestは`output/h3_long_video/`と`cache_name`で指定します。初回はresume/reroll設定を変更しません。

| Symptom / 症状 | Check / 確認 |
| --- | --- |
| Missing nodes or inputs / ノード・入力不足 | Pinned revisions, all patches, folder nesting, Python dependencies and startup errors. / 固定コミット・パッチ・配置・起動ログ。 |
| Model missing / モデル不足 | Exact filename and destination in `models.json`. / モデル名・配置先。 |
| LM 404 / LMの404 | Server may run while model is unloaded; reload the same identifier. / サーバー起動とモデルのロードは別状態。 |
| Out of memory / メモリー不足 | Lower megapixels/duration; consider CPU LM loading. / 画素数・尺・同時ロードを調整。 |
| Wrong duration / 尺が変わらない | Remove old explicit duration from the prompt. / 本文に残った明示尺を確認。 |
| Repeated movement / 動作反復 | Inspect the actual prompt, timed progression and result; this revision does not claim a general repetition fix. / 実文章・時間展開・生成物を確認。 |

## Verification and limits / 検証と制限

Run `python verify_package.py` in the extracted package. It checks hashes, workflow wiring and settings, source syntax and distribution contents without model downloads or generation. [VALIDATION.md](VALIDATION.md) separates the source author's historical generation report from checks made for this publication.

展開先で`python verify_package.py`を実行すると、モデル取得や生成をせずにハッシュ・配線・設定・構文・配布内容を検査します。元の実生成記録と今回の公開用検査は[VALIDATION.md](VALIDATION.md)で区別しています。

## License and project identity / ライセンスと名称

Original contributions are MIT. The Long-Video compatibility patch is GPL-3.0-only; other dependency patches retain MIT notices. This repository does not relicense third-party code or model weights. See [LICENSES_AND_NOTICES.md](LICENSES_AND_NOTICES.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

独自作成部分はMIT、Long-Video互換パッチはGPL-3.0-only、その他の依存パッチはMIT表示を維持します。第三者コードやモデルのライセンスを変更しません。モデルは各配布元の利用条件に従って取得してください。

An independent community project by FURUYAN1234; not an official MiniMax, ComfyUI or LM Studio product. / FURUYAN1234による独立したコミュニティ制作物です。MiniMax・ComfyUI・LM Studioの公式製品ではありません。

[Detailed note article / 詳細記事](https://note.com/happy_duck780/n/n15e732b3147b) · [Related manga system / 関連まんがシステム](https://github.com/FURUYAN1234/nano-banana-pro)
