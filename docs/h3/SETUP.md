# H3 shared setup / H3動画・MVの共通導入
<!-- bilingual-readme: paired english-japanese -->

Install this once for the H3 video set or MV. KomaOrbit uses its own instructions. The video ZIP has five node packages; the MV ZIP adds `comfyui-mv-workflow` for six. / H3動画3方式またはMVのための共通導入です。KomaOrbitは専用手順を使います。動画版は5パッケージ、MV版は `comfyui-mv-workflow` を加えた6パッケージです。

## Installation / インストール

### 1. Choose one package / 1. 必要なZIPを1つ選ぶ

Use **H3_Video_20261105183000_v1.2.1.zip** for T2V/I2V/Ref2V, or **H3_MV_20261105183000_v1.2.1.zip** for MV. Each is self-contained for its custom nodes; neither needs KomaOrbit or the other ZIP. / T2V・I2V・Ref2Vは **H3_Video_20261105183000_v1.2.1.zip**、MVは **H3_MV_20261105183000_v1.2.1.zip** を選びます。それぞれに必要なノードが入り、KomaOrbitやもう一方のZIPは不要です。

[Named ZIP assets / 名前付きZIPを取得](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/tag/h3-split-20261105183000)

Extract the entire selected ZIP. Before updating, back up existing same-named node folders outside ComfyUI. / 選んだZIP全体を展開します。同名ノードの更新前には、既存フォルダーをComfyUIの外へ退避してください。

### 2. Verify the extracted package / 2. 展開した配布物を検査する

Run the following command in the extracted directory. Models and a GPU are not required for this check. / 展開先で次を実行します。モデルやGPUは不要です。

```bash
python -B verify_package.py
```

If `python` is unavailable on Windows, use the command that matches your installed Python, such as `py -B verify_package.py`. A valid package finishes with: / Windowsで `python` が見つからない場合は、インストール済みのPythonに合わせて `py -B verify_package.py` などへ置き換えます。次の表示で終了すれば配布内容は正常です。

```text
Workflow OK: I2V
Workflow OK: Ref2V
Workflow OK: T2V
Package checks passed.
```

### 3. Install the included custom-node folders / 3. 同梱カスタムノードを配置する

Copy all included folders under `custom_nodes/` in the extracted ZIP into ComfyUI's `custom_nodes/` directory. / 展開したZIPの `custom_nodes/` にある同梱フォルダーを、ComfyUI本体の `custom_nodes/` へコピーします。

```text
ComfyUI/
└─ custom_nodes/
   ├─ comfyui-h3-standard-prompt/
   ├─ ComfyUI-MiniMax-H3-Long-Video/
   ├─ ComfyUI-H3-AudioRefine/
   ├─ ComfyUI-PlagueKind-Nodes/
   ├─ ComfyUI-Custom-Scripts/
   └─ comfyui-mv-workflow/     # MV package only / MV版のみ
```

Each folder must have `__init__.py` directly inside it. Do not create a duplicated nested folder such as: / 各フォルダーの直下に `__init__.py` がある状態が正しい配置です。次のように同じフォルダー名が二重にならないようにしてください。


```text
# Incorrect
ComfyUI/custom_nodes/ComfyUI-H3-AudioRefine/ComfyUI-H3-AudioRefine/__init__.py
```


```text
# 誤った例
ComfyUI/custom_nodes/ComfyUI-H3-AudioRefine/ComfyUI-H3-AudioRefine/__init__.py
```

The four externally sourced packages are already included in the Release ZIP. Do not mix them with separately downloaded or older versions. / 外部由来の4パッケージもRelease ZIPに同梱済みです。別途取得した版や古い版と混在させないでください。

### 4. Install the Python dependencies / 4. Python依存ライブラリを入れる

Install the dependencies into **the Python interpreter actually used by ComfyUI**. / 依存ライブラリは、**ComfyUIが実際に使用するPython**へインストールします。

For ComfyUI Portable on Windows, run this from the Portable root: / Windows Portable版では、Portableのルートから実行します。


```powershell
.\python_embeded\python.exe -m pip install -r "C:\path\to\H3_Video_20261105183000_v1.2.1\requirements.txt"
```


```powershell
.\python_embeded\python.exe -m pip install -r "C:\展開先\H3_Video_20261105183000_v1.2.1\requirements.txt"
```

For a Linux or WSL venv installation, run this from the ComfyUI directory: / Linux・WSLのvenv版では、ComfyUIフォルダーから実行します。


```bash
.venv/bin/python -m pip install -r "/path/to/H3_Video_20261105183000_v1.2.1/requirements.txt"
```


```bash
.venv/bin/python -m pip install -r "/展開先/H3_Video_20261105183000_v1.2.1/requirements.txt"
```

