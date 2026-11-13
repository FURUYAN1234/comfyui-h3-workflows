# v1.2.2 validation / v1.2.2の検証（2026-11-14）

Public-bundle checks use only the included schema/client, without private Qwen nodes. / 公開用の検査は同梱スキーマとクライアントだけを使用し、個人用Qwenノードは使いません。

- 60 visual-audit and 6 motion-witness regressions pass, including valid held action, replay, later-frame duplicates, uncertain visibility, independent defects, untimed opening input and focused-inspection scope. / 動作保持・やり直し・後続フレームの人物重複・不確定・独立欠陥・時刻なし冒頭・限定検査の対象範囲を含む画像検査60件と動作観測6件に合格しました。
- Seven clean-public client checks cover missing private mappings, Japanese screen-text bans, chosen/missing model readiness, typed conversion, malformed/incomplete JSON and request-specific failure receipts. / 個人用ノード不在、画面文字の禁止指定、指定モデルと未読込、構造化変換、不正・未完了JSON、失敗時の証拠対応を公開クライアント7件で確認しました。
- The actual bundled client inspected 12 ordered images through the configured LM Studio model: valid ongoing/held action → confirmed_no_replay (276.9 s); completed sequence restarted → confirmed_replay (317.5 s). / 同梱クライアントから設定済みLM Studioへ12枚の時系列画像を実送信し、正常な動作継続・保持はやり直しなし（276.9秒）、完了後の再開はやり直し検出（317.5秒）となりました。
- An existing generated same-scene two-character fixture was independently confirmed_duplicate through that same public client (92.08 s). / 既存の同一場面に2人を生成した異常素材を同じ公開クライアントで検査し、人物重複を検出しました（92.08秒）。
- Transport measurements run from each client call to parsed response, excluding model startup and generation of reused fixtures. They are wall-clock observations, not billing or speed guarantees. / 時間はクライアント呼出から応答解析までの実測で、モデル起動と再利用素材の生成は含みません。請求額や速度の保証ではありません。
- Existing distribution/license/path/MV checks and the 13 JavaScript security checks pass. Python test mocks now restore prior modules so combined execution remains valid. / 既存の配布・ライセンス・パス・MV検査とJavaScript安全性13件に合格しました。Pythonテストのモックは元のモジュールを復元し、まとめて実行しても干渉しないよう修正しました。

- Ordinary public T2V workflow executed through an isolated ComfyUI with only the six bundled node packages: run `e7fc6de8-937d-4933-bb83-5a6eaeca0b3f`, 2026-11-14 07:01:52–07:10:46 JST, 534.557 s including LM conversion, cold model loading, video/audio generation, inspection and final saving; no cache or retry. / 同梱6パッケージだけを読み込む隔離ComfyUIで公開T2Vの通常経路を実行しました。実行IDは同左、07:01:52～07:10:46、実測534.557秒です。LM変換・コールドモデル読込・映像音声生成・検査・最終保存を含み、キャッシュ・再試行はありません。
- Retained 15 s, 864×480, 24 fps, the specified Fused4 video model and four video steps, SLA settings, and two audio-refinement steps at denoise 0.5. The test payload alone disabled optional GPU/CPU switching to preserve the already loaded LM's placement; public JSON defaults and the switching feature remain unchanged. / 15秒・864×480・24fps、指定Fused4モデルと映像4ステップ、SLA、音声2ステップ・denoise 0.5を維持しました。既存LMの配置を保全するため試験payloadだけGPU/CPU切替をOFFにし、公開JSONの既定値と切替機能は維持しました。
- The first candidate passed broad and independent opening inspection. Selected actual frames show one consistent character, a single wave, the held pose and the same park; no material visual break was observed. Final H.264/AAC output is 15.000 s. The configured LM stayed loaded; no TTL-related audit failure occurred. / 初回候補が全体検査と独立した冒頭検査に合格しました。実フレームで同一人物・1回の手振り・その後の保持・公園の継続を確認し、意味を損なう破綻は見られませんでした。最終H.264/AACは15.000秒です。設定LMは読込を維持し、TTLによる検査不能は発生していません。
- The final public T2V JSON was loaded in the actual UI with no missing-node dialog; four steps and the retained GPU/CPU option were visible. Human full-length audio listening is not claimed. / 公開T2Vの最終JSONを実画面で読み込み、不足ノード表示がないこと、4ステップと既存GPU/CPU設定を確認しました。人による全編の音声試聴は今回の確認に含めません。

No other-PC GPU or Windows-native generation claim is made. The older results below retain their original scope and date. / 別PC・WindowsネイティブのGPU実生成は今回確認していません。以下の過去検証は元の日付と範囲で保持します。

