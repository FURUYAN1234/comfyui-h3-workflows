# H3 T2V・I2V・Ref2V v1.1.8 導入と操作

## 配布対象

このシリーズのv1.1.5からの更新です。T2V（画像なし）、I2V（開始フレーム1枚）、Ref2V（参照1〜5枚）の3本を同梱。ファイル名のプロジェクト名・方式・14桁日時・版番号で識別できます。

`custom_nodes/`内の5フォルダーをComfyUIの`custom_nodes/`へコピーします。
- comfyui-h3-standard-prompt：日本語変換、入力切替、尺計画、GPU/CPU切替、ローカル画像検査用クライアント。
- ComfyUI-MiniMax-H3-Long-Video：可変尺、区間保存と再開、候補選択、音声・映像検査、MP4保存。
- ComfyUI-H3-AudioRefine：映像を固定して音声を2ステップ再精錬。
- ComfyUI-PlagueKind-Nodes：SLA Attention。
- ComfyUI-Custom-Scripts：最終プロンプトと実行計画の表示。

同名ノードを別名で二重配置しないでください。既存フォルダーはComfyUIの外へ退避します。このZIPに四コマ用ワークフロー、NanoBananaのクラウドAPI設定、個人用Qwenノードは含みません。

## v1.1.8（2026-09-11）の変更

H3StandardPromptの日本語変換に進捗表示を追加しました。実行受付、処理中の経過秒、完了、エラーを画面上部へ表示します。経過秒は接続・GPU切替・文章変換・CPU復帰を含み、完了率や残り時間ではありません。直接入力の文章変換表示は出しません。GPU→CPU切替、モデル、生成ステップ、尺、候補検査・再開はv1.1.7から維持します。

更新後はComfyUIを再起動し、未保存ワークフローを保存したうえでブラウザーを再読み込みしてください。個人用qwen_rapid_jpを追加する必要はありません。

## 1. 環境・配置

NVIDIA CUDA版PyTorchとMiniMax H3、V3ノードAPI、ResolutionSelectorに対応したComfyUIが必要です。開発時の実生成はWSL2 Ubuntu・RTX5080 16GB・ComfyUI0.34.0・フロントエンド1.51.9で行いました。WindowsネイティブでのGPU動作は未検証です。

ComfyUI本体の準備は https://docs.comfy.org/installation/system_requirements を参照してください。既存のPyTorchやドライバーをこのZIPが変更する処理はありません。

1. ZIP展開直後に `python verify_package.py` を実行します（モデルもGPUも不要）。
2. 5パッケージを配置し、ComfyUIのPythonで `python -m pip install -r requirements.txt` を実行します。Portable版は通常 `python_embeded/python.exe`、WSL/LinuxはComfyUIのvenv内Pythonを使用します。
3. SLA用に使用中PyTorchと互換のTritonを用意します。Windowsは https://github.com/woct0rdho/triton-windows 、Linux/WSLはPyTorch対応のTritonを確認してください。`python -c "import torch,triton; print(torch.cuda.is_available(),triton.__version__)"` で確認できます。
4. FFmpeg/ffprobeをPATHへ用意します。ワークフローJSONは `user/default/workflows/02_動画/13_動画_MiniMax-H3/配布版_T2V-I2V-Ref2V_v1.1.8/` へ置くか、画面から読み込みます。現行タブの未保存内容は先に保存・退避してください。

## 2. H3用4モデル

`models.json`のname・directory・urlに従って取得します。モデル本体はZIPにありません。ローダーのproperties.modelsにも同じ情報を設定してあります。実際の不足モデル画面からの取得・配置はComfyUI版により異なります。

- `models/diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors`
- `models/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`
- `models/vae/minimax_h3_video_vae_int8_convrot.safetensors`
- `models/vae/minimax_h3_audio_vae_fp32.safetensors`

4モデル合計は約40.44GB。LM Studioモデル、Whisper、区間キャッシュの空き領域も必要です。モデルの利用条件は取得元と同梱NOTICEを参照してください。

## 3. LM StudioとGPU切替

LM StudioのDeveloper画面で画像対応のローカルモデルをロードし、サーバーを手動起動します。検証モデルはQwen3.5-9B、APIのモデルIDは `qwen-prompt-ja`。別のIDを使う場合はワークフローのmodel欄を合わせます。画像認識用ファイルも揃えます。APIは `/api/v1/chat` と `/v1/chat/completions` 対応版が必要です。

通常の接続先は `http://127.0.0.1:1234/v1`。WSL側からWindows側LMへはホスト経路も照合します。配布版は個人PC専用の自動起動スクリプトを含まないため、毎回先にLM Studioを起動してください。

