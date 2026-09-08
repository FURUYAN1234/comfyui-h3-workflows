# H3 Standard Prompt / H3入力補助

Original helper by FURUYAN1234, version v1.0.0, MIT (see the repository LICENSE). / FURUYAN1234の独自ノード、v1.0.0、MIT。リポジトリのLICENSEを参照してください。

Routes nonempty top-field instructions through LM Studio; otherwise uses the completed bottom-field H3 prompt directly. Contains conversion rules, input schema and the LM client. LM Studio must be started manually. / 上欄の指示をLM Studioへ渡す経路と、上欄を空にして下欄の完成文章を直接使う経路を持ちます。変換ルール・入力定義・通信処理を含み、LM Studioは手動で起動します。

The patched external ComfyUI-MiniMax-H3-Long-Video timeline module is required, including for duration planning. This folder alone is not a complete video-generation environment. / 尺計画には互換パッチ適用済みの外部Long-Videoモジュールが必要です。このフォルダーだけでは動画生成環境になりません。

Read the distribution root README.md and docs/DEPENDENCIES.md before installation. No private server launcher, port forwarder or personal credentials are included. / 導入前に配布ルートのREADMEと依存手順を確認してください。私有サーバー起動処理、ポート中継、個人資格情報は含みません。