# v1.2.1 検証範囲（2026-09-23）

v1.2.0のT2V・I2V・Ref2V・MV機能を保持し、YuE2 schema v2の複数人物契約をMVへ引き継ぐ経路を追加しました。

- schema v2の2人物を別々のSubject／話者IDへ割り当て、人物の融合・分裂・入れ替わり・分身と、ソロ歌詞の複数口割当を禁止する指示を単体検査。
- visual情報のないschema v1フォールバック、重複ID、空配列、不足項目、不正schemaを正常例・異常例として検査。
- v1.2.1の通常経路実行ID：`2ab581ce-9845-4df4-b58c-100371e4924c`。
- YuE2 v1.6.1の同じ2人物キャラクターシートとschema-v2バンドルを入力し、H3の5区間すべてがattempt 1で自動検査合格。
- 完成MP4：`ComfyUI/output/video/MV/二人の夏色メモワール_20260923-100647/final.mp4`。
- 実測60.000秒、864×480、24fps CFR、H.264 High／AAC-LC 48kHz stereo。
- Whisper整列した日本語字幕4区間（34.5–55.5秒）を焼き込み、各字幕区間の実フレームで全文表示を確認。
- タイトル、終盤リンク、映像／音声の1秒フェードを実フレームとレポートで確認。
- フェード前58秒の完成AACと元FLACの相関0.999743、NaNなし、統合ラウドネス-15.7 LUFS、true peak -1.6 dBFS。
- 2秒以上の意図しない黒画面・静止をFFmpeg検査で検出せず。
- 確認フレームでは、アカリ（赤橙ボブ、眼鏡なし）とヒカリ（金髪ボブ、青い目、丸眼鏡）が別人物として維持され、人物融合・未登録の分身・明らかな髪型不一致・入れ替わりは見られなかった。
- H3の参照誘導は完全な顔一致を保証しないため、画風・顔・髪・眼鏡・服装・人物数の目視確認を前提とする。

## 継承したv1.1.9検証

v1.1.8を基準に、指定尺を超える明示タイムラインと、混合禁止リスト内の `歌` を見落とす日本語指示解析を修正しました。v1.1.8のLM Studio進捗表示、モデル、映像4step、音声2step・denoise 0.5、可変尺、区間再開は維持しています。

- 異常例：5秒要求に対する `[00:00-00:45]` を拒否し、5秒内の正常案を受理。
- 正常例：引用符内の時刻や、要求尺内のタイムラインは尺超過として誤検出しないことを確認。
- 異常例：`台詞・歌・音楽はなし` を全体無言として認識。
- 正常例：`歌はなし。女性が「こんにちは」と話す。` は指定台詞まで抑止しないことを確認。
- T2V、I2V、Ref2Vを修正後の通常ワークフローから内蔵ブラウザーで実生成。各5.000秒、864×480、24fps、H.264/AACで、区間監査は合格。
- 実環境Standard Prompt 65件、配布用Standard Prompt／デバイス58件、Long Video 88件、配布・設定11件の回帰検査に合格。
- 新規展開物の `verify_package.py`、全ハッシュ、余分なファイル、クリーンタグからの再構築はv1.2.1公開前検査で実行する。

選択フレームでは人物の分身、明らかな髪型不一致、入れ替わりを確認していません。H3には歌詞本文を渡さず、完成音源を最終段で復元しているため生成台詞としての二重歌唱を避ける契約も単体検査済みです。ただし、人による全60秒の連続試聴、別PC・WindowsネイティブGPU実行は未検証です。

## 2026-11-05 分割配布の検査

- H3 VideoはT2V・I2V・Ref2VのJSON3本と共通ノード5種。MVのJSON・専用ノード・KomaOrbitは含めません。
- H3 MVはMVのJSON1本と共通5種＋MV専用1種。他方式のJSONは含めません。
- 各ZIPを別の新規フォルダーへ展開し、接続・設定・モデル取得情報・権利表示・全ファイルのSHA-256を確認。
- 余分なワークフロー混入、ライセンス欠落を拒否し、元に戻すと合格することを確認。
- 動画版115ファイル、MV版120ファイルの実行用ソースとJSONは、分割前の同じソースとバイト単位で一致。
- 分割配布1件、ライセンス13件、既存仕様8件、モデル情報パス34件、MV可搬性3件の検査を実行。
- 今回は配布・文書の改訂です。新しいGPU生成や別PCでの生成は実施していません。上記の通常生成の記録は以前の検証であり、今回のZIP展開検査とは区別します。
