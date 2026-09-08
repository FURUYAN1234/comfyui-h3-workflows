# Changelog / 更新履歴

## v1.0.0 — 2026-09-08

- Initial standalone publication of three H3 workflows and the original prompt helper. / H3ワークフロー3本と自作入力補助ノードを独立した配布物として整理。
- Separate model links and four pinned external node dependencies; retain compatibility patches and license notices. / モデルはリンク案内、外部4ノードは固定版＋互換パッチとして導入し、出典と条件を保持。
- Add bilingual installation, troubleshooting, model reference and validation documentation. / 日英の導入・トラブル対処・モデル・検証説明を追加。
- Add offline dependency checks and reproducible ZIP packaging. Generation behavior and graph wiring are unchanged. / オフライン依存検査とZIP作成を追加。生成処理と配線は変更なし。

## Version policy / バージョン管理

`VERSION` is the source version. Tags use `vMAJOR.MINOR.PATCH`; ZIP names include the same version. Record each change here and in matching release notes. Do not move published tags. Patch revisions cover compatible fixes/docs; minor revisions add compatible features; major revisions mark incompatible setup or interface changes. / `VERSION`、Gitタグ、ZIP名を一致させ、公開タグは付け替えません。互換修正や文書更新はPATCH、互換機能追加はMINOR、非互換の導入・仕様変更はMAJORを上げます。
