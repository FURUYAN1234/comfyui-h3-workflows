# H3 settings and recovery / H3動画の設定・再開・トラブル対応
<!-- bilingual-readme: paired english-japanese -->

These settings describe T2V/I2V/Ref2V. MV has its own duration and final-audio controls. / T2V・I2V・Ref2Vの設定です。MVの尺と元曲の処理はMV専用ガイドを参照してください。

## Choose the correct prompt field / プロンプト入力欄の使い分け

The large prompt node has three fields with different roles. / 大きなプロンプトノードには、役割が異なる3つの欄があります。

| Field / 欄 | Content / 入れる内容 | Behavior / 動作 |
|---|---|---|
| ① Top / ① 一番上 | Video instructions written in Japanese or another language / 日本語などで書いた動画の指示 | Converted by LM Studio into an H3-oriented prompt / LM StudioでH3向け文章へ変換 |
| ② Middle / ② 中央 | Base rules sent to LM Studio / LM Studioへ渡す基本ルール | Normally leave unchanged / 通常は変更しない |
| ③ Bottom / ③ 一番下 | Finished English H3 prompt / 完成済みの英語H3プロンプト | Used directly when field ① is empty / ①が空欄のとき、そのまま使用 |

When field ① contains text, it takes priority and LM Studio performs the conversion. To use field ③ directly, leave field ① completely empty. Execution is unavailable if both fields ① and ③ are empty. / ①に文章がある場合は①が優先され、LM Studioによる変換が行われます。③を直接使う場合は①を完全に空にしてください。①と③が両方空の場合は実行できません。

Minimal T2V example using direct English input: / 英語を直接使うT2Vの最小例です。

```text
Duration: 5 seconds

integrated_multimodal_description: Soft anime style. One adult traveler stands on a quiet park path, waves once slowly at the camera, then rests their hand and smiles. A gentle breeze moves their hair and jacket. One continuous shot with a fixed camera. No speech or on-screen text.

overall_soundscape: Soft wind, rustling leaves and distant birds.

non_diegetic_music: N/A
```

The displayed "Prompt sent to H3" and execution plan are read-only confirmation outputs. To change the next run, edit field ① or ③. / 「H3へ渡すプロンプト」や実行計画の表示欄は確認用です。次回の入力を変える場合は①または③を編集してください。

## Default duration, resolution, and audio settings / 秒数・解像度・音の既定値

| Item / 項目 | Distributed setting / 配布時の設定 |
|---|---|
| Duration / 秒数 | 15 seconds; 5 seconds recommended for the first check / 15秒。初回確認は5秒を推奨 |
| Frame rate / フレームレート | 24 fps / 24fps |
| Aspect ratio / 画面比率 | 16:9 |
| Pixel target / 画素数 | 0.4 MP / 0.4MP |
| Example actual output / 実出力の確認例 | 864×480 |
| Video / 映像 | `res_multistep` / `simple` / 4 steps / `res_multistep` / `simple` / 4ステップ |
| SLA | 0.9 |
| Audio refinement / 音声再精錬 | 2 steps / denoise 0.5 / 2ステップ / denoise 0.5 |
| Segment attempts / 区間試行 | Up to five total, including the first; ends early when accepted / 初回を含む累計最大5回。合格時は早期終了 |

If the prompt body states `Duration: 30 seconds` or "30 seconds total," that explicit duration overrides the numeric field. Use only one duration statement in the prompt body. After queueing, check `duration_seconds` and `segment_count` in the execution plan. / 本文に `Duration: 30 seconds` や「全体30秒」と書いた場合は、数値欄より本文の明示尺が優先されます。尺指定は本文中で一つに統一してください。実行後は実行計画の `duration_seconds` と `segment_count` を確認します。

