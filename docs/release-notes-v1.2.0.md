# v1.2.0 — YuE2連携MVワークフロー / YuE2-connected MV workflow

This release adds a complete music-video path to the existing MiniMax H3 T2V/I2V/Ref2V distribution. It reads the portable asset bundle created by ComfyUI-YuE2-Japanese v1.6.0, accepts a standard ComfyUI character image, plans direction locally through LM Studio, generates variable-length H3 video, and restores the exact source song during finishing. / 既存のMiniMax H3 T2V・I2V・Ref2V配布へ、MVの通常経路を追加しました。ComfyUI-YuE2-Japanese v1.6.0が出力する可搬MV素材を読み、標準ComfyUI画像を受け、LM Studioでローカル演出設計、H3で可変尺生成し、仕上げ時に正確な元曲へ戻します。

Highlights / 主な追加:

- Fixed test duration or full-song variable duration / 任意の検証秒数または曲末までの可変尺
- Whisper alignment for exact supplied lyrics / 正確な提供歌詞をWhisperで時刻合わせ
- Source-audio lip-sync direction and original-audio finishing / 元曲リップシンク指示と元音源仕上げ
- 2D/3D/live-action style preservation rules / 2D・3D・実写の画風維持ルール
- Top-left fading title, lower-right end link, subtitle burn-in, synchronized audio/video fade / 左上タイトル、右下終了リンク、字幕焼付、映像音声フェード
- ComfyUI preview/download and portable relative paths / ComfyUIプレビュー／ダウンロードと可搬相対パス

Validated / 検証:

- Public workflow: 26 nodes, 35 links, no missing node on load / 公開JSONは26ノード・35リンク、不足なし
- Unit/regression: MV core 8, portability 3, Long Video 186, Standard Prompt 76, root regression 3 / 単体・回帰検査は左記件数に合格
- Normal-path sample: 20.000 s, 864×480, 24fps, H.264/AAC, 4 subtitle cues, source-audio correlation 0.999712 / 通常経路サンプルは左記条件

Install the named ZIP from this Release, not GitHub's automatically generated source archive. Run `python -B verify_package.py` after extraction. / GitHub自動生成Sourceではなく、このReleaseの名前付きZIPを導入し、展開後に `python -B verify_package.py` を実行してください。

Related / 関連:

- YuE2 repository: https://github.com/FURUYAN1234/ComfyUI-YuE2-Japanese
- YuE2 note: https://note.com/happy_duck780/n/n57df44cf7fd2
- MV note: https://note.com/happy_duck780/n/ne8a84cb6db37
