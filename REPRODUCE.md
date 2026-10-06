# v1.2.0-ref1-license1 再構築

専用リポジトリのタグ v1.2.0-ref1-license1 をクリーンに取得します。Python標準ライブラリだけで再構築できます。

```text
git clone --branch v1.2.0-ref1-license1 --depth 1 https://github.com/FURUYAN1234/comfyui-h3-workflows.git h3-source
cd h3-source
python -B build_release.py ../H3_T2V-I2V-Ref2V-MV_20260926115639_v1.2.0-ref1-license1.zip
```

再構築ZIPを新規フォルダーへ展開し、展開したルートで python -B verify_package.py を実行します。SHA256SUMS.jsonの全件一致、欠落と余分なファイルも検査します。マニフェスト自身のみ自己ハッシュ対象外です。

同じタグの配布ZIPと再構築ZIPの全相対パス・SHA-256を照合します。圧縮ライブラリが同じ場合のZIPバイト一致も確認しますが、一般の再現条件は展開内容の一致です。

GitHub自動生成のSource code (zip)はルート名やZIPメタデータが配布ZIPと異なります。ZIPそのもののSHA-256一致とは説明しません。ルート名を除いた全相対パスと内容ハッシュは配布ZIPと一致することを公開後に検査します。

生成物はソースツリー外へ出力してください。ビルダーは `.git`、Python/pytest/mypy/ruffのキャッシュ、`.pyc`、`.DS_Store` とマニフェスト自身を除くファイルを収録します。`.gitignore` は参照しないため、原本ZIP、`dist/`、`scratch/`、仮想環境、検査ログのないクリーンなソースを使ってください。第三者LICENSEやrequirementsも保持されます。配布者が内容を更新した場合だけ `--update-manifest` を使い、再検査します。既存の出力ZIPは上書きせず、別名を指定してください。

## ライセンス修正版の検査

公開v1.2.0-ref1から既存ノードとワークフローを変更せず、Apache-2.0本文・適用範囲・再発防止検査を追加した配布修正版です。機能版とワークフロー名はv1.2.0-ref1を保持し、タグ・外側ZIP・archive_rootでlicense1を識別します。main/v1.2.1の機能変更は含みません。

ビルド前と展開後にApache-2.0全文（CRLF/LFのみ正規化）、対象2ファイルの説明、由来・改変表示、既存MIT表示を検査します。本文の欠落・改変はマニフェスト更新でも通過しません。python -B -m unittest test_license_packaging -v で13件の回帰検査を実行できます。