Long videos are generated in segments. A 30-second configuration with a 39-frame continuation context uses 13 + 13 + 4-second segments and joins them into 720 frames at 24 fps. For longer durations, describing what happens in the first and second halves helps reduce unnatural repetition of the same action. / 長尺は区間に分けて生成します。39フレーム継続条件の30秒構成では、13＋13＋4秒の3区間として生成し、24fps・720フレームへ結合します。秒数を増やす場合は、前半・後半で何をするかも文章に書くと、同じ動作の不自然な反復を抑えやすくなります。

Specify BGM in the prompt. For no BGM, write `non_diegetic_music: N/A`. This tells H3 whether to generate music; it is not a feature for adding a local music file afterward. / BGMはプロンプトで指定します。BGMなしの場合は `non_diegetic_music: N/A` と書きます。これは手元の音楽ファイルを後付けする機能ではなく、H3へ音楽生成を指示する項目です。

## Output locations / 保存先

Completed videos are saved below ComfyUI's `output` directory: / 完成動画はComfyUIの `output` 以下へ保存されます。

```text
ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/T2V/
ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/I2V/
ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/Ref2V/
```

Intermediate data is saved under: / 中間データは次の場所に保存されます。


```text
ComfyUI/output/h3_long_video/H3_T2V-I2V-Ref2V/<mode>/<cache>/
```


```text
ComfyUI/output/h3_long_video/H3_T2V-I2V-Ref2V/<方式>/<キャッシュ>/
```

Longer videos create more segment data, prompts, and latent data. Check available disk space regularly. / 長尺ほど区間データ、プロンプト、潜在データが増えます。空き容量を定期的に確認してください。

## Resume or regenerate from a segment / 途中から再開・区間を再生成する

When resuming, keep these values consistent with the original run: / 再開時は、最初の実行と次の条件をそろえます。

- `cache_name`: fix it to the exact cache name that was created / `cache_name`：実際に作成された同じキャッシュ名を固定
- Model, input images, seed, and requested duration: keep them the same as the first run / 使用モデル、入力画像、シード、指定尺：最初の実行と同じ
- `noise_seed`: `fixed` / `noise_seed`：`fixed`
- `resume`: ON / `resume`：ON
- `reroll_from_segment`: first segment to regenerate, using a **zero-based index** / `reroll_from_segment`：再生成を始める区間。**0始まり**
- `stop_after_segment`: set a segment number only when stopping partway; use `-1` to continue through the end / `stop_after_segment`：途中で止める場合だけ区間番号を指定。最後まで進める場合は `-1`
- `reroll_feedback`: optional description of what should be corrected in the current segment / `reroll_feedback`：現在区間で直したい点を記入可能

For a 30-second video split into 13 + 13 + 4 seconds, `reroll_from_segment=1` regenerates everything from 13–26 seconds onward, while `2` regenerates only 26–30 seconds. A different `cache_name` cannot inherit the previous attempts or intermediate state. / 30秒を13＋13＋4秒に分ける構成では、`reroll_from_segment=1` は13〜26秒以降、`2` は26〜30秒だけを再生成します。別の `cache_name` では以前の試行回数や中間状態を引き継げません。

## Troubleshooting / よくあるトラブル

