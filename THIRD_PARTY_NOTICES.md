# Third-party notices / 第三者の出典と変更表示

The compatibility patches were prepared on 2026-09-08 from the supplied workflow distribution. They reproduce its modified files against the pinned upstream snapshots below; they are not unmodified official releases. / 互換差分は2026年9月8日に元配布物から整理しました。下記の上流固定版に対する修正版であり、各作者の公式無改変版ではありません。

| Project / プロジェクト | Upstream / 出典 | Base commit / 元コミット | Terms / 条件 |
| --- | --- | --- | --- |
| Custom Scripts | [pythongosssss](https://github.com/pythongosssss/ComfyUI-Custom-Scripts) | `609f3afaa74b2f88ef9ce8d939626065e3247469` | MIT |
| H3 AudioRefine | [Adudeguyman](https://github.com/Adudeguyman/ComfyUI-H3-AudioRefine) | `fd5a8dfe8b4c636b1877abd07d11ebc985eddbed` | MIT |
| H3 Long Video | [palealloy2999-prog](https://github.com/palealloy2999-prog/ComfyUI-MiniMax-H3-Long-Video) | `f3fc996068a9319ace1d99d7c6ef54747a4018d2` | GPL-3.0-only |
| PlagueKind Nodes | [PlagueKind](https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes) | `59f54d359bbabff8bb813b1e3e381dd29843e720` | MIT |

License texts and original copyright notices are preserved in [licenses/](licenses/). Nested upstream notices, including SLA/AdaLN-related attributions, remain in the obtained sources and patch context. No authorship of third-party code is claimed. / ライセンス本文と元の著作権表示を保持しています。SLA/AdaLNなど内部構成の出典も取得元のソースと差分内に残します。他者のコードを自作として表示しません。

For each project, `dependencies.lock.json` lists every changed file and the hashes of files from the original supplied snapshot. Read the corresponding `patches/<project>.patch` for the exact modifications. The differences cover prompt/timeline and save integration, audio refinement, SLA compatibility, and frontend behavior. These are source patches, not binaries. Use the fixed source plus the patch when reproducing the modified version, and preserve this change record when redistributing it.

変更ファイル名は固定版一覧の`changed_files`、具体的な変更行は各パッチを参照してください。時間計画や保存連携、音声再精錬、SLA互換処理、画面側の差分を含みます。再配布時は元ソース、適用差分、ライセンスと変更表示の対応を保ってください。

For model provenance, consult [models.json](models.json) and the [model guide](docs/MODELS.md). Model license documents are reference notices; their presence does not mean weights are bundled or relicensed. / モデルの出典は別表に記載し、重みを同梱・再許諾しているという意味ではありません。
