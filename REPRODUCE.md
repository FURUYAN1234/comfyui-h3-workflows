# v1.1.8 再構築

専用リポジトリのタグ v1.1.8 をクリーンに取得します。Python標準ライブラリだけで再構築できます。

```text
git clone --branch v1.1.8 --depth 1 https://github.com/FURUYAN1234/comfyui-h3-workflows.git h3-source
cd h3-source
python -B build_release.py ../H3_T2V-I2V-Ref2V_20260911222017_v1.1.8.zip
```

再構築ZIPを新規フォルダーへ展開し、展開したルートで python -B verify_package.py を実行します。SHA256SUMS.jsonの全件一致、欠落と余分なファイルも検査します。マニフェスト自身のみ自己ハッシュ対象外です。

同じタグの配布ZIPと再構築ZIPの全相対パス・SHA-256を照合します。圧縮ライブラリが同じ場合のZIPバイト一致も確認しますが、一般の再現条件は展開内容の一致です。

GitHub自動生成のSource code (zip)はルート名やZIPメタデータが配布ZIPと異なります。ZIPそのもののSHA-256一致とは説明しません。ルート名を除いた全相対パスと内容ハッシュは配布ZIPと一致することを公開後に検査します。

生成物はソースツリー外へ出力してください。.gitとPythonキャッシュ以外のファイルは収録され、第三者LICENSEやrequirementsも保持されます。配布者が内容を更新した場合だけ --update-manifest を使い、再検査と新しい版番号・タグを作成します。
