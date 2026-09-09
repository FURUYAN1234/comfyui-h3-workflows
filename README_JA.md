# v1.1.5 — 同梱版の導入手順と版表記を修正

v1.1.5のZIPには必要な5フォルダーをすべて同梱しています。`custom_nodes/`内の5フォルダーをComfyUIの`custom_nodes/`へコピーしてください。同梱済みの外部4ノードを別途取得したり、互換パッチを適用したりする必要はありません。ワークフローと変換ノードの挙動はv1.1.4から変更していません。

## v1.1.4 — 発声タグ・区間境界の形式不備で停止する問題を修正

T2V・I2V・Ref2Vで共通の変換ノードに反映。台詞タグの省略や生成区間境界をまたぐ時間範囲は警告として続行し、元の内容を保持します。時間範囲は既存の区間処理で切り出します。「常に叫ぶ」「歌い続ける」など明示された反復は意図しない重複と区別します。特定の台詞・人物・場所を条件にしません。非LLM四コマの入力・台詞数による尺設定は変更していません。


検証範囲は VALIDATION.md を参照してください。

## v1.1.3 — 軽微な変換不備での停止を修正

一部の日本語説明・擬音・参照画像タグの省略・保持分類の省略は警告として、内容を削除せずH3へ渡します。原文全体の丸読み、意図しない台詞重複、存在しない画像番号などは従来の最大3回の修正対象です。特定の場面や人物に依存する例外はありません。

詳細は VALIDATION.md を参照してください。

# MiniMax H3｜I2V・Ref2V・T2V サンプル集

映像4ステップ Fused＋SLA／音声再精錬2ステップ／可変尺／日本語LM Studio変換・完成英語直接入力。

## 同梱内容

- `workflows/`：`T2V_4step_20260909182752_v1.1.5.json`、`I2V_4step_20260909182752_v1.1.5.json`、`Ref2V_4step_20260909182752_v1.1.5.json`の3種類。サンプル日本語だけを入力済み。各ワークフロー内にも詳しい説明があります。
- `custom_nodes/`：必要な5パッケージのソースコードと各ライセンス。
- `models.json`：H3用4モデルの正確な名前・配置先・取得URL・サイズ・SHA-256。
- `verify_package.py`：ZIP展開後の内容とワークフロー条件を検査するスクリプト。
- `SHA256SUMS.json`：配布ファイルのハッシュ一覧。
- `LICENSES_AND_NOTICES.md`・`licenses/`：再配布とモデル利用の条件。

モデル本体、入力画像、生成動画、APIキー、個人用辞書、個人PCの設定は同梱していません。

## 動作環境

この設定はNVIDIA CUDA GPUを使う構成です。SLAにTritonが必要です。「高性能GPU」でもAMD・Intel・AppleのGPUでこの構成がそのまま動くとは確認していません。

参考の検証環境：Windows上のWSL2 Ubuntu、NVIDIA RTX 5080（16GB VRAM）、ComfyUI 0.34.0、フロントエンド1.51.9。16GBは全設定での動作保証や最低要件ではありません。個人のアカウント名、PC名、機器の識別番号、個別の保存パス、ネットワークアドレスは記載していません。

必要なRAMとVRAMは、モデルの量子化方式、画素数、尺、同時にロードするモデルによって変わります。H3用モデル約40.44GBに加えて、LMモデル・中間動画・潜在保存用の十分な空き容量を用意してください。長尺の中間ファイルは大きくなります。

H3・ResolutionSelector・V3ノードAPI・選択中の量子化方式を扱えるComfyUIが必要です。モデル名だけを合わせても、古い本体では動きません。ComfyUI本体の個別改造はこの3本のためには必要ありません。

## 1. ComfyUIと依存ライブラリ