| Symptom / 症状 | What to check / 確認すること |
|---|---|
| A node is red or reported missing / ノードが赤い・不足と表示される | Check that all six folders are installed directly, no duplicated nesting exists, dependencies were installed into ComfyUI's Python, and the startup log has no `ImportError` / 6フォルダーを正しく配置したか、二重フォルダーになっていないか、ComfyUIのPythonへ依存を入れたか、起動ログにImportErrorがないか |
| A model is absent from the list / モデルが一覧に出ない | Check filename, extension, destination, and download completion, then refresh the model list or restart ComfyUI / ファイル名、拡張子、配置先、ダウンロード完了を確認し、モデル一覧の更新またはComfyUI再起動を行う |
| CUDA is unavailable / CUDAが使えない | Run `torch.cuda.is_available()` with ComfyUI's Python and check the NVIDIA CUDA PyTorch build and driver / ComfyUIのPythonで `torch.cuda.is_available()` を確認し、NVIDIA CUDA版PyTorchとドライバーを確認する |
| Triton error / Triton関連で止まる | Confirm that Triton matches PyTorch, CUDA, and the OS; for native Windows, check the `triton-windows` compatibility table / PyTorch、CUDA、OSに対応するTritonか確認する。Windowsネイティブは `triton-windows` の対応表を確認する |
| `ffmpeg` or `ffprobe` is missing / `ffmpeg` / `ffprobe` が見つからない | Confirm that both commands run from `PATH` / 両コマンドがPATHから実行できるか確認する |
| Cannot connect to LM Studio / LM Studioへ接続できない | Confirm that the server is running, the model is loaded, and the identifier and endpoint match; from WSL, use an address that can reach Windows / サーバー起動、モデルのロード、識別子、接続先を確認する。WSLではWindows側へ届くアドレスを使う |
| LM Studio server responds with 404 / LM Studioサーバーは動くが404 | Confirm that the model with the configured identifier is still loaded and was not unloaded by TTL or another setting / 指定した識別子のモデルがロード中か、TTLなどでアンロードされていないか確認する |
| Stops while returning the LM model to CPU / LMモデルのCPU復帰で止まる | Check the `lms` CLI and `lmstudio` package; if automatic switching is unavailable, turn it off and load the LM model on CPU / `lms` CLIと `lmstudio` パッケージを確認する。自動切替を使わない場合はOFFにしてCPUロードする |
| Stops during speech review / 音声検査で止まる | Configure a Transformers-format Whisper folder and confirm that all required files are together / Transformers形式Whisperのフォルダーを設定したか、必要ファイルが同じ場所にあるか確認する |
| I2V or Ref2V stops before generation / I2V・Ref2Vが実行前に止まる | Add the required image and make sure an empty optional image node is not enabled / 必須画像を入れたか、空の追加画像ノードを有効にしていないか確認する |
| Out of memory / メモリー不足 | Return to 0.4 MP and 5 seconds, and move the LM Studio model to CPU; shortening duration may not fix memory needed just to load the models / 0.4MP・5秒へ戻す。LM StudioモデルをCPUへ移す。モデル読込時点の不足は尺短縮だけでは解消しない場合がある |
| Changing the numeric duration has no effect / 秒数欄を変えても長さが変わらない | Check whether `Duration` or an explicit total duration in the prompt body is overriding it / 本文中の `Duration` または「全体○秒」が優先されていないか確認する |
| The same action repeats / 同じ動作が繰り返される | Describe the action for each part of the long video, then inspect the final prompt and execution plan / 長尺の各区間で何をするかを明記し、最終プロンプトと実行計画を確認する |
| BGM or speech differs from the request / BGMや台詞が意図と違う | Inspect `overall_soundscape`, `non_diegetic_music`, and dialogue instructions in the exact final prompt sent to H3 / 実際にH3へ渡された最終プロンプトの `overall_soundscape` と `non_diegetic_music`、台詞指定を確認する |
| Character looks different / キャラクターが似ない | Use a clearer sheet, keep style/identity rules enabled, reduce simultaneous characters, and regenerate the affected segment; H3 reference guidance is not exact face replacement / 明瞭なシート、画風・同一性ルール、人数削減、該当区間再生成を試す。H3参照は顔置換ではない |

## Validated scope and limitations / 検証済みの範囲と制限

These are recorded earlier runtime checks. The 2026-11-05 split release verifies the package without changing these generation paths. / 以下は以前に実施した通常生成の記録です。2026年11月5日の分割配布では、この生成処理を変えずに配布物を検査しています。

- T2V, I2V, and Ref2V generation were run in the development environment / 開発環境ではT2V・I2V・Ref2Vの実生成を実施済み
- For a 30-second T2V run, the ending was resumed from a cache generated through the middle, reaching 30 seconds and 720 frames / T2Vの30秒生成で、中盤まで生成したキャッシュから終盤だけ再開し、30秒・720フレームへの到達を確認
- GPU execution on another PC and on native Windows remains unverified / 別PCとWindowsネイティブでのGPU実行は未確認

