> 2026-09-26 更新：Ref2V・MVの複数参照画像は番号順に全枚数を生成へ渡します。参照番号に対して画像が足りない場合は生成前に止めます。配布版は[v1.2.0-ref1](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/tag/v1.2.0-ref1-ref1)です。

# 〖無料配布・ComfyUI〗YuE2で作った曲をMV化。可変尺・字幕・元曲リップシンク対応 MiniMax H3ワークフロー

YuE2などで作った「完成音源＋歌詞」とキャラクター画像を入れ、日本語で雰囲気を指示すると、MiniMax H3でMVを作るComfyUIワークフローを公開します。

短い確認用MVは20秒など任意の秒数、完成版は音源の途中から曲末までの可変尺で生成できます。最後は映像と音を一緒にフェードアウト。字幕、元曲に合わせた歌唱口パクの指示、左上タイトル、終盤の右下リンク表示もまとめました。

配布ページ：

- GitHub: https://github.com/FURUYAN1234/comfyui-h3-workflows
- 最新Release: https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/latest
- YuE2日本語おまかせ作曲: https://github.com/FURUYAN1234/ComfyUI-YuE2-Japanese
- YuE2の解説note: https://note.com/happy_duck780/n/n57df44cf7fd2

初回はGitHubの「Source code」やJSON単体ではなく、ReleaseのAssetsにある `H3_T2V-I2V-Ref2V-MV_20260926115639_v1.2.0-ref1.zip` を使ってください。

![YuE2からMVへの連携](assets/workflow-bridge-yue2-to-mv-v1.2.0.png)

## 2つのワークフローをつなぐ

今回のポイントは、作曲とMV生成を別々の手作業でつなぐのではなく、受け渡し仕様を決めたことです。

1. YuE2ワークフローが曲を完成
2. 通常の曲フォルダーとは別に、同じ実行から `ComfyUI/output/mv-assets/曲名_日時_ID/` を作成
3. MVワークフローが、そのフォルダーとキャラクター画像を読む
4. LM Studioが日本語の希望からMV演出を組み立てる
5. MiniMax H3が映像を生成
6. 最後に元の完成曲へ戻し、字幕・タイトル・リンク・フェードを付けて `final.mp4` を保存

YuE2側は作曲システム、こちらは映像システムです。それぞれ単独でも使えますが、2本をつなぐと「日本語で曲を作る→その曲をMV化する」まで同じ素材仕様で進められます。

## MV素材フォルダーの中身

YuE2 v1.6.0の⑥「曲完成と同時にMV素材を書き出す」が、次を作ります。

```text
ComfyUI/output/mv-assets/曲名_日時_ID/
├─ master.flac
├─ lyrics_display.txt
├─ lyrics_singing.txt
├─ lyrics.json
├─ mv_manifest.json
└─ README.txt
```

`master.flac` は完成した元曲、`lyrics_display.txt` は表示用の正確な歌詞、`lyrics_singing.txt` は歌唱用の読みです。`mv_manifest.json` にはファイル名、ハッシュ、音源情報、歌詞の時刻状態を記録します。

歌詞の時刻は作曲時に推測して埋めません。まず `not_aligned` として安全に渡し、MV側で最終音源にWhisperを当てて時刻合わせします。字幕と口パクの基準が、編集前の仮音源ではなく完成した `master.flac` になるためです。

書き出しノードは標準ComfyUI `AUDIO` と任意メタデータを受ける作りです。同じバンドル仕様を作れば、YuE2以外の歌声合成システムにも応用できます。

## MVワークフローの画面

![MVワークフロー](assets/workflow-mv-v1.2.0.png)

左から順に、入力、キャラクター画像、LM Studioの演出設計、H3モデル、可変尺生成、元曲での仕上げ、プレビュー／ダウンロードです。説明枠をワークフロー内へ置いたので、READMEを開き直さなくても変更する場所を確認できます。

主な機能：

- 日本語で「雰囲気はお任せ」などと入力
- LM StudioのローカルLLMをGPUへ一時ロードし、H3生成前にCPUへ戻す
- 20秒など任意秒数、または音源末尾までの可変尺
- 音源途中の `start_seconds` から開始
- 表示字幕ON/OFF
- 最終音源へWhisperで歌詞時刻合わせ
- 元曲へ合わせたリップシンクをH3プロンプトへ明記
- 元画像が2Dアニメなら2D、3Dなら3D、実写なら実写を維持する指示
- 左上タイトルのフェードアウト
- 終盤に右下リンクを重ね表示
- 最後に映像と音声を同じ秒数でフェードアウト
- 最終ノードで再生とダウンロード