Install the Triton build required by SLA separately, matching your OS, PyTorch, and CUDA combination. For native Windows guidance, see [triton-windows](https://github.com/woct0rdho/triton-windows). / SLAに必要なTritonはOSとPyTorchの組み合わせに合わせて別途用意します。Windowsネイティブの情報は [triton-windows](https://github.com/woct0rdho/triton-windows) を確認してください。

Check CUDA and Triton using ComfyUI's Python: / ComfyUIのPythonでCUDAとTritonを確認します。

```bash
python -c "import torch,triton; print(torch.__version__, torch.cuda.is_available(), triton.__version__)"
```

If `torch.cuda.is_available()` is `False`, check the NVIDIA CUDA PyTorch build and driver first. Replace `python` above with `python_embeded/python.exe` for Portable or `.venv/bin/python` for a venv installation. / `torch.cuda.is_available()` が `False` の場合は、先にPyTorchとCUDA環境を確認してください。ここでの `python` も、Portable版なら `python_embeded/python.exe`、venv版なら `.venv/bin/python` へ置き換えます。

Also check FFmpeg: / FFmpegも確認します。

```bash
ffmpeg -version
ffprobe -version
```

## Install the four H3 model files / H3モデル4ファイルを配置する


The models total approximately 40.44 GB. Review each model's license terms before downloading it. / モデルは合計約40.44GBです。リンクを開く前に、各モデルの利用条件も確認してください。

| Type / 種類 | File and download / ファイル名・取得先 | Destination / 配置先 | Approx. size / 容量の目安 |
|---|---|---|---:|
| Video generation model / 動画生成モデル | [`minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors`](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/main/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors) | `ComfyUI/models/diffusion_models/` | 20.98 GB / 20.98GB |
| H3 text encoder / H3用テキストエンコーダー | [`qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | `ComfyUI/models/text_encoders/` | 15.69 GB / 15.69GB |
| Video VAE / 映像VAE | [`minimax_h3_video_vae_int8_convrot.safetensors`](https://huggingface.co/Kijai/MiniMax-H3-experimental/resolve/main/minimax_h3_video_vae_int8_convrot.safetensors) | `ComfyUI/models/vae/` | 3.17 GB / 3.17GB |
| Audio VAE / 音声VAE | [`minimax_h3_audio_vae_fp32.safetensors`](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | `ComfyUI/models/vae/` | 0.61 GB / 0.61GB |

After installation, the layout is: / 配置後は次の構成になります。

```text
ComfyUI/
└─ models/
   ├─ diffusion_models/
   │  └─ minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors
   ├─ text_encoders/
   │  └─ qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
   └─ vae/
      ├─ minimax_h3_video_vae_int8_convrot.safetensors
      └─ minimax_h3_audio_vae_fp32.safetensors
```

Do not place models under `workflows/` or `custom_nodes/`. A similarly named model may use a different quantization method or be incompatible with these loaders. Use the listed files for the first run. Exact byte counts and SHA-256 hashes are in [`models.json`](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/models.json). / モデルを `workflows/` や `custom_nodes/` へ置かないでください。ファイル名が似ている別モデルでも、量子化方式やローダーの互換性が同じとは限りません。最初は表の指定ファイルを使用してください。正確なバイト数とSHA-256は [`models.json`](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/models.json) にあります。

## Configure LM Studio / LM Studioを設定する

LM Studio is not the video generation model. It reads Japanese video instructions and input images and prepares an English prompt for H3. If per-segment AI review is enabled, LM Studio is also used during review even when the final prompt is entered directly in English. / LM Studioは動画生成モデルではありません。日本語の動画指示と入力画像を読み、H3向けの英語プロンプトへ整えるために使います。区間AI検査を有効にしている場合は、英語直接入力でも検査時にLM Studioを使います。

1. Install [LM Studio](https://lmstudio.ai/). / [LM Studio](https://lmstudio.ai/)をインストールします。
2. Download a vision-capable local model. Validation used **Qwen3.5-9B**. / 画像認識に対応したローカルモデルを取得します。検証では **Qwen3.5-9B** を使用しました。
3. For I2V and Ref2V, install the Vision-related files needed to accept image input. / I2V・Ref2Vでは画像も渡すため、必要なVision用ファイルを揃えます。
4. Load the model in the Developer view and start the local server. / Developer画面でモデルをロードし、ローカルサーバーを起動します。
5. Set the workflow's "LM Studio model name" to the same identifier used by LM Studio. / ワークフローの「LM Studioのモデル名」を、LM Studio側の識別子と一致させます。

The distributed example settings are: / 配布時の設定例は次のとおりです。


```text
Endpoint: http://127.0.0.1:1234/v1
Model identifier: qwen-prompt-ja
```


```text
接続先: http://127.0.0.1:1234/v1
モデル識別子: qwen-prompt-ja
```

`qwen-prompt-ja` is only an example identifier. If you use another identifier in LM Studio, change the workflow to match. See [LM Studio Server](https://lmstudio.ai/docs/developer/core/server) for detailed server operation. / `qwen-prompt-ja` は任意の識別子です。LM Studioで別名を使う場合はワークフロー側も変更してください。詳しいサーバー操作は [LM Studio Server](https://lmstudio.ai/docs/developer/core/server) を参照してください。

### Return the LM model from GPU to CPU / LMモデルをGPUからCPUへ戻す設定

To enable "load the LM model onto the GPU only for conversion, then return it to the CPU before video generation," add LM Studio's bundled `lms` CLI to `PATH` and install the `lmstudio` package into ComfyUI's Python. If returning the model to the CPU fails, video generation will not start, which avoids a VRAM conflict. / 「LM変換時だけGPUへ読み込み、動画生成前にCPUへ戻す」を有効にする場合は、LM Studio同梱の `lms` CLIをPATHへ登録し、ComfyUIのPythonに `lmstudio` パッケージが必要です。CPU復帰に失敗した場合は、VRAM競合を避けるため動画生成を開始しません。

If this automatic switch is unavailable, turn it off and load the LM model on the CPU from the beginning. Prompt conversion will be slower, but the model will not compete with H3 for VRAM. / この自動切替を使えない環境では設定をOFFにし、LMモデルを最初からCPUへロードしてください。文章変換は遅くなりますが、H3が使うVRAMとの競合を抑えられます。

### Connect from WSL to LM Studio on Windows / WSLからWindowsのLM Studioへ接続する場合

When ComfyUI runs in WSL and LM Studio runs on Windows, WSL may not reach Windows through `127.0.0.1`. In that case, configure an address for the Windows host that is reachable from WSL. / ComfyUIがWSL、LM StudioがWindowsで動いている場合、WSL側の `127.0.0.1` からWindows側へ届かないことがあります。その場合は、WSLから到達できるWindows側のアドレスを接続先に設定します。


```text
http://reachable-host-address:1234/v1
```


```text
http://到達できるホストアドレス:1234/v1
```

Check it from the OS running ComfyUI: / ComfyUIを動かしているOS側から確認します。


```bash
curl http://host-address:1234/api/v1/models
```


```bash
curl http://ホストアドレス:1234/api/v1/models
```

You do not need to expose the LM Studio port to the public internet. / 外部インターネットへLM Studioのポートを公開する必要はありません。

## Configure Whisper for Japanese speech review / 日本語音声検査用Whisperを設定する

Speech review uses [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) in **Transformers format**, not GGUF. Put the model weights, `config.json`, `preprocessor_config.json`, tokenizer files, and related required files in the same folder. / 音声検査には、[Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo)の**Transformers形式**を使います。GGUF形式ではありません。モデル本体、`config.json`、`preprocessor_config.json`、トークナイザー関連ファイルを同じフォルダーへ置きます。

In the extracted Release ZIP directory, run this with ComfyUI's Python: / 展開したRelease ZIPのフォルダーで、ComfyUIのPythonを使って次を実行します。


```bash
python configure_audio_audit.py --comfyui "path/to/ComfyUI" --whisper-model "path/to/Whisper-model"
```


```bash
python configure_audio_audit.py --comfyui "ComfyUI本体のフォルダー" --whisper-model "Whisperモデルのフォルダー"
```

Example for ComfyUI Portable on Windows: / Windows Portable版の例です。

```powershell
C:\ComfyUI_windows_portable\python_embeded\python.exe configure_audio_audit.py --comfyui "C:\ComfyUI_windows_portable\ComfyUI" --whisper-model "D:\AI_Models\whisper-large-v3-turbo"
```

The helper writes `local_audio_audit.json` inside the Long-Video node installed in ComfyUI and backs up an existing setting. You can alternatively use the `H3_LOCAL_WHISPER_MODEL` environment variable. / 設定先は、ComfyUIへ配置したLong-Videoノード内の `local_audio_audit.json` です。既存設定はバックアップされます。環境変数 `H3_LOCAL_WHISPER_MODEL` で指定することもできます。

Speech review runs on the CPU and does not send audio to an external service. It also does not download a model automatically. If review is enabled without a configured model, execution stops. It may still miss short grunts or similar sounds, so always listen to the finished video. / 音声検査はCPUで実行し、音声を外部へ送信しません。モデルの自動ダウンロードも行いません。検査をONにしたままモデル未設定の場合は実行を停止します。短いうなり声などを見落とす場合があるため、最終動画は必ず試聴してください。