The generation model and automated review have limits. They do not guarantee identical characters or clothing in every frame, seamless long-video transitions, exact dialogue pronunciation, or complete removal of unwanted sounds and subtitles. Earlier validation videos included examples with minor subtitles or short grunts. Play the completed video and check both picture and sound. / 生成モデルと自動検査には限界があります。人物や服装の全フレーム一致、長尺の継ぎ目、台詞の正確な発音、不要音や字幕の完全排除を保証するものではありません。過去の確認動画では軽微な字幕や短いうなり声が残った例があります。完成動画を再生し、映像と音声の両方を確認してください。

See [VALIDATION.md](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/VALIDATION.md) for detailed validation conditions and [REPRODUCE.md](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/REPRODUCE.md) for rebuilding the identical package. / 詳しい検証条件は [VALIDATION.md](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/VALIDATION.md)、同じ配布物を再構築する手順は [REPRODUCE.md](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/REPRODUCE.md) にあります。

## Privacy and network access / プライバシーと通信

- No cloud API key is required. / クラウドAPIキーは不要です。
- LM Studio communication goes to a local server. / LM Studioとの通信先はローカルサーバーです。
- Whisper speech review runs locally on the CPU and does not send audio externally. / Whisper音声検査はローカルCPUで実行し、音声を外部へ送信しません。
- The Release ZIP excludes input images, generated outputs, credentials, personal paths, and personal settings. / Release ZIPに入力画像、生成物、認証情報、個人パス、個人用設定は含みません。
- A local WSL-to-Windows connection does not require exposing a port to the public internet. / WSLとWindows間のローカル接続に、外部インターネットへのポート公開は不要です。

## Licenses / ライセンス

This distribution contains components under several licenses. / この配布セットは複数のライセンスを含みます。主な構成は次のとおりです。

| Package / パッケージ | License / ライセンス |
|---|---|
| `ComfyUI-MiniMax-H3-Long-Video` | GPL-3.0-only |
| `ComfyUI-H3-AudioRefine` | MIT |
| `ComfyUI-PlagueKind-Nodes` | MIT + Apache-2.0 for the LightX2V-derived SLA files / LightX2V由来のSLAファイルはApache-2.0 |
| `ComfyUI-Custom-Scripts` | MIT |
| `comfyui-h3-standard-prompt` | Original portions are MIT; see the bundled documents for the combined distribution's conditions / 独自作成部分はMIT。結合構成全体の条件は同梱文書を確認 |

When redistributing or modifying the package, review [LICENSES_AND_NOTICES.md](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/LICENSES_AND_NOTICES.md) and the `LICENSE` file in each folder, and retain copyright notices and license text. / 再配布・改変時は [LICENSES_AND_NOTICES.md](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/LICENSES_AND_NOTICES.md) と各フォルダーの `LICENSE` を確認し、著作権表示とライセンス本文を保持してください。

SLA `kernel.py` and `block_map.py` retain their LightX2V attribution and modification notices. The [Apache-2.0 text](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/custom_nodes/ComfyUI-PlagueKind-Nodes/LICENSE-APACHE-2.0.txt) and [file mapping](https://github.com/FURUYAN1234/comfyui-h3-workflows/blob/main/custom_nodes/ComfyUI-PlagueKind-Nodes/THIRD_PARTY_NOTICES.md) accompany the existing MIT license inside the node pack. / SLAの `kernel.py` と `block_map.py` の由来・改変表示を保持し、ノードパック内に既存MIT本文とApache-2.0全文・対象ファイルの説明を同梱しています。


The model weights are governed by their respective distributors' terms. MiniMax H3 is covered by the [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE), which includes terms related to region, use, commercial use, and redistribution. Before use, read the original agreement and confirm that your region and intended use qualify. / モデル本体には各配布元の条件が適用されます。MiniMax H3には地域、用途、商用利用、再配布などを定めた [MiniMax H3 Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) があります。利用前に、自分の地域と用途が条件を満たすか原文で確認してください。
