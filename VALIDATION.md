# Validation / 検証範囲

## Publication revision v1.0.0 / 公開用改訂

The publication work separates the original helper from external dependencies. Four fixed upstream source snapshots plus compatibility patches reconstruct 92 files from the supplied package with LF-normalized hashes. `git apply --check` and application succeeded for all four. The source ZIP initially supplied by the author matched all 108 entries in its manifest.

自作ノードと外部依存を分離しました。上流固定版へ4パッチを適用し、元配布物の第三者ファイル92個が改行をLFへそろえたハッシュで一致しました。元ZIPも108件のマニフェストと一致しています。

`verify_package.py` checks all three JSON graphs, link endpoints, prompt routes, the 4+2 sampling configuration, resolution and duration defaults, model metadata, empty image inputs, licenses, required patches and the file manifest. `build_package.py` runs these checks both before packaging and after extraction. These are offline source/package checks, not GPU generation.

検査スクリプトは3本の配線、入力経路、映像4＋音声2ステップ、解像度と秒数、モデル情報、空の画像入力、ライセンス、パッチ、ファイルハッシュを確認します。ZIP作成前と展開後の両方を検査します。GPU生成とは区別してください。

## Original generation record / 元配布構成の生成記録

The following was reported in the original package/article, not newly rerun during this publication revision. / 以下は元の配布構成・記事に記録された結果で、今回新たに実行した結果ではありません。

| Check / 確認 | Reported result / 記録 |
| --- | --- |
| T2V, I2V, Ref2V, direct English / 英語直接入力 | Each saved a 5-second MP4 / 各5秒MP4を保存 |
| T2V, 20 seconds / 20秒 | 480 video frames and 20-second audio; 15+5-second segments / 480フレームと音声20秒、15＋5秒の区間 |
| Japanese via LM Studio / 日本語変換 | Prompt output for all three routes, images sent for I2V/Ref2V / 3経路の文章出力と画像送信 |
| 30 and 60 seconds / 30・60秒 | Duration parsing and 2/4-segment planning only / 尺認識と2/4区間の計算のみ |

Reference environment: Windows/WSL2 Ubuntu, RTX 5080 16 GB, ComfyUI 0.34.0 (commit `3216c62e9962c3babd28a4dfea6e5aef50b8fe16`), frontend 1.51.9, 864×480, 24 fps. / 元記録の参考環境であり、最低要件ではありません。

## Not established / 未確認

- No fresh end-to-end video was generated with this restructured package. / 再構成後の配布物で新たな動画生成は行っていません。
- The original record inspected selected frames and audio stream length, not every frame or audible quality. / 元記録の品質確認は抜粋フレームと音声の有無・長さで、全再生や聴取評価ではありません。
- All three Japanese routes were not each rerun through final video generation. / 日本語3経路すべての完成動画までの一括再生成ではありません。
- Native Windows, other GPUs, 30/60-second video output, automatic model placement and arbitrary future dependency updates remain unverified. / Windowsネイティブ、別GPU、30/60秒実動画、自動モデル配置、今後の依存更新は未検証です。

Reproduce the short test in the [README](README.md) on your own environment before a long generation. Report the package version, OS, GPU, ComfyUI revision and error text without private paths or credentials. / 長尺生成の前にREADMEの短い例で確認してください。問い合わせには版と環境を添え、秘密情報は含めないでください。
