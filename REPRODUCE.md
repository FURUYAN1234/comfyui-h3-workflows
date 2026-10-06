# v1.2.1 開発版の再構築

専用リポジトリのタグ v1.2.1 をクリーンに取得します。Python標準ライブラリだけで再構築できます。

```text
git clone --branch v1.2.1 --depth 1 https://github.com/FURUYAN1234/comfyui-h3-workflows.git h3-source
cd h3-source
python -B build_release.py ../H3_T2V-I2V-Ref2V-MV_20260923075019_v1.2.1.zip
```

再構築ZIPを新規フォルダーへ展開し、展開したルートで python -B verify_package.py を実行します。SHA256SUMS.jsonの全件一致、欠落と余分なファイルも検査します。マニフェスト自身のみ自己ハッシュ対象外です。

同じタグの配布ZIPと再構築ZIPの全相対パス・SHA-256を照合します。圧縮ライブラリが同じ場合のZIPバイト一致も確認しますが、一般の再現条件は展開内容の一致です。

GitHub自動生成のSource code (zip)はルート名やZIPメタデータが配布ZIPと異なります。ZIPそのもののSHA-256一致とは説明しません。ルート名を除いた全相対パスと内容ハッシュは配布ZIPと一致することを公開後に検査します。

生成物はソースツリー外へ出力してください。ビルダーは `.git`、Python/pytest/mypy/ruffのキャッシュ、`.pyc`、`.DS_Store` とマニフェスト自身を除くファイルを収録します。`.gitignore` は参照しないため、原本ZIP、`dist/`、`scratch/`、仮想環境、検査ログのないクリーンなソースを使ってください。第三者LICENSEやrequirementsも保持されます。配布者が内容を更新した場合だけ `--update-manifest` を使い、再検査します。既存の出力ZIPは上書きせず、別名を指定してください。

## 公開ライセンス修正版

公開版は [v1.2.0-ref1-license1](https://github.com/FURUYAN1234/comfyui-h3-workflows/tree/v1.2.0-ref1-license1) のソースとREPRODUCE.mdを使用してください。これは公開v1.2.0-ref1の既存ノード・ワークフローを維持したライセンス修正版です。main/v1.2.1の開発機能は含みません。上の既存v1.2.1タグはこのライセンス修正より前の履歴であり、mainの最新ライセンス修正は含みません。

mainにもApache-2.0本文、対象SLA2ファイルの適用説明、配布前・展開後の検査を反映しています。python -B -m unittest test_license_packaging -v で13件の回帰検査を実行できます。新しいライセンス修正版の公開に、mainの開発機能やmainのマニフェストを混入させないでください。
