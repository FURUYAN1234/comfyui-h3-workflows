# Changelog / 更新履歴

## v1.1.1 — 2026-09-09

- Preserve byte-identical portable-package source files in Git so the tagged source rebuild passes its own manifest check. The v1.1.0 release asset remains unchanged. / Gitの改行変換を避け、タグ付きソースから再構築してもマニフェスト検査を通るようにした修正版。v1.1.0の公開ZIPアセットは変更しません。

## v1.1.0 — 2026-09-09

- Ship the reviewed portable distribution: T2V, I2V and Ref2V workflows plus all five required custom-node packages and their license texts. / T2V・I2V・Ref2Vの3ワークフロー、必要な5カスタムノード本体、各ライセンスを含む可搬版を配布。
- Add structured LM Studio conversion with bounded retries that rejects production-direction dialogue, duplicate speech, untranslated production prose, and incomplete time development. It never forwards the original brief as an H3 prompt. / JSON Schemaを使うLM Studio変換と上限付き再試行を追加。制作指示の台詞化、台詞重複、未変換の制作文、時間展開不足を止め、原文をH3へそのまま渡さない。
- Preserve timestamped phase ranges, single-take continuity, and specified ending states when a long video is segmented. / 長尺の区間化で時刻範囲、単一ショットの連続性、指定された終幕状態を保持。
- Revalidate the supplied ZIP, its three graphs, model metadata, private-file exclusion, Python syntax, license presence, and full file manifest. / 提供ZIP、3ワークフロー、モデル情報、私有ファイル除外、Python構文、ライセンス、全ファイルマニフェストを再検査。

## v1.0.0 — 2026-09-08

- Initial standalone publication of three H3 workflows and the original prompt helper. / H3ワークフロー3本と自作入力補助ノードを独立した配布物として整理。
- Separate model links and four pinned external node dependencies; retain compatibility patches and license notices. / モデルはリンク案内、外部4ノードは固定版＋互換パッチとして導入し、出典と条件を保持。
- Add bilingual installation, troubleshooting, model reference and validation documentation. / 日英の導入・トラブル対処・モデル・検証説明を追加。
- Add offline dependency checks and reproducible ZIP packaging. Generation behavior and graph wiring are unchanged. / オフライン依存検査とZIP作成を追加。生成処理と配線は変更なし。

## Version policy / バージョン管理

`VERSION` is the source version. Tags use `vMAJOR.MINOR.PATCH`; ZIP names include the same version. Record each change here and in matching release notes. Do not move published tags. Patch revisions cover compatible fixes/docs; minor revisions add compatible features; major revisions mark incompatible setup or interface changes. / `VERSION`、Gitタグ、ZIP名を一致させ、公開タグは付け替えません。互換修正や文書更新はPATCH、互換機能追加はMINOR、非互換の導入・仕様変更はMAJORを上げます。
