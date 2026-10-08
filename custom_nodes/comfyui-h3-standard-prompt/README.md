# H3 standard three modes v1.1.7 / H3標準3方式 v1.1.7

## Usage terms / 利用条件

> [!NOTE]
> Source code public / free to use. Personal, business, internal and commissioned use, outputs and introductions require no FURU application, prior contact or fees. The existing MIT LICENSE governs, including paid licensed uses; third-party terms remain separate. / ソースコード公開・利用無料。個人・業務・社内・受託制作、成果物や紹介はFURUへの申請・事前連絡・利用料不要です。有料利用も含め既存MIT LICENSEが適用され、第三者の条件は別です。
>
> [Full guidance / 利用案内全文](../../FURU_TERMS.md) · [Scope / 適用範囲](../../LICENSE_SCOPE.md)

Source code public / free to use; MIT licensed uses require no FURU application. See [usage guidance](../../FURU_TERMS.md) and [scope](../../LICENSE_SCOPE.md). / ソースコード公開・利用無料。MIT許諾内の利用はFURUへの申請不要です。[利用案内](../../FURU_TERMS.md)と[適用範囲](../../LICENSE_SCOPE.md)を参照してください。


Preserves Japanese input, direct completed H3 text, variable duration, Fused4/SLA and two audio steps. Start LM Studio manually in advance. The LM client is embedded; no personal Qwen node or startup script is needed. / 日本語入力と完成H3文章の直接入力、可変尺、Fused4/SLA・音声2stepを継承。LM Studioは事前に手動起動。LMクライアントを内蔵し、別の個人用Qwenノードや起動スクリプトは不要です。

Automatic GPU switching preserves the local LM Studio model settings, uses GPU during conversion and returns to CPU before video generation. See the root README_JA.md for CLI/Python dependencies. / GPU自動切替はローカルLM Studioの読込モデル設定を保持し、変換時GPU・動画前CPUへ戻します。必要なCLI/Python依存は配布ルートREADME_JA.mdを参照。

Long Video keeps up to five cumulative attempts per segment under the same cache and generation settings and does not reset counts after a predecessor changes. Segment resume and stop_after_segment allow partial review. / Long Videoは同一キャッシュと生成条件のもとで区間ごとの累計最大5回を保持し、前区間変更後も回数をリセットしません。区間指定の再開とstop_after_segmentによる途中確認を使用できます。

Configure a local Whisper folder to enable Japanese audio review. This does not guarantee all aspects of voice quality. Setup and limits are in the root README_JA.md. / Whisperのローカルフォルダーを設定して日本語音声検査を有効にします。声の全面的な品質保証ではありません。導入と制約は配布ルートREADME_JA.mdに記載。
