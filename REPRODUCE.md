# Rebuild the separate kits / 独立ZIPの再構築

Use the release tag matching the asset. From the repository root / 対象Releaseタグを取得し、リポジトリ直下で実行:

```sh
python -B build_individual.py video /absolute/output/H3_Video_20261114065048_v1.2.2.zip --stamp 20261114065048
python -B build_individual.py mv /absolute/output/H3_MV_20261114065048_v1.2.2.zip --stamp 20261114065048
```

For KomaOrbit, change to `docs/komaorbit` and run / KomaOrbitは専用ディレクトリへ移動して実行:

```sh
python -B build_release.py /absolute/output/KomaOrbit_20261105183001.zip
```

Destinations must be outside the checkout and not exist. Extract each ZIP into a fresh directory and run its `python -B verify_package.py`. The collection root is not an installable combined package. / 保存先はチェックアウト外の未作成ファイルを指定します。各ZIPを新規フォルダーへ展開し、同梱の検査を実行してください。リポジトリ全体を一括でComfyUIへ入れる構成ではありません。

The split H3 kits preserve v1.2.2 workflow filenames and runtime bytes. `RUNTIME_SHA256.json` lists those files; `SHA256SUMS.json` covers the entire kit, including documentation. / H3分割版はv1.2.2のJSON名と生成コードを維持しています。実行用ファイル一覧と全配布物のハッシュを別々に記録します。
