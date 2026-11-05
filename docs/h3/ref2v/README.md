# Ref2V — Reference images to video / 参照画像から動画
<!-- bilingual-readme: paired english-japanese -->

[![H3 Video note cover / H3 Videoのnote見出し画像](https://raw.githubusercontent.com/FURUYAN1234/comfyui-h3-workflows/main/docs/assets/h3/video-cover.png)](https://furuyan1234.github.io/comfyui-h3-workflows/h3-video/demo/)

**[Open the H3 Video guide / H3 Videoの案内ページを開く →](https://furuyan1234.github.io/comfyui-h3-workflows/h3-video/demo/)**

One to five references for characters, clothing, objects or locations. References are not a fixed first frame. / 人物・服装・物体・場所などの参照画像1～5枚。開始フレーム固定ではありません。

## Download / 入手先

[Download H3_Video_20261105183000_v1.2.1.zip / 動画3方式のZIPを取得](https://github.com/FURUYAN1234/comfyui-h3-workflows/releases/download/h3-split-20261105183000/H3_Video_20261105183000_v1.2.1.zip). This package contains T2V, I2V and Ref2V; open only **Ref2V_H3_T2V-I2V-Ref2V_4step_20260923075019_v1.2.1.json** for this task. MV and KomaOrbit are not included or required. / 3本セットの中から、今回は **Ref2V_H3_T2V-I2V-Ref2V_4step_20260923075019_v1.2.1.json** だけを開きます。MV・KomaOrbitは同梱せず、別途導入も不要です。

[Workflow JSON only / 導入済みの方向けJSON単体](https://raw.githubusercontent.com/FURUYAN1234/comfyui-h3-workflows/main/workflows/Ref2V_H3_T2V-I2V-Ref2V_4step_20260923075019_v1.2.1.json) · [Shared setup / 共通導入](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/docs/h3/SETUP.md)

## Requirements and installation / 必要な環境と導入

Use ComfyUI with H3 support, CUDA PyTorch, matching Triton, FFmpeg/ffprobe, H3’s four model files and LM Studio with a vision-capable model. Validation used WSL2 and RTX 5080 16GB; that is not a guaranteed minimum. / H3対応ComfyUI、CUDA版PyTorch、対応するTriton、FFmpeg／ffprobe、H3用4モデル、画像対応モデルを備えたLM Studioを使います。検証はWSL2・RTX 5080 16GBで、最低要件の保証ではありません。

1. Extract the entire video ZIP and run `python -B verify_package.py`. / 動画ZIP全体を展開し、検査を実行します。
2. Copy its five `custom_nodes/` folders directly into ComfyUI’s `custom_nodes/`; back up old same-named folders first. / 同梱5フォルダーをComfyUIの同名場所へ配置し、旧版は先に退避します。
3. With ComfyUI’s Python, run `python -m pip install -r requirements.txt`. Obtain the four models listed in `models.json` separately. / ComfyUI用Pythonで依存ライブラリを入れ、`models.json`の4モデルを別途配置します。
4. Configure LM Studio’s endpoint/model ID and local Whisper speech review, following the included `docs/h3/SETUP.md`. Restart ComfyUI. / 同梱の共通導入に従ってLM StudioとローカルWhisper音声検査を設定し、ComfyUIを再起動します。

## Inputs and first run / 入力と最初の実行

Fill 「参照画像1：アップロード（必須）」. For each extra image, fill its node and enable it with Ctrl+B. Describe each image’s role in 「動画指示」 and run a 5-second test. / 「参照画像1：アップロード（必須）」へ画像を入れます。2～5枚目を使うときだけ画像を設定し、対象ノードをCtrl+Bで有効にします。「動画指示」に各画像の役割を書き、5秒で試します。

Example direction (Japanese; replace it with your scene) / 入力例（作りたい場面に書き換えます）:

```text
全体5秒。画像1は人物の顔と髪型、画像2は衣装の参照。登場人物は一人。その人物が静かな道を歩く。服装を途中で変えない。台詞・歌・BGM・字幕なし。
```

For a one-image test, remove the image-2 sentence from the example. Enable only filled reference nodes; an empty enabled input causes an error. / 1枚だけで試す場合は入力例の画像2の文を削除します。画像を設定したノードだけを有効にし、空欄を有効にしないでください。

The default is 15 seconds, 16:9, 0.4MP, 24fps. A duration explicitly written in the prompt overrides the numeric field, so make them agree. Video sampling remains `res_multistep` / `simple` / 4 steps, SLA 0.9, with audio refinement at 2 steps and denoise 0.5. / 既定は15秒・16:9・0.4MP・24fpsです。文章内の明示尺が数値欄より優先されるため、初回は両方5秒に合わせます。映像4ステップ、SLA 0.9、音声再精錬2ステップ・denoise 0.5を維持しています。

Japanese instructions use LM Studio. Direct English input is possible by clearing the top Japanese field and filling the bottom completed-prompt field. AI review may still use LM Studio. / 日本語入力ではLM Studioを使います。上の日本語欄を空にして下の完成プロンプト欄へ英語を入れる直接入力も可能ですが、区間AI検査がONなら検査時にはLM Studioを使います。

## Outputs and review / 出力と確認

The save node 「変更場所：完成動画の保存先（output基準）」 writes MP4 beneath `ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/`. Play the preview and check the whole picture and sound. / 保存ノードから左記フォルダーへMP4を出力します。プレビューを再生して、人物数・髪型・衣装・台詞・音・尺を確認してください。

[Resume, reroll and troubleshooting / 再開・区間再生成・トラブル対応](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/docs/h3/SETTINGS.md). Keep the same cache name and inputs when resuming; do not delete segment caches before completion. / 再開は同じキャッシュ名と入力で行い、完了前に区間キャッシュを消さないでください。

## Limits and validation / 制約と検証

This is generative video; identity and dialogue may drift. The recorded Ref2V normal-path check produced 5.000-second 864×480 24fps H.264/AAC output. This distribution revision checks packaging and unchanged runtime hashes, and does not claim a new GPU run for every workflow. / 生成方式のため人物や台詞が変化することがあります。記録済みのRef2V通常経路では5.000秒・864×480・24fpsのH.264/AACを確認しています。今回の改訂は配布構成と元コードの一致を検査するもので、全方式を今回新しくGPU生成したという意味ではありません。

## Licenses and links / ライセンスと関連リンク

Model weights and input media are not included. Original FURU portions are MIT; Long Video is GPL-3.0-only and other bundled code keeps its own license. Model terms are separate. / モデル重み・入力素材は同梱しません。FURU独自部分はMIT、Long VideoはGPL-3.0-onlyなど、第三者部分は各条件を保持します。モデルの条件は別です。

[License scope / 権利範囲](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/LICENSE_SCOPE.md) · [Notices / 権利表示](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/LICENSES_AND_NOTICES.md) · [Validation / 検証記録](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/VALIDATION.md) · [Japanese article / note解説](https://note.com/happy_duck780/n/n15e732b3147b) · [Choose a workflow / 一覧へ](https://github.com/FURUYAN1234/comfyui-h3-workflows)
