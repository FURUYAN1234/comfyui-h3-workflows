# v1.1.4 — 発声タグ・区間境界の形式不備で停止する問題を修正

T2V・I2V・Ref2Vで共通の変換ノードに反映。台詞タグの省略や生成区間境界をまたぐ時間範囲は警告として続行し、元の内容を保持します。時間範囲は既存の区間処理で切り出します。「常に叫ぶ」「歌い続ける」など明示された反復は意図しない重複と区別します。特定の台詞・人物・場所を条件にしません。非LLM四コマの入力・台詞数による尺設定は変更していません。

稼働版で直前に失敗した非性的な30秒T2Vを同じ入力で再実行。発声タグと15秒境界の警告を残したまま1回目の変換で通過し、映像H264・音声AACとも30秒、全720フレームのデコードを確認しました。全体716.529秒（変換・モデル読み込み・2区間の映像4／音声2ステップ・保存込み）、変換77.3秒。音声聴取と映像の内容品質判定は未実施です。

今回の配布ZIPからの新規GPU生成・他PC実機実行は未検証です。配布ノードの3モードの実コード検査、正常・異常入力テスト、ZIP展開検査とタグ再構築を実施します。GitHub Release・note更新・フルバックアップはこのZIP作成作業では未実施です。

---
過去版の記録：

# v1.1.3 — 軽微な変換不備での停止を修正

一部の日本語説明・擬音・参照画像タグの省略・保持分類の省略は警告として、内容を削除せずH3へ渡します。原文全体の丸読み、意図しない台詞重複、存在しない画像番号などは従来の最大3回の修正対象です。特定の場面や人物に依存する例外はありません。

稼働版では直前に失敗した同じ日本語入力・参照画像・設定で再実行し、LLM変換1回目で通過、20秒・864×480・24fps・480フレームのMP4保存まで成功しました。実行時間648.755秒（LM変換・モデル読み込み・映像4ステップ・音声2ステップ・保存を含む）。音声はAAC20秒を確認、聴取による判定は未実施です。1秒ごとの抽出映像では終盤の爆発後に場面が巻き戻らず、上空への移動と消失を確認。前半に似た接近構図が続く箇所はあります。全般的な反復解消は保証しません。

今回の配布ZIPそのものによる新規GPU生成と別PC実行は未検証です。配布ノードの動作検査、全件ハッシュ、クリーンなタグからの再構築は別途検査します。GitHub Release・note・フルバックアップはこのZIP作成作業では未実施です。

# Changelog / 更新履歴

## v1.1.2 — 2026-09-09

- Generate a 16–20 second request as one H3 pass instead of introducing an artificial 15-second seam. Longer requests retain segmented continuation. / 16〜20秒の指定は15秒位置で不要に分割せず、H3の1区間で生成。20秒超は従来どおり継続区間で生成。
- Require timed phases not to straddle a real generation boundary, retain completed sound events as context rather than replaying them, and repair missing Japanese dialogue language tags. / 実際の区間境界をまたぐ時間範囲を変換時に拒否し、完了済み音響イベントの再発を防ぎ、日本語台詞の言語タグ欠落を補正。
- Make source ZIP reconstruction deterministic across Windows and Unix by fixing the ZIP creator metadata; the repository rebuild is byte-identical to the reviewed v1.1.2 asset. / ZIP作成者メタデータを固定し、Windows・Unix間でも再構築ZIPをバイト一致させる。

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
