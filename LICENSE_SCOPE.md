# License scope and existing permissions / ライセンス適用範囲と既存の許諾

Documentation revision / 文書改定: 2026-10-08. Software baseline / ソフトウェア基準: **H3 v1.2.1**, commit `a3a0d42f664142aabaeef6bb08b0b223d8a94c63`.

This document clarifies rights; it does not replace [LICENSE](LICENSE), impose additional restrictions on existing software, change version numbers or republish ZIPs. / 本書は権利の説明です。[LICENSE](LICENSE)を置き換えず、既存ソフトウェアへ独自制限を加えず、版番号変更やZIP再公開も行いません。

| Scope / 対象 | Rights and applicable terms / 権利と適用条件 |
|---|---|
| Current code and workflow / 現行コード・ワークフロー | MIT for FURU original contributions; per-package GPL-3.0-only/MIT/Apache-2.0 for bundled nodes. / FURU独自部分はMIT。同梱ノードはパッケージごとにGPL-3.0-only・MIT・Apache-2.0。 |
| Rights holders / 権利者 | FURUYAN1234 (root, standard prompt and MV); Adudeguyman (AudioRefine); PlagueKind and LightX2V authors; pysssss (Custom Scripts); Long Video/ComfyUI contributors and MiniMax model rights holders. / FURUYAN1234（ルート・標準プロンプト・MV）、Adudeguyman（AudioRefine）、PlagueKind・LightX2V作者、pysssss（Custom Scripts）、Long Video・ComfyUIの各寄与者とMiniMaxモデル権利者。 |
| Third-party models and services / 第三者モデル・サービス | MiniMax H3 has a separate Community License; Qwen and Whisper models retain their source terms. / MiniMax H3は別途Community License。Qwen・Whisper等のモデルも取得元の条件に従います。 |
| Existing releases and ZIPs / 既存Release・ZIP | Keep the license shipped with the acquired version; no retrospective custom restrictions. / 取得した版に付属する許諾を保持し、独自制限は遡及適用しません。 |
| Custom terms target in this revision / 今回の独自条件適用対象 | None of the existing code, workflow JSONs or inherited licensed files. / 既存コード・ワークフローJSON・従前許諾のあるファイルには新規適用しません。 |

Existing MIT/GPL/Apache grants allow paid distribution and commercial use subject to their respective obligations. No additional FURU application, permission or fee is required for those licensed uses. This does not authorize a noncommercial third-party model for commercial use. / 既存のMIT・GPL・Apache許諾は、それぞれの義務を守る有料配布・商用利用を認めます。その許諾内の利用についてFURUへの追加申請・許可・利用料は不要です。第三者の非商用モデルの商用利用まで許可するものではありません。

Retain the license texts and copyright/NOTICE files that apply to each component. Modified redistribution must satisfy the respective license, including source and notice obligations where applicable. The request to display service notices does not add a new requirement to MIT/GPL/Apache-only services. / 各構成要素のライセンス本文・著作権表示・NOTICEを保持してください。改変再配布では各許諾に従い、該当する対応ソースや表示の義務を満たしてください。サービス説明ページへの表示のお願いを、MIT・GPL・Apacheだけのサービスへ追加するものではありません。

See [component/model notices](LICENSES_AND_NOTICES.md) and [FURU usage guidance](FURU_TERMS.md). / [構成要素・モデルの条件](LICENSES_AND_NOTICES.md)と[FURUの利用案内](FURU_TERMS.md)を参照してください。

## File-level scope / ファイルごとの範囲

| Files/components / ファイル・構成要素 | Rights and conditions / 権利・条件 |
|---|---|
| `workflows/*.json`, root scripts and documentation / ルート処理と文書 | FURU original portions under root MIT / FURU独自部分はルートMIT |
| `custom_nodes/comfyui-h3-standard-prompt/**`, `custom_nodes/comfyui-mv-workflow/**` | FURUYAN1234, MIT (each LICENSE retained) / 各LICENSEを保持 |
| `custom_nodes/ComfyUI-MiniMax-H3-Long-Video/**` | Long Video and inherited ComfyUI authors, GPL-3.0-only / Long Video・ComfyUIの各作者 |
| `custom_nodes/ComfyUI-H3-AudioRefine/**` | Adudeguyman, MIT |
| `custom_nodes/ComfyUI-PlagueKind-Nodes/**` | PlagueKind MIT; LightX2V-derived SLA files Apache-2.0 / SLA由来ファイルはApache-2.0 |
| `custom_nodes/ComfyUI-Custom-Scripts/**` | pysssss and contributors, MIT / pysssssと寄与者 |
| `licenses/MINIMAX_H3_LICENSE.txt`, `licenses/NOTICE.txt` | MiniMax model license, not the FURU software grant / MiniMaxモデル条件でありFURU許諾ではありません |

Use per-file headers, bundled LICENSE/NOTICE files and the pinned source commit to resolve individual third-party contributions; the repository owner is not asserted to own all bundled material. / 個々の第三者寄与はファイル内表示・同梱LICENSE/NOTICE・上記ソースコミットで確認し、リポジトリ所有者が同梱物すべての権利者であるとは扱いません。