「LLM変換時にGPUを使用し、動画生成前にCPUへ戻す」はONです。LM Studio同梱の `lms` CLIとPythonの `lmstudio` が必要です。既にロードした同じモデルの設定を保持してGPU→CPUへ切り替えます。CPU復帰できない場合は動画開始を止めます。CLIの場所を解決できない場合はlmsをPATHに登録するか、この設定をOFFにしてLMをCPUでロードしてください。遠隔PC上のLMにはこの自動切替を使いません。

日本語入力欄が空なら下欄の完成H3プロンプトを直接使用します。この場合、日本語変換のAPIは呼びません。ただし「区間AI検査」がONなので映像検査ではLM Studioを使用します。LMなしで試す場合のみ区間AI検査をOFFにできますが、今回の映像検査と同等ではありません。

外部クラウドAPIキーの入力はこの3方式には不要です。`Bearer lm-studio` はローカルAPIの固定識別子で、個人の秘密鍵ではありません。認証を必須にしたLM Studioサーバーのトークン入力には、この版は対応していません。

## 4. ローカル日本語音声検査（新規導入必須）

Whisper large-v3-turboのTransformers形式一式を取得します： https://huggingface.co/openai/whisper-large-v3-turbo 。GGUFではありません。モデルファイル、config.json、preprocessor_config.json、トークナイザー関連ファイルを同じフォルダーへ揃えます。検証と生成の処理は自動ダウンロードや外部への音声送信を行いません。

ComfyUIのPythonで次を実行します（パスを自分の環境に置換）：

```text
python configure_audio_audit.py --comfyui "ComfyUI本体のフォルダー" --whisper-model "Whisperモデルのフォルダー"
```

これは配置済みLong Videoの `minimax_h3_long_video/local_audio_audit.json` にこのPCのパスを書き込みます。設定済みファイルは初回変更時に退避します。配布元ZIPを書き換える必要はありません。`H3_LOCAL_WHISPER_MODEL` 環境変数での指定も可能です。検査を有効にしたままモデル未設定で実行すると不足エラーになるので、この手順を完了してください。

音声認識はCPUで通常2回、食い違う場合は最大3回。検出対象は指定外の反復発声や台詞語尾の崩れ等です。短い自然な息、一度の長母音は許容します。短いうなりを認識しない場合があり、認識一致だけで自然な音声を保証しません。

## 5. 入力・読み確認・尺・BGM

上欄は日本語指示、中央は基本ルール、下欄は完成H3プロンプトです。上欄がある場合はLM変換を優先します。変換後の表示で台詞と読みを確認します。この3方式にクラウドの読み確認ダイアログや個人発音辞書はありません。

I2Vは開始画像をアップロード。Ref2Vは1枚目を必須とし、必要な2〜5枚目だけCtrl+Bで有効化。参照画像は開始構図固定ではありません。T2Vには画像接続がありません。

映像はres_multistep・simple4ステップ、SLA0.9・音声保護ON。音声再精錬は2ステップ・denoise0.5で、元音声と補正音声も比較します。台詞数で尺を変更しません。指定なしは15秒、時間指定があれば優先します。39フレームの継続条件では30秒を13+13+4秒に分割し、24fps・720フレームに結合します。15秒は内部上限とガイド分の都合で複数区間になる場合があります。

BGMはユーザーの指定に従い、指定がなければ追加しません。配布サンプルは台詞なし・BGMなしです。基本ルールと最終プロンプトは同じ方針です。

## 6. 区間ごとの最大5回と再開

各区間は初回を含む累計最大5回。合格すれば次へ進み、上限で最良の互換候補を使います。前区間を変えても消費済み回数は戻りません。古い前区間の続きから生成した候補を新しい前区間へ無条件につなぎません。回復不能な中間データ破損は別途エラーです。

再開時は同じcache_name、モデル・入力・シード・指定尺を使い、noise_seedをfixedにしてresumeをONにします。reroll_from_segmentは0始まりで、0が最初、1が中盤、2が終盤です。30秒なら1で13〜26秒以降、2で26〜30秒だけを対象にします。中盤だけ見たい場合はstop_after_segment=1。全体を完成させるときは-1へ戻します。reroll_feedbackに現在区間の修正点を記載できます。

cache_nameの日時部分は最初の実行後に表示される実フォルダー名へ固定してください。別キャッシュを作ると別の生成になり、過去の回数は引き継げません。JSON名だけで再開できるわけではありません。

## 7. 保存先と限界

完成動画： `ComfyUI/output/video/MiniMax_H3/H3_T2V-I2V-Ref2V/<方式>/`
中間保存： `ComfyUI/output/h3_long_video/H3_T2V-I2V-Ref2V/<方式>/`

検証動画ではI2Vに軽微な字幕、T2Vの最後の台詞前に短いうなりが残り、許容判断のうえ採用しました。区間ごとの選択と試聴を前提とします。全文の完全発音一致、全フレームの衣装維持、全PCでの成功を保証する版ではありません。
