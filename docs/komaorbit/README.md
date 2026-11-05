# KomaOrbit — compose an illustration in 3D / 一枚絵から3Dで構図を探す
<!-- bilingual-readme: paired english-japanese -->

KomaOrbit turns an illustration into a fixed 3D subject and an inferred environment in local ComfyUI. Move the camera with the mouse and save your composition at the original image dimensions. / 一枚絵から固定3Dの人物と補完背景を作るローカルComfyUIワークフローです。マウスでカメラを動かし、選んだ構図を元画像と同じ画素数で保存します。

The package contains workflow JSON, custom-node source, installation scripts, tests and notices. Supply your own images and obtain models separately. / 配布内容はワークフローJSON、ノードのソース、導入・検査用スクリプト、権利表示です。入力画像は利用者が用意し、モデルは別途取得します。

## Download / 入手先

See the release link and exact build identifier in [VERSION.json](VERSION.json). Use its named KomaOrbit ZIP. / [VERSION.json](VERSION.json)の配布先と識別情報を確認し、名前付きKomaOrbit ZIPを取得してください。

[Usage page / 操作・導入ページ](demo/) · [Model list / モデル一覧](models.json)

## Features and controls / 機能と操作

| Feature / 機能 | Operation and result / 操作と結果 |
|---|---|
| Orbit / 回転 | Left drag inside ⑩; continuous horizontal orbit and low/high viewpoints / ⑩の画像内を左ドラッグ。左右の連続周回とアオリ・俯瞰 |
| Pan / 平行移動 | Right drag moves the camera and target together / 右ドラッグでカメラと注視点を一緒に移動 |
| Zoom / 拡大縮小 | Wheel changes the saved composition, 0.5–4× / ホイールで保存される構図を0.5～4倍に調整 |
| Reset / 初期位置 | “中央に戻す” restores the base camera and model placement / カメラとモデル配置を初期化 |
| PNG / 画像保存 | “この視点を保存”; original width, height and aspect ratio / 元画像と同じ幅・高さ・縦横比で保存 |
| View grid / 角度別一覧 | ⑧: 3 heights × 73 directions = 219 PNGs, including the closing 360° view / 高さ3段階×73方位、終点360°を含む219枚 |
| 3D export / 3D保存 | Gaussian-splat PLY from ⑩'s export menu / ⑩の書き出しからGaussian Splat形式のPLY |
| Face detail / 顔の詳細 | Matched source face details are projected onto a fixed 3D surface / 元絵の顔の詳細を対応する固定3D曲面へ反映 |

Gaussian PLY is a splat representation. Use a Gaussian-splat-compatible importer; it is not a rigged polygon mesh. / Gaussian PLYは点群状の表現です。Gaussian Splat対応の読込機能を使ってください。ボーン付きポリゴンメッシュではありません。

## Requirements / 必要な環境

- Linux or WSL2, NVIDIA CUDA GPU, Git, Python 3.10+, C++ compiler and CUDA Toolkit matching PyTorch. / LinuxまたはWSL2、NVIDIA CUDA GPU、Git、Python 3.10以上、PyTorchに対応するC++コンパイラー・CUDA Toolkit。
- Recent ComfyUI with MiniMax H3, Qwen Image 2.1, Idol/TripoSplat and Gaussian-splat nodes. See [VALIDATION.md](VALIDATION.md) for exact tested versions. / MiniMax H3・Qwen Image 2.1・Idol/TripoSplat・Gaussian Splat標準ノードを含むComfyUI。検証版は[検証記録](VALIDATION.md)を参照。
- The local GPU is RTX 5080 16 GB; this is a tested configuration, not a guaranteed minimum. Native Windows and macOS are not validated. / 検証GPUはRTX 5080 16GB。最低要件を保証する値ではありません。Windowsネイティブ・macOSは未検証です。
- Sufficient RAM and disk for the models in [models.json](models.json). FL2VA is the normal route; Ref2VA and its LoRA are optional comparison downloads. / [モデル一覧](models.json)の大きなモデルを配置できるRAM・ディスクが必要です。標準はFL2VA。Ref2VA本体とLoRAは比較経路用です。

## Installation / 導入

Use the Python environment belonging to ComfyUI for every command. Activate its virtual environment first. / 以下はすべてComfyUI用のPythonで実行します。仮想環境の場合は先に有効化してください。

1. Extract the named release ZIP and open a terminal in that folder. / Releaseの名前付きZIPを展開し、そのフォルダーでターミナルを開きます。
2. Verify and install all three node packages. / 展開ファイルを検査し、3つのノードパッケージを導入します。

```sh
python verify_package.py
python install.py --comfy /path/to/ComfyUI --dependencies
```