[公式インストール案内](https://docs.comfy.org/installation/system_requirements)に従い、CUDA版PyTorchを使うComfyUIを準備します。既に稼働中なら、更新前に既存環境をバックアップしてください。

ZIPを展開し、`custom_nodes/`内の5フォルダーをComfyUIの`custom_nodes/`へコピーします。同名ノードの二重導入は避けます。既存の同名フォルダーを差し替える場合は元を退避してください。このZIPは専用の配布スナップショットで、Managerで同名ノードを更新すると内容が置き換わる場合があります。

依存ライブラリは**ComfyUI自身のPython**へ入れます。ZIPのrequirements.txtへのパスは展開先に合わせてください。

Windows Portable版で、Portableのルートから実行する例：
```powershell
.\python_embeded\python.exe -m pip install -r "展開したZIPのフォルダー\requirements.txt"
```

Linux／WSLのvenv版で、ComfyUIフォルダーから実行する例：
```bash
.venv/bin/python -m pip install -r "/path/to/ComfyUI_H3_Workflows/requirements.txt"
```

TritonはOSとPyTorchに合う版を使います。Windowsでは[triton-windows公式リポジトリ](https://github.com/woct0rdho/triton-windows)の互換表に従って同じPythonへ導入します（Linux用tritonとは別パッケージ）。埋め込みPythonで追加のヘッダーやライブラリが必要な場合も同ページの手順に従います。Linux／WSLではPyTorchに対応するtritonを使用します。

ComfyUIのPythonで次を実行し、CUDAとTritonが認識されることを確認します：
```bash
python -c "import torch,triton; print(torch.__version__, torch.cuda.is_available(), triton.__version__)"
```
ここで`python`は上の`python_embeded/python.exe`または`.venv/bin/python`へ置き換えます。FalseならCUDA環境を先に直します。ComfyUIを再起動し、H3StandardPrompt・MiniMaxH3LongReferenceSampler・H3SLAAttention・H3AudioRefineSampler・ShowTextが不足ノードにならないことを確認します。

## 2. モデルを取得して配置



## 4モデルすべてが必要です

- [minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors](https://huggingface.co/MATLOWAI/minimax-h3-fused-turbo-int8-convrot/resolve/main/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors)  
  保存先：`ComfyUI/models/diffusion_models/`（約20.98 GB）

- [qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors)  
  保存先：`ComfyUI/models/text_encoders/`（約15.69 GB）

- [minimax_h3_video_vae_int8_convrot.safetensors](https://huggingface.co/Kijai/MiniMax-H3-experimental/resolve/main/minimax_h3_video_vae_int8_convrot.safetensors)  
  保存先：`ComfyUI/models/vae/`（約3.17 GB）

- [minimax_h3_audio_vae_fp32.safetensors](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/vae/minimax_h3_audio_vae_fp32.safetensors)  
  保存先：`ComfyUI/models/vae/`（約0.61 GB）

ローダーの不足モデル案内にも同じ取得先を登録しています。画面にDownloadが出る場合は選択できます。ブラウザーで取得したファイルは下の階層図の位置へ手動で移動し、ComfyUIのモデル一覧を更新／再起動して選択します。ZIPにモデル本体は入りません。H3用4ファイルの合計は約40.44GBです。LM Studio用モデルは別途必要です。

## ComfyUIフォルダーの中へ保存
```text
ComfyUI/
├─ models/
│  ├─ diffusion_models/
│  │  └─ minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors
│  ├─ text_encoders/
│  │  └─ qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
│  └─ vae/
│     ├─ minimax_h3_video_vae_int8_convrot.safetensors
│     └─ minimax_h3_audio_vae_fp32.safetensors
├─ custom_nodes/
│  ├─ comfyui-h3-standard-prompt/
│  ├─ ComfyUI-MiniMax-H3-Long-Video/
│  ├─ ComfyUI-H3-AudioRefine/
│  ├─ ComfyUI-PlagueKind-Nodes/
│  └─ ComfyUI-Custom-Scripts/
├─ input/   ← アップロードした自分の画像
└─ output/  ← 完成動画と中間データ
```

モデルをZIPのworkflowsフォルダーやcustom_nodesへ置かないでください。Windows Portable版なら通常は `ComfyUI_windows_portable/ComfyUI/` が上の起点です。カスタムノードを二重の同名フォルダーにしないでください。

LM Studio用GGUFモデルはLM Studio自身のモデル保存場所へ取得します。上のH3用4ファイルとは別管理です。



ダウンロード完了後、一覧の再読み込みまたはComfyUI再起動を行います。ファイル名が変わった場合は各ローダーで選び直します。ブラウザーからのリンク取得は通常ダウンロードフォルダーに保存されるため、上の所定位置へ移動が必要です。不足モデルダイアログでの自動配置の対応状況はComfyUIの版によって異なります。

## 3. LM Studioを準備（日本語変換を使う場合）



## LM Studioの役割
①の日本語＋②の基本ルール＋接続した画像
→ ローカルのLM Studioが内容を解釈
→ H3向けの英語文章（日本語台詞は保持）
→ H3が映像と音声を生成

LM Studioは動画を作るモデルではなく、動画用文章を作る担当です。下の③だけを使う場合、この変換工程を省略します。

## 導入
1. [LM Studio公式サイト](https://lmstudio.ai/)からインストールします。この配布ノードは `/v1/chat/completions` に対応するLM Studioを使用します。
2. アプリ内のモデル検索で **Qwen3.5-9B** のGGUF版（例：Q4_K_M）を取得します。I2V・Ref2Vの日本語変換は画像も送るため、画像認識対応モデルと必要なVisionファイルを揃えます。
3. Developer画面でモデルをロードし、サーバーをStart、ポート1234にします。詳しくは[公式サーバー手順](https://lmstudio.ai/docs/developer/core/server)。
4. この枠の「LM Studioのモデル名」をDeveloper画面に表示されるモデル識別子に合わせます。`qwen-prompt-ja`は任意の別名です。CLIなら `lms load qwen/qwen3.5-9b --identifier qwen-prompt-ja --context-length 16384 --gpu off` で設定できます。取得したモデルのキーが違う場合は置き換えます。
5. 同じOS上なら接続先は `http://127.0.0.1:1234/v1`。ComfyUIがWSL・LM StudioがWindowsなら、READMEのWSL接続手順を使います。

H3とGPUを共有するとメモリー不足になる場合があります。CPUロード（GPU offloadを0）ならVRAM競合を抑えられますが、変換は遅くなります。

接続・モデル名を変える場所はプロンプト枠の小さな設定欄です。出力上限4096は「文字数」ではなくトークン数です。通信の無応答タイムアウトは初期値360秒です。ストリーム受信中は最大18分まで継続し、内容の不合格は最大3回の変換で打ち切ります。途中で切れた文章や未変換の制作指示を動画へ渡しません。

配布版は個人PC用の自動起動スクリプトを含みません。LM Studioは事前に起動してください。H3のテキストエンコーダーsafetensorsをLM Studioへ読み込むわけではありません。



LM Studioの自動アンロードやTTLでモデルが解除されると、サーバーが起動中でもモデル指定が404になる場合があります。Developer画面で同じ識別子のモデルを再ロードし、長い作業では保持時間も確認してください。

### WSLのComfyUIからWindowsのLM Studioへ接続する場合

別のOS間のため、`127.0.0.1`が同じ場所を指さない構成があります。LM Studio側でWSLから到達できるアドレスにサーバーを起動し、ワークフローの接続先を`http://到達できるホストアドレス:1234/v1`に設定します。[公式ローカル接続案内](https://lmstudio.ai/docs/developer/core/server/serve-on-network)も参照してください。

この配布ノードはWSLの標準ゲートウェイなども接続候補にしますが、ファイアウォールやWSLネットワーク設定によって自動検出できない場合があります。接続確認はComfyUIを動かすOS側から行います。例：`curl http://ホストアドレス:1234/api/v1/models`。外部インターネットへポートを公開する必要はありません。

配布版の接続は認証不要のローカルサーバーを前提とし、固定の個人用キーはありません。LM StudioでAPI認証を必須にしている場合は、そのままでは接続できません。この版にはトークン入力欄がないため、認証必須の環境では対応クライアントへの変更が別途必要です。既存の認証を勝手に変更するスクリプトは含みません。

## 4. ワークフローを開く

JSONをComfyUI画面へドラッグ＆ドロップするか、「開く／インポート」で読み込みます。

| 種類 | 入れる画像 | 動作 |
|---|---|---|
| T2V | なし | 文章から映像・音声を生成 |
| I2V | 開始画像1枚 | その画像を開始フレームとして動かす |
| Ref2V | 参照画像1〜5枚 | 人物・服装などを参照して別の場面を生成 |

I2V・Ref2Vは画像ファイルを含まないため、画像を入れてから実行します。Ref2Vの追加画像2〜5は初期状態で無効です。必要なノードを選びCtrl+Bで有効化し、2から順にアップロードします。不要な画像は再度無効化します。



## 大きなプロンプト枠の3つの欄
**① 一番上：作りたい動画の指示**
登場人物、動作、背景、カメラ、台詞、音を書きます。ここに文章があるとLM StudioがH3用英語文章へ変換します。英語をここに入れた場合も再構成されます。

**② 中央：LM Studioの基本ルール**
台詞の言語、字幕、BGMなどについて変換時に守らせるルールです。動画の本文を貼る場所ではありません。通常はそのまま使います。①の指示を変換するときだけ使用します。

**③ 一番下：完成した英語H3プロンプト**
他で作った完成文を直接使うときは、①を完全に空欄にして③へ貼ります。LM Studioを呼ばず、②も適用しません。BGMや台詞の指定も③の本文に含めてください。

**両方に文章がある場合は①が優先です。** ③は下書きとして統合されません。①と③が両方空なら実行できません。

「H3最終プロンプト」は結果の確認欄です。そこへ書いても次回入力にはなりません。上流H3文章ソケットは外部ノード接続用で、通常は未接続です。

## 秒数を変える場所
左側の「変更場所：秒数」の数値を変更します。初期値は15秒です。これは**入力文に尺指定がない場合**の長さです。

入力文で指定する場合：日本語は `全体15秒`、英語は行頭に `Duration: 15 seconds` と書きます。明示した尺が数値欄より優先されます。数値欄で調整したい場合は、本文中の尺指定を削除してください。複数の明示尺があると最長が採用されるため、一つに統一します。

## 可変尺はどう動くか

このワークフローは15秒固定ではありません。30秒・60秒など、15秒を超える長さも指定できる可変尺です。配布版の実生成では20秒（24fps・480フレーム、音声20秒）を確認しています。30秒・60秒は設定可能な例であり、この配布検証で実生成した長さではありません。
秒数×24fpsから出力フレーム数を計算します。内部フレーム数はH3の形式に合わせ、最終出力を指定尺に整えます。20秒までは分割せず一括生成します。20秒を超える動画は約15秒ごとの区間生成となり、前区間末尾を引き継ぎます。20秒一括生成は864×480で確認済みです。解像度や環境によって必要メモリーが増えるため、他PCでは短尺から確認してください。20秒超の継ぎ目や反復動作の一般的な解消は未確認です。台詞の数で勝手に尺を伸ばす設定ではありません。

長尺では文章にも時間の流れが必要です。①なら「全体30秒。前半は歩き、後半は立ち止まって見回す」のように書きます。③では `[Shot 2] At 00:15.000, ...` などの時刻を使います。連続撮影ならその旨も書きます。

`[0:00-0:05] 動作` のような任意の時刻表記だけでは、この版は総尺を認識しない場合があります。上の明示尺か数値欄を使い、実行計画の `duration_seconds` と `segment_count` で実際の設定を確認してください。

長くするほど時間・メモリー・中間保存容量が増えます。長尺の継ぎ目や動作・音の連続性は生成結果で確認します。

## 「変更場所：画面比率・画素数」で変更
`aspect_ratio` は画面の形です。
- 16:9：横長の動画
- 9:16：縦長の動画
- 1:1：正方形

`megapixels` は画素数です。初期値0.4MP。上げると画像が大きくなり、一般に細部を表現しやすくなる一方、生成時間とメモリー使用量が増えます。縦横寸法は比率と画素数から自動計算されます。

`multiple` は32のまま使います。H3に合う寸法へ丸めるため、選択比率と実際の縦横比には小さな差が出ることがあります。

生成ノードのwidth・heightはここから線で入力されます。生成ノード側の古い数値を書き換えても、接続先からの値が優先です。

I2Vは開始画像と同じ比率が使いやすい設定です。異なる比率では開始画像がリサイズ・切り抜きされ、画面端が見えなくなる場合があります。Ref2Vは参照画像と完成動画の構図を変える用途にも使えます。

最初は0.4MP・5〜15秒で確認し、必要に応じて大きくしてください。

## BGMを鳴らしたいとき
①へ動画内容と一緒に「明るいピアノのBGMを入れる。台詞中は音楽を控えめにする」と書きます。音楽の楽器・テンポ・雰囲気も指定できます。

③の直接入力では、完成文の `non_diegetic_music:` を、例えば `Gentle piano music with a warm, relaxed rhythm, kept below the dialogue.` にします。BGMなしなら `non_diegetic_music: N/A` と書きます。

**BGMはH3が動画とともに生成する音楽です。** 既存の曲ファイルを再生・後付けする機能ではありません。音量指示も生成への指示であり、独立したミキサーで必ずその音量に制御するものではありません。

## 台詞・効果音
①の例：「人物が一度だけ『こんにちは。』と言う。鳥の声と足音。BGMなし。」
③では話者を `(S1)` などで示し、発話を `<d>[Japanese]こんにちは。</d>` と書きます。`overall_soundscape:` は環境音・物音の欄です。

台詞なしなら「台詞なし・ナレーションなし」、完全な無音なら「BGM・環境音・効果音・人声すべてなし」と指定します。BGMなしでも環境音は残せます。

「音声再精錬2ステップ」は生成済み映像を保って音声を整える追加処理です。BGMのON/OFFスイッチではありません。固有の読みが必要な言葉は、かな等で読みを明示し、実際の音声を聴いて確認します。個人の読み辞書は付属しません。

## 必須カスタムノード
ZIPのcustom_nodes内の5フォルダーを、ComfyUI/custom_nodes/へコピーします。
- comfyui-h3-standard-prompt：入力切替・LM変換・尺計算
- ComfyUI-MiniMax-H3-Long-Video：可変尺生成・区間保存・完成動画保存
- ComfyUI-H3-AudioRefine：音声再精錬
- ComfyUI-PlagueKind-Nodes：SLA Attention
- ComfyUI-Custom-Scripts：最終文章・実行計画の表示

ComfyUI本体のH3・ResolutionSelector対応版、CUDA版PyTorch、**Triton**が必要です。Linux/WSLはtriton、Windowsネイティブは対応するtriton-windowsをComfyUIのPython環境へ入れます。具体的なコマンドと互換性確認はREADME参照。

## 保存場所を変える
右端の保存ノードの `filename_prefix` はComfyUI/output/からの相対パスです。初期設定の `video/MiniMax_H3/…` に日時付き動画が保存されます。

生成ノードの `cache_name`（途中保存フォルダー名） は中間データの場所です。初期設定では `output/h3_long_video/種類/日時/` に区間動画、潜在データ、文章、manifestを保存します。長尺ほど容量を使います。`resume`・`reroll_from_segment` は継続処理用なので通常は変更不要です。

## 困ったとき
- 赤い不足ノード：5フォルダーの位置・依存ライブラリ・起動ログを確認。
- モデル不足：完全なファイル名と置き場所を確認。4モデルの役割を取り違えない。
- LM接続エラー：LM Studioの起動、モデル識別子、接続先を確認。①空欄＋③入力ならLM不要。
- I2V・Ref2Vが実行前に止まる：必須画像をアップロード。空の追加画像ノードは無効にする。
- メモリー不足：画素数や尺を下げ、LM StudioをCPUへ。単に4ステップにしてもモデルのメモリーが不要になるわけではありません。
- 同じ動作を繰り返す：最終文章・実行計画を確認し、画像やシードの影響も比較。この版は生成モデルの反復動作や人物一致を保証する修正ではありません。

APIキーはこの3ワークフローには不要です。クラウド画像変換・外部API検査は接続していません。



## 検証範囲と生成品質

`VALIDATION.md`に、このZIPに対して行った確認内容と未確認の環境を記載しています。ワークフローが実行できることと、全ての入力で映像・音声が希望どおりになることは別です。特定の参照画像による反復動作、人物の変化、台詞や音の揺れを完全に防ぐものではありません。完成動画を見て、音を聴いて確認してください。

## 再配布

ZIPを公開する前に`LICENSES_AND_NOTICES.md`を確認してください。必要な著作権・ライセンス表示は削除しないでください。画像や完成動画を追加する場合は、その素材の権利を別に確認してください。


## 更新履歴

各版の変更内容はCHANGELOG.md、検証範囲と残る制限はVALIDATION.md、タグからの再構築方法はREPRODUCE.mdを参照してください。
