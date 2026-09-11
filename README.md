# MiniMax H3 T2V・I2V・Ref2V Workflows for ComfyUI — v1.1.8

MiniMax H3で、**文章から動画（T2V）**、**開始画像から動画（I2V）**、**参照画像から動画（Ref2V）**を生成するComfyUI向け配布セットです。映像と音声を一緒に生成し、日本語の指示、15秒を超える可変尺、区間ごとの検査と再生成、途中再開に対応します。

このREADMEでは、初めて使う人が「どれを選ぶか」「何を入れるか」「どこへ置くか」「最初に何を実行するか」を順番に確認できます。さらに詳しいノード設定と操作例は [README_JA.md](README_JA.md)、実施済みの検証と未確認範囲は [VALIDATION.md](VALIDATION.md) を参照してください。

> [!IMPORTANT]
> 本プロジェクトはH3のT2V・I2V・Ref2V専用です。[Super FURU AI 4-koma System](https://github.com/FURUYAN1234/nano-banana-pro)とは別製品です。初回導入ではGitHubのソース一式やJSON単体ではなく、[最新Release](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/latest)のAssetsにある名前付きZIPを使用してください。

[![Latest release](https://img.shields.io/github/v/release/FURUYAN1234/comfyui-h3-workflows?label=release)](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/latest)
[![ComfyUI](https://img.shields.io/badge/ComfyUI-workflow-2b2b2b)](https://github.com/comfyanonymous/ComfyUI)
[![Local processing](https://img.shields.io/badge/LM%20%2F%20speech-local-2ea44f)](#lm-studioを設定する)

## できること

| 方式 | 必要な入力 | 向いている用途 |
|---|---|---|
| **T2V** | 画像なし | 人物、背景、動作を文章から作る。初回の動作確認にも向く |
| **I2V** | 開始画像1枚 | 手元の画像を最初のフレームとして、その構図から動かす |
| **Ref2V** | 参照画像1〜5枚 | 人物、顔、髪型、服装などを参照し、別の場面や構図を作る |

主な機能は次のとおりです。

- 映像はFused 4ステップ＋SLA、音声は追加2ステップ・denoise 0.5
- 日本語の指示をLM StudioのローカルモデルでH3向けプロンプトへ変換
- 完成済みの英語H3プロンプトを直接入力する経路
- 15秒固定ではない可変尺。長尺は複数区間に分けて生成・結合
- 区間ごとの画像検査、日本語音声検査、初回を含む累計最大5回の区間試行
- 中間データを使った途中再開と、指定区間からの再生成
- モデル取得URL、配置先、サイズ、SHA-256を [`models.json`](models.json) に収録
- クラウドAPIキー不要。日本語変換、画像検査、音声検査はローカル処理

区間試行は必ず5回行う仕組みではありません。合格した時点で次の区間へ進み、上限に達した場合は条件に合う候補から最良のものを使用します。AI検査にも見落としがあるため、完成動画は最後に目と耳で確認してください。

## v1.1.8の変更点

日本語入力からLM Studioを使う処理に、画面上部の進捗表示を追加しました。

- 実行を受け付けたことを表示
- 処理中は経過秒を更新
- 完了またはエラーでカウントを停止
- 経過秒には、接続、LMモデルのGPU切替、文章変換、CPU復帰を含む

表示される秒数は**完了率や残り時間ではありません**。完成済み英語プロンプトを直接使い、文章変換を行わない場合は、この変換表示は出ません。更新後はComfyUIを完全に再起動し、ワークフローを保存してブラウザーを再読み込みしてください。

## 配布内容

Release ZIPには次のファイルが入っています。

```text
H3_T2V-I2V-Ref2V_20260911222017_v1.1.8/
├─ workflows/                 # T2V・I2V・Ref2Vのワークフロー3本
├─ custom_nodes/              # 導入に必要なカスタムノード5パッケージ
├─ requirements.txt           # Python依存ライブラリ
├─ models.json                # モデル名・配置先・取得URL・ハッシュ
├─ configure_audio_audit.py   # ローカルWhisper設定補助
├─ verify_package.py          # 展開した配布物の検査
├─ README.md / README_JA.md
├─ VALIDATION.md
├─ LICENSES_AND_NOTICES.md
└─ SHA256SUMS.json
```

モデル本体、入力画像、生成動画、APIキー、個人用プロンプト、読み辞書、個人PCのパス設定は含みません。

## 必要な環境

### 必須

- MiniMax H3とV3ノードAPI、`ResolutionSelector`に対応した [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- NVIDIA CUDA版PyTorchを使用できる環境
- 使用中のPyTorchと互換性があるTriton。Windowsネイティブでは対応する `triton-windows`
- `ffmpeg` と `ffprobe` がPATHから実行できること
- H3用モデル4ファイル（合計約40.44GB）
- 日本語変換・区間AI検査を使う場合はLM Studioと画像対応ローカルモデル
- 日本語音声検査を使う場合はTransformers形式のWhisperモデル

確認に使用した構成はWSL2 Ubuntu、NVIDIA RTX 5080 16GBです。16GBは最低要件でも、すべての解像度・尺で動く保証でもありません。H3モデル、LM Studioモデル、中間データ、完成動画を置くため、ストレージにはモデル容量より十分大きい空きを確保してください。

> [!NOTE]
> Windowsネイティブ、別GPU、別PCでのGPU生成は未確認です。まず配布時の **5秒・16:9・0.4MP** で動作を確認してください。

## インストール

### 1. Release ZIPを取得して展開する

1. [最新Release](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/latest)を開きます。
2. Assetsから `H3_T2V-I2V-Ref2V_20260911222017_v1.1.8.zip` を取得します。
3. ZIPをすべて展開します。ZIPの中からJSONだけを取り出さないでください。
4. 既に同名のカスタムノードを使っている場合は、ComfyUIの外へ退避します。

### 2. 展開した配布物を検査する

展開先で次を実行します。モデルやGPUは不要です。

```bash
python -B verify_package.py
```

Windowsで `python` が見つからない場合は、インストール済みのPythonに合わせて `py -B verify_package.py` などへ置き換えます。次の表示で終了すれば配布内容は正常です。

```text
Workflow OK: I2V
Workflow OK: Ref2V
Workflow OK: T2V
Package checks passed.
```

### 3. カスタムノード5フォルダーを配置する

展開したZIPの `custom_nodes/` にある次の5フォルダーを、ComfyUI本体の `custom_nodes/` へコピーします。

```text
ComfyUI/
└─ custom_nodes/
   ├─ comfyui-h3-standard-prompt/
   ├─ ComfyUI-MiniMax-H3-Long-Video/
   ├─ ComfyUI-H3-AudioRefine/
   ├─ ComfyUI-PlagueKind-Nodes/
   └─ ComfyUI-Custom-Scripts/
```

各フォルダーの直下に `__init__.py` がある状態が正しい配置です。次のように同じフォルダー名が二重にならないようにしてください。

```text
# 誤った例
ComfyUI/custom_nodes/ComfyUI-H3-AudioRefine/ComfyUI-H3-AudioRefine/__init__.py
```

外部由来の4パッケージもRelease ZIPに同梱済みです。別途取得した版や古い版と混在させないでください。

### 4. Python依存ライブラリを入れる

依存ライブラリは、**ComfyUIが実際に使用するPython**へインストールします。

Windows Portable版では、Portableのルートから実行します。

```powershell
.\python_embeded\python.exe -m pip install -r "C:\展開先\H3_T2V-I2V-Ref2V_20260911222017_v1.1.8\requirements.txt"
```

Linux・WSLのvenv版では、ComfyUIフォルダーから実行します。

```bash
.venv/bin/python -m pip install -r "/展開先/H3_T2V-I2V-Ref2V_20260911222017_v1.1.8/requirements.txt"
```

SLAに必要なTritonはOSとPyTorchの組み合わせに合わせて別途用意します。Windowsネイティブの情報は [triton-windows](https://github.com/woct0rdho/triton-windows) を確認してください。

ComfyUIのPythonでCUDAとTritonを確認します。

```bash
python -c "import torch,triton; print(torch.__version__, torch.cuda.is_available(), triton.__version__)"
```

`torch.cuda.is_available()` が `False` の場合は、先にPyTorchとCUDA環境を確認してください。ここでの `python` も、Portable版なら `python_embeded/python.exe`、venv版なら `.venv/bin/python` へ置き換えます。

FFmpegも確認します。

```bash
ffmpeg -version
ffprobe -version
```

## H3モデル4ファイルを配置する

モデルは合計約40.44GBです。リンクを開く前に、各モデルの利用条件も確認してください。

| 種類 | ファイル名・取得先 | 配置先 | 容量の目安 |
|---|---|---|---:|
| 動画生成モデル | [`minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors`](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/main/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors) | `ComfyUI/models/diffusion_models/` | 20.98GB |
| H3用テキストエンコーダー | [`qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) | `ComfyUI/models/text_encoders/` | 15.69GB |
| 映像VAE | [`minimax_h3_video_vae_int8_convrot.safetensors`](https://huggingface.co/Kijai/MiniMax-H3-experimental/resolve/main/minimax_h3_video_vae_int8_convrot.safetensors) | `ComfyUI/models/vae/` | 3.17GB |
| 音声VAE | [`minimax_h3_audio_vae_fp32.safetensors`](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors) | `ComfyUI/models/vae/` | 0.61GB |

配置後は次の構成になります。

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

モデルを `workflows/` や `custom_nodes/` へ置かないでください。ファイル名が似ている別モデルでも、量子化方式やローダーの互換性が同じとは限りません。最初は表の指定ファイルを使用してください。正確なバイト数とSHA-256は [`models.json`](models.json) にあります。

## LM Studioを設定する

LM Studioは動画生成モデルではありません。日本語の動画指示と入力画像を読み、H3向けの英語プロンプトへ整えるために使います。区間AI検査を有効にしている場合は、英語直接入力でも検査時にLM Studioを使います。

1. [LM Studio](https://lmstudio.ai/)をインストールします。
2. 画像認識に対応したローカルモデルを取得します。検証では **Qwen3.5-9B** を使用しました。
3. I2V・Ref2Vでは画像も渡すため、必要なVision用ファイルを揃えます。
4. Developer画面でモデルをロードし、ローカルサーバーを起動します。
5. ワークフローの「LM Studioのモデル名」を、LM Studio側の識別子と一致させます。

配布時の設定例は次のとおりです。

```text
接続先: http://127.0.0.1:1234/v1
モデル識別子: qwen-prompt-ja
```

`qwen-prompt-ja` は任意の識別子です。LM Studioで別名を使う場合はワークフロー側も変更してください。詳しいサーバー操作は [LM Studio Server](https://lmstudio.ai/docs/developer/core/server) を参照してください。

### LMモデルをGPUからCPUへ戻す設定

「LM変換時だけGPUへ読み込み、動画生成前にCPUへ戻す」を有効にする場合は、LM Studio同梱の `lms` CLIをPATHへ登録し、ComfyUIのPythonに `lmstudio` パッケージが必要です。CPU復帰に失敗した場合は、VRAM競合を避けるため動画生成を開始しません。

この自動切替を使えない環境では設定をOFFにし、LMモデルを最初からCPUへロードしてください。文章変換は遅くなりますが、H3が使うVRAMとの競合を抑えられます。

### WSLからWindowsのLM Studioへ接続する場合

ComfyUIがWSL、LM StudioがWindowsで動いている場合、WSL側の `127.0.0.1` からWindows側へ届かないことがあります。その場合は、WSLから到達できるWindows側のアドレスを接続先に設定します。

```text
http://到達できるホストアドレス:1234/v1
```

ComfyUIを動かしているOS側から確認します。

```bash
curl http://ホストアドレス:1234/api/v1/models
```

外部インターネットへLM Studioのポートを公開する必要はありません。

## 日本語音声検査用Whisperを設定する

音声検査には、[Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo)の**Transformers形式**を使います。GGUF形式ではありません。モデル本体、`config.json`、`preprocessor_config.json`、トークナイザー関連ファイルを同じフォルダーへ置きます。

展開したRelease ZIPのフォルダーで、ComfyUIのPythonを使って次を実行します。

```bash
python configure_audio_audit.py --comfyui "ComfyUI本体のフォルダー" --whisper-model "Whisperモデルのフォルダー"
```

Windows Portable版の例です。

```powershell
C:\ComfyUI_windows_portable\python_embeded\python.exe configure_audio_audit.py --comfyui "C:\ComfyUI_windows_portable\ComfyUI" --whisper-model "D:\AI_Models\whisper-large-v3-turbo"
```

設定先は、ComfyUIへ配置したLong-Videoノード内の `local_audio_audit.json` です。既存設定はバックアップされます。環境変数 `H3_LOCAL_WHISPER_MODEL` で指定することもできます。

音声検査はCPUで実行し、音声を外部へ送信しません。モデルの自動ダウンロードも行いません。検査をONにしたままモデル未設定の場合は実行を停止します。短いうなり声などを見落とす場合があるため、最終動画は必ず試聴してください。

## 最初の動画を生成する

環境を確認しやすいT2Vから、短い設定で始めます。

1. ComfyUIを完全に再起動します。
2. `workflows/T2V_H3_T2V-I2V-Ref2V_4step_20260911222017_v1.1.8.json` を読み込みます。
3. 赤い不足ノードがないことを確認します。
4. 4つのモデルローダーで、指定ファイルが選択されていることを確認します。
5. 日本語入力または区間AI検査を使う場合は、LM Studioのサーバーとモデルを起動します。
6. 秒数を **5秒**、画面比率を **16:9**、画素数を **0.4MP** にします。
7. 本文にも尺を書く場合は「全体5秒」または `Duration: 5 seconds` にそろえます。
8. 「実行する」を押し、最終プロンプト、実行計画、完成動画を確認します。

初回はモデルの読み込みに時間がかかります。T2Vが動いたら、用途に応じてI2VまたはRef2Vへ進みます。

### I2Vを使う

`workflows/I2V_H3_T2V-I2V-Ref2V_4step_20260911222017_v1.1.8.json` を開き、「開始画像」へ画像を1枚アップロードします。入力画像と動画の比率を最初はそろえると、切り抜きやリサイズの影響を確認しやすくなります。

### Ref2Vを使う

`workflows/Ref2V_H3_T2V-I2V-Ref2V_4step_20260911222017_v1.1.8.json` を開き、「参照画像1」へ画像を入れます。画像1は必須です。画像2〜5を使う場合は順番に画像を入れ、対象ノードを選択して `Ctrl+B` で有効にします。空の画像ノードを有効にすると実行前に止まります。

複数画像を使う場合は、役割を文章で分けてください。

```text
画像1は人物の顔と髪型の参照。
画像2は服装の参照。
画像3は背景の雰囲気の参照。
完成動画には人物を一人だけ登場させる。
```

## プロンプト入力欄の使い分け

大きなプロンプトノードには、役割が異なる3つの欄があります。

| 欄 | 入れる内容 | 動作 |
|---|---|---|
| ① 一番上 | 日本語などで書いた動画の指示 | LM StudioでH3向け文章へ変換 |
| ② 中央 | LM Studioへ渡す基本ルール | 通常は変更しない |
| ③ 一番下 | 完成済みの英語H3プロンプト | ①が空欄のとき、そのまま使用 |

①に文章がある場合は①が優先され、LM Studioによる変換が行われます。③を直接使う場合は①を完全に空にしてください。①と③が両方空の場合は実行できません。

英語を直接使うT2Vの最小例です。

```text
Duration: 5 seconds

integrated_multimodal_description: Soft anime style. One adult traveler stands on a quiet park path, waves once slowly at the camera, then rests their hand and smiles. A gentle breeze moves their hair and jacket. One continuous shot with a fixed camera. No speech or on-screen text.

overall_soundscape: Soft wind, rustling leaves and distant birds.

non_diegetic_music: N/A
```

「H3へ渡すプロンプト」や実行計画の表示欄は確認用です。次回の入力を変える場合は①または③を編集してください。

## 秒数・解像度・音の既定値

| 項目 | 配布時の設定 |
|---|---|
| 秒数 | 15秒。初回確認は5秒を推奨 |
| フレームレート | 24fps |
| 画面比率 | 16:9 |
| 画素数 | 0.4MP |
| 実出力の確認例 | 864×480 |
| 映像 | `res_multistep` / `simple` / 4ステップ |
| SLA | 0.9 |
| 音声再精錬 | 2ステップ / denoise 0.5 |
| 区間試行 | 初回を含む累計最大5回。合格時は早期終了 |

本文に `Duration: 30 seconds` や「全体30秒」と書いた場合は、数値欄より本文の明示尺が優先されます。尺指定は本文中で一つに統一してください。実行後は実行計画の `duration_seconds` と `segment_count` を確認します。

長尺は区間に分けて生成します。39フレーム継続条件の30秒構成では、13＋13＋4秒の3区間として生成し、24fps・720フレームへ結合します。秒数を増やす場合は、前半・後半で何をするかも文章に書くと、同じ動作の不自然な反復を抑えやすくなります。

BGMはプロンプトで指定します。BGMなしの場合は `non_diegetic_music: N/A` と書きます。これは手元の音楽ファイルを後付けする機能ではなく、H3へ音楽生成を指示する項目です。

## 保存先

完成動画はComfyUIの `output` 以下へ保存されます。

```text
ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/T2V/
ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/I2V/
ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/Ref2V/
```

中間データは次の場所に保存されます。

```text
ComfyUI/output/h3_long_video/H3_T2V-I2V-Ref2V/<方式>/<キャッシュ>/
```

長尺ほど区間データ、プロンプト、潜在データが増えます。空き容量を定期的に確認してください。

## 途中から再開・区間を再生成する

再開時は、最初の実行と次の条件をそろえます。

- `cache_name`：実際に作成された同じキャッシュ名を固定
- 使用モデル、入力画像、シード、指定尺：最初の実行と同じ
- `noise_seed`：`fixed`
- `resume`：ON
- `reroll_from_segment`：再生成を始める区間。**0始まり**
- `stop_after_segment`：途中で止める場合だけ区間番号を指定。最後まで進める場合は `-1`
- `reroll_feedback`：現在区間で直したい点を記入可能

30秒を13＋13＋4秒に分ける構成では、`reroll_from_segment=1` は13〜26秒以降、`2` は26〜30秒だけを再生成します。別の `cache_name` では以前の試行回数や中間状態を引き継げません。

## よくあるトラブル

| 症状 | 確認すること |
|---|---|
| ノードが赤い・不足と表示される | 5フォルダーを正しく配置したか、二重フォルダーになっていないか、ComfyUIのPythonへ依存を入れたか、起動ログにImportErrorがないか |
| モデルが一覧に出ない | ファイル名、拡張子、配置先、ダウンロード完了を確認し、モデル一覧の更新またはComfyUI再起動を行う |
| CUDAが使えない | ComfyUIのPythonで `torch.cuda.is_available()` を確認し、NVIDIA CUDA版PyTorchとドライバーを確認する |
| Triton関連で止まる | PyTorch、CUDA、OSに対応するTritonか確認する。Windowsネイティブは `triton-windows` の対応表を確認する |
| `ffmpeg` / `ffprobe` が見つからない | 両コマンドがPATHから実行できるか確認する |
| LM Studioへ接続できない | サーバー起動、モデルのロード、識別子、接続先を確認する。WSLではWindows側へ届くアドレスを使う |
| LM Studioサーバーは動くが404 | 指定した識別子のモデルがロード中か、TTLなどでアンロードされていないか確認する |
| LMモデルのCPU復帰で止まる | `lms` CLIと `lmstudio` パッケージを確認する。自動切替を使わない場合はOFFにしてCPUロードする |
| 音声検査で止まる | Transformers形式Whisperのフォルダーを設定したか、必要ファイルが同じ場所にあるか確認する |
| I2V・Ref2Vが実行前に止まる | 必須画像を入れたか、空の追加画像ノードを有効にしていないか確認する |
| メモリー不足 | 0.4MP・5秒へ戻す。LM StudioモデルをCPUへ移す。モデル読込時点の不足は尺短縮だけでは解消しない場合がある |
| 秒数欄を変えても長さが変わらない | 本文中の `Duration` または「全体○秒」が優先されていないか確認する |
| 同じ動作が繰り返される | 長尺の各区間で何をするかを明記し、最終プロンプトと実行計画を確認する |
| BGMや台詞が意図と違う | 実際にH3へ渡された最終プロンプトの `overall_soundscape` と `non_diegetic_music`、台詞指定を確認する |

## 検証済みの範囲と制限

- 開発環境ではT2V・I2V・Ref2Vの実生成を実施済み
- T2Vの30秒生成で、中盤まで生成したキャッシュから終盤だけ再開し、30秒・720フレームへの到達を確認
- v1.1.8では、3方式のJSON、5パッケージの読み込み、30秒計画、日本語変換の受付・経過秒・完了・エラー、配布ZIPの再構築を検査
- v1.1.8の進捗表示変更に対する新しいGPU動画生成は未実施
- 別PCとWindowsネイティブでのGPU実行は未確認

生成モデルと自動検査には限界があります。人物や服装の全フレーム一致、長尺の継ぎ目、台詞の正確な発音、不要音や字幕の完全排除を保証するものではありません。過去の確認動画では軽微な字幕や短いうなり声が残った例があります。完成動画を再生し、映像と音声の両方を確認してください。

詳しい検証条件は [VALIDATION.md](VALIDATION.md)、同じ配布物を再構築する手順は [REPRODUCE.md](REPRODUCE.md) にあります。

## プライバシーと通信

- クラウドAPIキーは不要です。
- LM Studioとの通信先はローカルサーバーです。
- Whisper音声検査はローカルCPUで実行し、音声を外部へ送信しません。
- Release ZIPに入力画像、生成物、認証情報、個人パス、個人用設定は含みません。
- WSLとWindows間のローカル接続に、外部インターネットへのポート公開は不要です。

## ライセンス

この配布セットは複数のライセンスを含みます。主な構成は次のとおりです。

| パッケージ | ライセンス |
|---|---|
| `ComfyUI-MiniMax-H3-Long-Video` | GPL-3.0-only |
| `ComfyUI-H3-AudioRefine` | MIT |
| `ComfyUI-PlagueKind-Nodes` | MIT |
| `ComfyUI-Custom-Scripts` | MIT |
| `comfyui-h3-standard-prompt` | 独自作成部分はMIT。結合構成全体の条件は同梱文書を確認 |

再配布・改変時は [LICENSES_AND_NOTICES.md](LICENSES_AND_NOTICES.md) と各フォルダーの `LICENSE` を確認し、著作権表示とライセンス本文を保持してください。

モデル本体には各配布元の条件が適用されます。MiniMax H3には地域、用途、商用利用、再配布などを定めた [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) があります。利用前に、自分の地域と用途が条件を満たすか原文で確認してください。

## 関連ドキュメント

- [README_JA.md](README_JA.md)：ノードごとの詳しい導入・操作説明
- [VALIDATION.md](VALIDATION.md)：検証した内容と未確認範囲
- [REPRODUCE.md](REPRODUCE.md)：配布ZIPの決定的な再構築手順
- [LICENSES_AND_NOTICES.md](LICENSES_AND_NOTICES.md)：ライセンスと再配布時の表示
- [`models.json`](models.json)：モデルの取得先、配置先、容量、SHA-256
- [note解説記事](https://note.com/happy_duck780/n/n15e732b3147b)：画面の見方、入力例、導入と操作の詳しい説明
- [GitHub Releases](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases)：配布ZIPと更新履歴

## English quick start

This package provides three local ComfyUI workflows for MiniMax H3: T2V from text, I2V from one starting image, and Ref2V from one to five reference images. Download the named ZIP from the [latest release](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/latest), extract it, run `python -B verify_package.py`, copy all five folders under `custom_nodes/` into `ComfyUI/custom_nodes/`, and install `requirements.txt` with the Python interpreter used by ComfyUI.

Download the four model files listed above and place them under `models/diffusion_models`, `models/text_encoders`, and `models/vae`. A compatible NVIDIA CUDA/PyTorch/Triton environment and FFmpeg are required. LM Studio is used locally for Japanese prompt conversion and visual segment review; a local Transformers-format Whisper model is used for Japanese speech review. No cloud API key is required. Start with the T2V workflow at 5 seconds, 16:9, and 0.4MP. Hardware outside the documented development environment has not been verified.