If a node package already exists, back it up before adding `--replace`. The installer does not download model weights or modify user outputs. / 同名ノードがある場合はバックアップしてから`--replace`を付けます。モデル重みの取得や利用者の出力の変更は行いません。

3. Read the [WorldMirror terms](https://github.com/Tencent-Hunyuan/HunyuanWorld-Mirror/blob/c61300e47ce06db2f02c8e2551aae68be7ae3a0b/License.txt), then obtain its official code separately. This explicit command fetches its pinned inference source and license notices. / WorldMirrorの条件を確認して公式コードを別途取得します。次の明示コマンドは指定版の推論コードと権利表示を取得します。

```sh
python /path/to/ComfyUI/custom_nodes/ComfyUI-H3-Orbit/prepare_runtime.py --worldmirror
```

4. Build/check gsplat for your GPU. Point `--cuda-home` at the matching CUDA Toolkit, or omit it if already discoverable. / GPU用のgsplatをビルド・確認します。対応するCUDA Toolkitを指定し、既に検出できる場合は`--cuda-home`を省略します。

```sh
python /path/to/ComfyUI/custom_nodes/ComfyUI-H3-Orbit/prepare_runtime.py --renderer --cuda-home /usr/local/cuda
```

First compilation can take several minutes. The result is cached locally; no machine-specific binary ships in the ZIP. / 初回ビルドには数分かかる場合があり、結果はローカルにキャッシュされます。PC専用バイナリは同梱しません。

5. Obtain the models below, restart ComfyUI, and open `KomaOrbit.json`. / 以下のモデルを配置し、ComfyUIを再起動して`KomaOrbit.json`を開きます。

For a narrow window, use “ビューに合わせる” (fit view), then select a node and press `.` to focus it. / 細い画面では「ビューに合わせる」で全体を表示し、操作するノードを選んで`.`キーで寄せます。

## Model downloads and network access / モデル取得と通信

Each loader has `properties.models` containing the exact name, destination and upstream URL. Use ComfyUI's missing-model Download/Copy URL controls or [models.json](models.json). Save each file under `ComfyUI/models/<directory>/`. / 各ローダーにファイル名・配置先・取得URLを登録しています。不足モデルのDownload／リンクコピー、またはモデル一覧を使い、指定フォルダーへ配置します。

WorldMirror additionally needs [config.json and model.safetensors](https://huggingface.co/tencent/HunyuanWorld-Mirror/tree/main) in `ComfyUI/models/worldmirror/`. Separately downloaded code goes in `custom_nodes/ComfyUI-H3-Orbit/vendor/worldmirror/`. / WorldMirrorは追加で`config.json`と`model.safetensors`を`models/worldmirror/`へ配置します。公式コードは別途取得コマンドにより左記へ入ります。

The first face-restoration run automatically downloads missing hysts detector weights into the standard Hugging Face cache: HRNetV2 revision `9b3435248b26aeb82e2a8578fe9d86d5d57158af`, YOLOv3 revision `afdd4226a79ae8bb81f334dbcffd34f8cc000c38`. / 顔復元の初回は、不足しているhystsの検出用重み2つをHugging Face標準キャッシュへ自動取得します。固定版は左記です。

There is no cloud generation API or API-key requirement. Model/code downloads contact their upstream hosts; image processing is local. / クラウド生成API・APIキーは不要です。モデル・コードの取得時は配布元へ通信し、画像処理はローカルで行います。

## Generate and save / 生成と保存

1. Choose your illustration at ①. Begin with a clearly visible subject and face detail. / ①で画像を選びます。初回は人物と顔の線がはっきりした画像で試してください。
2. Keep ② at long edge 1024, set ③ to “固定3Dを新規生成” and ⑥ to “背景用 FL2VA（73枚）”, then Run. / ②は長辺1024、③は固定3Dの新規生成、⑥はFL2VAを選んで実行します。
3. Use left drag, right drag and wheel inside ⑩. The white frame is saved; dimmed areas are outside it. / ⑩の画像内で左ドラッグ・右ドラッグ・ホイールを操作します。白枠内が保存範囲です。
4. Save with “この視点を保存”; use ⑧'s “全視点をフォルダー保存” for the full grid. / 1枚は「この視点を保存」、一括保存は⑧を使います。

Defaults: H3 28 steps/73 frames; Qwen angle references 4 Lightning steps; background passes 25 steps; Idol 50 steps. / 標準設定はH3が28ステップ・73枚、角度参照QwenがLightning4ステップ、背景が25ステップ、Idolが50ステップです。

The isolated 1024×1536 test took 18m03.489s from execution start to success, including loading and additional compilation; four image-description nodes were cached. Initial installation, prior renderer setup, input creation and later saves are excluded. See [VALIDATION.md](VALIDATION.md) for scope. / 分離環境の1024×1536検証は開始から成功まで18分03.489秒でした。読込と実行中の追加ビルドを含み、画像説明の4ノードはキャッシュを利用しています。初期導入・事前の描画環境構築・入力作成・後からの保存は含みません。検証範囲は[検証記録](VALIDATION.md)を参照してください。

## Outputs and reopening / 出力と再開

| Location / 場所 | Contents / 内容 |
|---|---|
| `output/H3_ORB360/<scene-id>/` | Scene manifest, splats, textures and working data / シーン情報・3D・テクスチャ・中間データ |
| `output/H3_ORB360/snapshots/` | Original-size selected-view PNGs / 元画像寸法の構図PNG |
| Folder export / フォルダー保存 | Angle/elevation/zoom-named PNGs at processing resolution, retaining source aspect ratio, plus metadata / 方位・高さ・倍率を含む名前の処理解像度PNGと情報。元画像の縦横比を維持 |

Keep the complete scene folder: the PLY alone does not include the external panorama/floor textures. With the same source image, select “固定3Dを開く（3D ID指定）” and supply the scene ID. / シーンフォルダー全体を保持してください。PLY単体には外部背景・床テクスチャは入りません。同じ元画像で固定3Dを開く方式を選び、シーンIDを指定します。

## Troubleshooting and limitations / トラブル対応と制約

- Missing nodes: install all three packages, restart and check the ComfyUI version/import log. / ノード不足は3パッケージの配置・再起動・ComfyUIの版と読込ログを確認します。
- Missing weights: compare filenames and folders with `models.json`. / 重み不足はモデル一覧のファイル名・配置先を照合します。
- Renderer errors: match PyTorch/CUDA Toolkit/compiler, then rerun the renderer setup command. / 描画エラーはPyTorch・CUDA Toolkit・コンパイラーの対応を確認して再ビルドします。
- CUDA out of memory: close other GPU jobs and review resolution/RAM. Lower resolution can lose face detail. / VRAM不足は他のGPU処理を止め、解像度・RAMを見直します。縮小すると顔の詳細も減ります。
- Hidden geometry, lighting and background perspective are inferred. Source-face recovery cannot guarantee exact identity at every angle or recover detail absent from the input. / 隠れた形状・照明・遠近は推定です。顔復元でも全角度の厳密一致や元絵にない詳細は保証できません。
- The lowest camera angle is limited by ground clearance and framing. Background depth uses a panorama/floor approximation. / 最低角度は地面との距離と構図に合わせて制限されます。背景の奥行きはパノラマと床で近似します。

## Credits and terms / クレジット・利用条件

Original KomaOrbit code/workflow: MIT. The T8 adapter includes a GPL-3.0-only ComfyUI compatibility module; ComfyUI-Idol is GPL-3.0 with Apache-2.0-derived portions, and anime-face-detector includes MIT/Apache-2.0 components. Complete source and notices are included. / 独自コード・ワークフローはMIT。T8アダプターの互換部分はGPL-3.0-only、ComfyUI-IdolはGPL-3.0とApache-2.0由来部分、顔検出コードはMIT/Apache-2.0です。対応ソースと権利表示を同梱します。

Models and separately obtained implementations retain their upstream terms, including commercial-use and regional restrictions. This software license grants no additional model/output permissions. / モデル・別途取得する実装には、商用利用・地域条件を含む各配布元の条件が適用されます。本ソフトのライセンスはモデルや生成物に追加の許諾を与えません。

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for MiniMax H3, pablodawson, MATLOWAI, Tencent, Qwen/Alibaba PAI, Idol/TripoSplat, BiRefNet, hysts/OpenMMLab, Kim2091, Real-ESRGAN, T8mars, ComfyUI, gsplat and Spark. / 各作者・モデル・実装へのリンクはクレジット一覧にまとめています。

## Verification and rebuild / 検証と再構築

[VALIDATION.md](VALIDATION.md) records the tested environment and scope. `verify_package.py` checks all SHA-256 values and rejects missing, changed or extra files. / 検証記録に環境と確認範囲を記載しています。検査スクリプトはSHA-256全件と欠落・変更・余分なファイルを確認します。

```sh
python verify_package.py
python build_release.py /absolute/path/outside-source/KomaOrbit.zip
```

Build from the release tag's `docs/komaorbit/` directory. The builder includes only this source package and does not install dependencies. / Releaseタグの`docs/komaorbit/`から再構築します。このソース配布物だけを対象とし、依存物の導入は行いません。

## Changes / 更新

2026-11-05: first source release with face recovery, precise face rendering, pan/zoom/reset, source-size PNG and portable runtime setup. / 顔復元、顔の描画精度、パン・ズーム・中央復帰、元画像寸法PNG、環境別ランタイム導入を含む初回ソース配布。
