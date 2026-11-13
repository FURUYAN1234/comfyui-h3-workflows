# Workflow guides / ワークフロー別ガイド
<!-- bilingual-readme: paired english-japanese -->

Choose a package in the bilingual catalog, then follow its own guide. / 英日併記の一覧から必要なZIPを選び、専用ガイドへ進んでください。

- [ZIP catalog / 配布一覧](README.md)
- [T2V](docs/h3/t2v/README.md)
- [I2V](docs/h3/i2v/README.md)
- [Ref2V](docs/h3/ref2v/README.md)
- [MV](docs/h3/mv/README.md)
- [KomaOrbit](docs/komaorbit/README.md)
- [H3 setup / H3共通導入](docs/h3/SETUP.md)

## Changes in v1.2.2 / v1.2.2の変更点

- Restores the bundled LM Studio schema and client; a clean kit no longer needs private Qwen nodes. Start LM Studio and load your chosen vision model manually. / 同梱スキーマとLM Studioクライアントを再接続し、非同梱の個人用Qwenノードへの依存を解消しました。LM Studioと選択した画像対応モデルは手動で起動します。
- Recognizes screen-text bans without inventing speech. / 「画面文字なし」などの指定を発話要求と取り違えないよう修正しました。
- Confirms suspected action replay using ordered action phases; checks every suspected duplicate frame independently. One clarification is allowed for uncertain visibility. Unresolved evidence stays unavailable and cannot authorize a defect-based rerender. / 動作のやり直し疑義を時系列の動作段階で確認し、人物重複の疑義は該当する全フレームを個別確認します。不確かな場合の再確認は1回までとし、未確定は検査不能として保持し、欠陥を根拠にした再生成へ進めません。
- Shared repairs apply to H3 Video and MV. KomaOrbit is unchanged. / 共通修正はH3 VideoとMVに適用し、KomaOrbitは変更していません。
