# ComfyUI MV Workflow bridge / ComfyUI MV連携ノード

## Usage terms / 利用条件

> [!NOTE]
> Source code public / free to use. Personal, business, internal and commissioned use, outputs and introductions require no FURU application, prior contact or fees. The existing MIT LICENSE governs, including paid licensed uses; third-party terms remain separate. / ソースコード公開・利用無料。個人・業務・社内・受託制作、成果物や紹介はFURUへの申請・事前連絡・利用料不要です。有料利用も含め既存MIT LICENSEが適用され、第三者の条件は別です。
>
> [Full guidance / 利用案内全文](../../FURU_TERMS.md) · [Scope / 適用範囲](../../LICENSE_SCOPE.md)

Source code public / free to use; MIT licensed uses require no FURU application. See [usage guidance](../../FURU_TERMS.md) and [scope](../../LICENSE_SCOPE.md). / ソースコード公開・利用無料。MIT許諾内の利用はFURUへの申請不要です。[利用案内](../../FURU_TERMS.md)と[適用範囲](../../LICENSE_SCOPE.md)を参照してください。

This package reads a portable music-video asset bundle, validates its manifest and source hashes, aligns exact lyrics to the final audio when requested, and finishes an H3 video with the source song, subtitles, title, end link, and synchronized fades. / 可搬MV素材バンドルを読み、manifestと元ファイルのハッシュを検査し、必要なら正確な歌詞を最終音源へ時刻合わせし、H3映像を元曲・字幕・タイトル・終了リンク・映像音声フェード付きで仕上げます。

The input accepts a standard ComfyUI `IMAGE`; the legacy path field is optional. Bundle paths may be relative to `ComfyUI/output`, such as `mv-assets/TITLE_TIMESTAMP_ID`. / 入力画像は標準ComfyUI `IMAGE` を受けます。旧パス欄は任意です。バンドルは `mv-assets/曲名_日時_ID` のように `ComfyUI/output` 基準の相対指定ができます。

See the repository root README and `workflows/MV_H3_YuE2-LMStudio_v1.2.1.json` for the supported contract and complete setup. / 対応仕様と導入全体はリポジトリ直下READMEと同梱MVワークフローを参照してください。