## キャラクターの画風と顔について

キャラクター画像は標準LoadImageから入れます。人物の顔、髪型、髪色、目、体格、服装が分かるシートが向いています。

ワークフローは入力画像を見て、2Dアニメ、3Dレンダー、実写のどれかを判定し、その見た目を維持するようLM Studioへ指示します。アニメ絵を勝手に3D化する、実写をイラスト化する、といった変更を避けるルールも入れています。

ただし、H3の参照画像は厳密な顔置換やID固定ではありません。顔・髪・服装の一致は完成映像を目視してください。似ない場合は、正面顔と全身がはっきりしたシートへ変更、1カットを短くする、同時登場人数を減らす、崩れた区間だけ再生成、という順で切り分けるのがおすすめです。

## 必要な環境

動作確認環境はWSL2 Ubuntu、NVIDIA RTX 5080 16GBです。16GBは最低要件でも、すべての解像度・尺を保証する値でもありません。

必要なもの：

- MiniMax H3/V3ノードAPIとResolutionSelectorに対応したComfyUI
- NVIDIA CUDA版PyTorch
- 使用中のPyTorchへ合うTriton（Windowsは対応するtriton-windows）
- `ffmpeg` と `ffprobe`
- H3用モデル4ファイル（合計約40.44GB）
- LM Studioと画像対応ローカルモデル
- 字幕を使う場合はTransformers形式のWhisper large-v3-turbo
- YuE2連携を使う場合はComfyUI-YuE2-Japanese v1.6.0

モデル本体、LM Studio、ComfyUI、生成曲、入力画像、APIキーはZIPへ同梱していません。各モデルの利用条件も取得元で確認してください。MiniMax H3には地域・用途・商用利用・再配布に関するCommunity Licenseがあります。

## 1. 配布ZIPを検査する

Release AssetsからZIPを取得して展開し、展開先で次を実行します。ここはGPUやモデルなしでも確認できます。

```bash
python -B verify_package.py
```

Windows Portableで `python` が見つからない場合は、`py -B verify_package.py` など自分のPythonへ置き換えます。

## 2. カスタムノード6フォルダーを配置

展開した `custom_nodes/` の6フォルダーを、ComfyUIの `custom_nodes/` 直下へコピーします。

```text
ComfyUI/custom_nodes/
├─ comfyui-h3-standard-prompt/
├─ ComfyUI-MiniMax-H3-Long-Video/
├─ ComfyUI-H3-AudioRefine/
├─ ComfyUI-PlagueKind-Nodes/
├─ ComfyUI-Custom-Scripts/
└─ comfyui-mv-workflow/
```

同名フォルダーが二重にならないようにします。以前の同名版がある場合は、先にComfyUIの外へ退避してください。

## 3. Python依存を入れる

ComfyUIが実際に使うPythonで実行します。

Windows Portable例：

```powershell
.\python_embeded\python.exe -m pip install -r "C:\展開先\H3_T2V-I2V-Ref2V-MV_20260926115639_v1.2.0-ref1\requirements.txt"
```

WSL/Linux venv例：

```bash
.venv/bin/python -m pip install -r "/展開先/H3_T2V-I2V-Ref2V-MV_20260926115639_v1.2.0-ref1/requirements.txt"
```

続けて `ffmpeg -version`、`ffprobe -version`、ComfyUIのPythonで `torch.cuda.is_available()` を確認します。

## 4. H3モデルを配置

ファイル名・取得先・SHA-256はZIP内 `models.json` に収録しています。

```text
ComfyUI/models/diffusion_models/
  minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors
ComfyUI/models/text_encoders/
  qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
ComfyUI/models/vae/
  minimax_h3_video_vae_int8_convrot.safetensors
  minimax_h3_audio_vae_fp32.safetensors
```

似た名前の別量子化版ではなく、最初はワークフロー指定のファイルを使ってください。

## 5. LM Studioを設定

LM StudioのDeveloper画面で画像対応モデルをロードし、ローカルサーバーを起動します。検証ではQwen3.5-9Bを使用しました。ワークフロー側のモデル識別子をLM Studio側と一致させます。

