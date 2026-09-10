# v1.1.7 再構築

GitHubの `v1.1.7` タグを開き、GitHubが提供する Source code (zip) を取得します。この配布ZIPはその公開Git ZIPと同一のバイト列です。ZIP内の VERSION.json に日時と版、ワークフロー名、配布ルートを記録しています。

配布前には、GitHubから取得したSource code (zip)のSHA-256と配布ZIPのSHA-256が一致することを確認します。

新規展開先で `python verify_package.py` を実行。SHA256SUMS.jsonに列挙された全ファイルの一致と、未列挙の余分なファイルがないことを確認します。公開操作はこのビルドには含みません。配布ZIPとクリーンタグ再構築ZIPの全相対パス・SHA256一致を配布作業で確認します。
