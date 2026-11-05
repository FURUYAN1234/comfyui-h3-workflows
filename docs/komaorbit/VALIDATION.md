# Validation / 検証記録

## Environment / 環境

Validated on 2026-11-05 in a separately installed ComfyUI and virtual environment on the same RTX 5080 16 GB computer. This is environment-isolation testing, not a second physical GPU test. / 2026年11月5日、RTX 5080 16GB搭載PC内に別のComfyUIと仮想環境を新規導入して検証しました。環境を分離した検証であり、別の物理GPUでの検証ではありません。

| Component / 項目 | Tested value / 検証値 |
|---|---|
| OS / 実行環境 | Ubuntu under WSL2 / WSL2上のUbuntu |
| ComfyUI | v0.43.0, `426f5eea52a7ece1dd599fff4cb6cd1f352ddd24` |
| Python | 3.10.6, independent virtual environment / 独立した仮想環境 |
| PyTorch / CUDA | 2.13.0+cu130 / CUDA 13.0 Toolkit |
| Frontend / 画面 | 1.57.4 |
| Transformers | 5.15.1 |
| Renderer / 描画 | gsplat 1.5.3, compiled for the test GPU / 検証GPU向けにソースからビルド |
| Workflow / ワークフロー | 78 nodes, 161 links; all 48 node types loaded / 78ノード・161接続、使用する48種類を読込 |
| Custom nodes / 拡張ノード | Three bundled packages / 同梱3パッケージ |

The official ComfyUI core was unchanged. Existing model files were shared read-only; no model weights are included in this release. The pinned WorldMirror implementation was downloaded separately using the documented setup. / ComfyUI本体は公式版のままです。既存のモデルファイルだけを読取専用で共有し、配布には含めていません。WorldMirrorの指定版実装は説明どおり別途取得しました。

## Normal generation / 通常生成

A 1024×1536 monochrome illustration of one standing person was processed through the normal fixed-3D route. H3 remained at 28 steps and 73 frames, angle references at 4 steps, backgrounds at 25 steps, and Idol at 50 steps. / 1人が立つ1024×1536のモノクロイラストを、固定3Dの通常経路で処理しました。H3は28ステップ・73枚、角度参照は4ステップ、背景は25ステップ、Idolは50ステップを維持しています。

Execution succeeded in **18m03.489s** from ComfyUI history start to success. This includes model loading and additional on-demand compilation during the run. Four image-description nodes were cached from a separate **13.009s** reproduction. It excludes initial installation, the earlier renderer setup/build, source-image creation and subsequent save/export tests. / 履歴の開始から成功までの実測は**18分03.489秒**です。モデル読込と実行中の追加ビルドを含みます。画像説明の4ノードは別途**13.009秒**の切り分け実行のキャッシュを使用しています。初期導入、事前の描画環境ビルド、入力画像作成、後続の保存検査は含みません。

The face-detail stage completed, followed by 219 fixed-camera PNGs and the final Gaussian-splat PLY. The 219-frame render stage took 118.509s. / 顔の詳細復元を通過し、固定カメラのPNG219枚と最終Gaussian Splat PLYを生成しました。219枚の描画工程は118.509秒でした。

## Saved results / 保存結果

Seven saved views were actually opened and inspected: base, 1.6× zoom, 0.65× zoom, pan, low, high and rear. All PNGs were **1024×1536**, their camera metadata matched the requested cameras, and all seven image hashes differed. / 基準・1.6倍・0.65倍・パン・アオリ・俯瞰・背面の7枚を実際に開いて確認しました。すべて**1024×1536**で、カメラ情報が指定値と一致し、7枚すべて異なる画像でした。

Zoom changed the saved crop and exposed background; pan changed composition. Eye/mouth detail remained readable without an opaque gray face overlay in this test. Backgrounds are inferred and softer than the source. / ズームは保存画像の切り取り範囲と見える背景を変え、パンも保存構図へ反映されました。この入力では目と口の線を判別でき、顔を覆う不透明な灰色塗りは見られませんでした。背景は推定されるため元絵より柔らかい描写が残ります。

Folder export completed with **219 PNGs**, named by azimuth, elevation and zoom, plus metadata. Grid images in this test were **682×1023** at the prepared processing resolution; original-pixel-size preservation applies to the final selected-view PNG. The grid retained the source 2:3 aspect ratio. / フォルダー書き出しは方位・高さ・倍率を含む名前の**219枚**と情報ファイルで完了しました。この入力の一覧画像は処理用の**682×1023**です。元画像と同じ画素数を保持するのは最終ノードの構図PNGで、一覧画像も元の2:3の縦横比を維持します。

The actual grid elevations were about −12.9°, 0° and +30°; ground clearance constrained the lowest angle. / この入力では地面との距離により最低角度が制限され、高さは約−12.9度・0度・+30度でした。

## Automated checks / 自動検査

- 104 Python regressions and 29 JavaScript regressions passed. / Pythonの回帰104件とJavaScriptの回帰29件が成功しました。
- Three focused portable-runtime tests passed after correcting a test-harness import order. / 検査側のimport順を修正したうえで、環境依存処理の検査3件が成功しました。
- Workflow source checks cover the complete graph, face connections, normal generation settings, model URLs and the final viewer's activation marker. / 全グラフ・顔復元接続・通常設定・モデルURL・最終ビューアーの有効化情報を検査します。

## Browser operation checks / 画面操作の確認

The workflow was loaded through the normal file-open dialog in the Codex in-app browser. Left-button dragging changed orbit and elevation; wheel input changed composition in both directions. Two PNGs saved using the actual viewer button retained **1024×1536**, with zoom metadata **3.958×** and **0.662×**; both files were opened and their crop/framing changes confirmed. / 内蔵ブラウザーの通常のファイル読込で検証しました。左ドラッグで回転・高さが変わり、ホイールで拡大・縮小しました。実際の保存ボタンから書き出した2枚も**1024×1536**で、記録倍率は**3.958倍**と**0.662倍**です。両方を開いて切り取り範囲・構図の変化を確認しました。

After opening the expanded 3D viewer, moving its camera and closing it, “中央に戻す” restored the full subject to the center. The original aspect-ratio frame remained intact. / 拡大3Dビューアー内でカメラを動かして閉じた後も、「中央に戻す」で人物の全身が中央へ復帰し、元画像の縦横比の枠を維持しました。

The source-only JSON loaded with no input image or cached scene. Its single bilingual help panel is on the left, processing groups on the right and output controls visible. The initial view was adjusted after checking a narrow browser pane; the whole graph was then checked at a 1280×900 desktop viewport. Use the fit-view button for narrower panes. / 素材非同梱JSONは入力画像・過去シーンなしで読み込め、左側の英日説明1枠、右側の処理グループ、出力操作を確認しました。細いブラウザー枠での表示を踏まえて初期倍率を調整し、1280×900のデスクトップ表示で全体を確認しています。細い画面では「ビューに合わせる」を使ってください。

Pan composition passed the backend save tests. The automated mouse checks described above cover left dragging and wheel input; this automation interface does not provide native right-button dragging. / パンによる構図変更は保存側の検査に合格しました。上記の自動マウス検証は左ドラッグとホイールが対象で、この自動操作インターフェースでは右ボタンのドラッグを実行できません。

## Limits / 確認範囲

This single input is not proof of arbitrary-image quality, exact reconstruction of hidden geometry, or support for every GPU/OS. Native Windows, macOS and other GPUs remain untested. / 今回の1入力だけで任意画像の品質、隠れた形状の厳密な復元、全GPU・OSへの対応を保証するものではありません。Windowsネイティブ・macOS・他GPUは未検証です。
