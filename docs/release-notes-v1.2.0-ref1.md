# v1.2.0-ref1 — H3 reference image binding / H3参照画像の対応付け修正

Ref2V and MV now feed every connected reference image to the H3 sampler in numeric socket order, including all images in a batch. The appearance audit uses the same order. Missing `<Picture N>` inputs are rejected before generation, and changed reference layouts invalidate old resume fingerprints. The optional audit reports `not_applicable` when disabled. / Ref2VとMVの複数参照画像を番号順に、バッチ内の全画像を含めてH3生成へ渡します。外見監査も同じ順序を使います。参照番号に足りない画像は生成前に拒否し、参照配置が変わる再開では旧結果を再利用しません。任意監査の無効時は`not_applicable`を返します。

The four workflow JSON files and their video/audio/model settings remain the v1.2.0 baseline. A focused local test and a 3-second normal-path H3 run with two references validated the affected path; a full-length MV and complete audio listening were not part of this check. / 4本のワークフローJSONと映像・音声・モデル設定はv1.2.0を継承します。焦点テストと参照画像2枚の3秒通常経路実生成で対象処理を確認しました。長尺MVと音声の全編試聴は未確認です。