```text
Endpoint: http://127.0.0.1:1234/v1
Model identifier example: qwen-prompt-ja
```

ComfyUIがWSL、LM StudioがWindowsの場合は、WSLから到達できるWindows側アドレスを使います。ポートを外部インターネットへ公開する必要はありません。

「LLM変換時にGPUを使用し、動画生成前にCPUへ戻す」を使う場合、LM Studio同梱 `lms` CLIをPATHへ登録し、ComfyUIのPythonへ `lmstudio` を導入します。自動切替できない環境は設定をOFFにして、最初からCPUロードにすると遅くなりますがH3とのVRAM競合を避けられます。

## 6. Whisperを配置

字幕を使う場合は、GGUFではなくTransformers形式の `openai/whisper-large-v3-turbo` 一式を用意します。

```text
ComfyUI/models/whisper/whisper-large-v3-turbo/
├─ config.json
├─ preprocessor_config.json
├─ tokenizer関連ファイル
└─ モデル重み
```

ワークフローの `whisper_model_path` 既定値は `models/whisper/whisper-large-v3-turbo` です。字幕OFFならMV入力処理でWhisperを呼びません。

## 7. まず20秒で確認

1. ComfyUIを完全に再起動
2. `workflows/MV_H3_YuE2-LMStudio_v1.2.0-ref1.json` を開く
3. 赤い不足ノードがないことを確認
4. `mv-assets/曲名_日時_ID` を入力
5. キャラクター画像をLoadImageへアップロード
6. 尺モードを「任意秒数」、秒数を20、フェードを1秒
7. 字幕を使う場合は時刻合わせON
8. 日本語欄へ雰囲気を書く。「全部お任せ」でも可
9. 実行し、最終ノードで再生・ダウンロード

20秒版で人物、画風、字幕、口の動き、元曲、タイトル、終了リンク、フェードを確認してから、尺モードを「音源末尾まで（可変尺）」へ変更すると無駄な長時間生成を減らせます。

## 保存先

```text
ComfyUI/output/video/MV/曲名_日時/final.mp4
```

中間のH3区間データは `ComfyUI/output/h3_long_video/` 以下へ保存されます。長い曲ほど区間データと空き容量が増えるので、途中再開に必要なキャッシュを消さないようにしてください。

## 検証した範囲

- 公開MV JSONをComfyUIへ読込：26ノード、35リンク、不足ノードなし
- MVコア8件、可搬性3件、H3 Long Video 186件、H3 Standard Prompt 76件、ルート回帰3件
- 通常経路の検証MV：20.000秒、864×480、24fps、H.264/AAC
- 字幕4区間
- 指定した元音源区間との相関0.999712
- 左上タイトル、終盤右下リンク、映像／音声フェード

これは生成システムの経路確認です。特定の1本が成功しても、すべての曲・キャラクター・PCで同じ結果を保証するものではありません。人物の分身、明らかな髪型不一致、台詞破綻、二重発声、字幕ずれは完成映像を見て、原因に応じて入力・区間・生成設定を修正してください。

## YuE2側もv1.6.0へ更新

YuE2側は、曲完成と同時にMV素材を別フォルダーへ書き出す⑥ノードと、次のMVワークフローを案内する⑦説明枠を追加しました。

![YuE2ワークフロー](assets/workflow-yue2-v1.6.0.png)

既存の曲フォルダーへ余分なファイルを混ぜず、通常曲とMV受け渡し素材を分離しています。歌詞時刻を推測しないこと、manifestとSHA-256で取り違えを検出すること、標準AUDIO入力で他システムへ応用できることが、今回の連携の土台です。

YuE2の導入と作曲側の詳しい説明はこちらです：

https://note.com/happy_duck780/n/n57df44cf7fd2

## ダウンロード

- MV／H3ワークフロー: https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/tag/v1.2.0-ref1
- YuE2日本語おまかせ作曲: https://github.com/FURUYAN1234/ComfyUI-YuE2-Japanese/releases/tag/v1.6.0

どちらも初回はRelease Assetsの名前付きZIPを使い、展開後に `verify_package.py` を実行してください。自動生成されるGitHubのSource code ZIPやJSON単体だけでは、必要なカスタムノードと検査ファイルが揃いません。
