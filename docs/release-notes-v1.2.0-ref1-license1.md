# v1.2.0-ref1-license1 — Apache-2.0 license inclusion / Apache-2.0本文の同梱修正

The distribution now includes the complete Apache-2.0 text and explicit file mapping for the LightX2V-derived `ComfyUI-H3-SLA-Attention/sla/kernel.py` and `sla/block_map.py`, inside `custom_nodes/ComfyUI-PlagueKind-Nodes/`. The existing MIT license, author notices and source modification notices are retained. / `custom_nodes/ComfyUI-PlagueKind-Nodes/` 内にApache-2.0全文と、LightX2V由来の `ComfyUI-H3-SLA-Attention/sla/kernel.py`・`sla/block_map.py` への適用説明を追加しました。既存MIT本文・著作者表示・ソースの改変表示は保持しています。

This is a license-only revision of public **v1.2.0-ref1**, based on commit `80448495ee400c2efc2529506c822765d8c1de41`. All 120 existing custom-node and workflow files are byte-identical to that release, including its reference-image binding fix. Functional version and workflow filenames remain v1.2.0-ref1. The v1.2.1/main feature changes are not included. / 公開 **v1.2.0-ref1** のライセンス限定修正版です。既存ノード・ワークフロー120ファイルは公開版と完全一致し、参照画像の対応付け修正も維持します。機能版・ワークフロー名はv1.2.0-ref1のままで、v1.2.1/mainの機能変更は含みません。

Build and extracted-package checks now reject missing or changed Apache text and missing attribution/scope notices. Archive creation refuses existing destinations; manifest ordering is stable across platforms. / ビルド前・展開後の検査でApache本文や適用・由来表示の欠落を拒否します。既存ZIPの上書きを防ぎ、マニフェストの並び順をOS間で統一しました。

Validation: 13 focused tests, full extracted-package checks, exact file/checksum inventory, official Apache text equality, byte-preserved existing runtime/workflow files and deterministic local rebuild. No new GPU generation or paid API run was performed for this license-only correction. / 検証は13テスト、展開後検査、全ファイルとチェックサム、公式本文一致、既存機能ファイル不変、再構築一致です。このライセンス修正ではGPU生成・有料API実行は追加していません。

Existing tags and previously published ZIPs remain unchanged. Download this release's newly named ZIP and matching SHA-256 file. Nano's separate FourPanel `_authfix1.zip` does not contain the affected SLA sources and is unchanged. / 既存タグ・公開済みZIPは変更していません。このReleaseの新しい名前付きZIPとSHA-256を取得してください。Nanoの別製品FourPanel `_authfix1.zip` には対象SLAが含まれず、変更していません。
