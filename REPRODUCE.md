# タグからの再構築

同じ保存先の v1.1.3_source.bundle は既存公開履歴を引き継いだローカルGitタグ v1.1.3 を含みます。外部リポジトリへの公開は行っていません。

Python 3 と Git が必要です。次を新規ディレクトリーで実行してください。

```
git clone <同じ保存先のv1.1.3_source.bundleへのパス> source
cd source
git checkout --detach v1.1.3
python build_release.py ../rebuilt.zip
```

ZIPを新規フォルダーに展開し、ComfyUI_H3_Workflows/verify_package.py を実行してください。公開予定ZIPと再構築ZIPの展開後の全ファイル相対パスとSHA256を比較します。SHA256SUMS.json自身もZIP間比較に含めます。外部RELEASE_CHECKS.jsonにタグ・コミット・比較結果を記録します。
