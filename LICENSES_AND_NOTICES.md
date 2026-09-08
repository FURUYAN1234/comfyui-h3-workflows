# Licenses and redistribution / ライセンスと再配布

## Original work / 独自作成部分

Original contributions by FURUYAN1234, including the prompt helper, original workflow arrangement and documentation, are offered under the [MIT License](LICENSE). This does not relicense dependencies, patch context, model weights or other authors' work. / FURUYAN1234の独自作成部分はMITです。他者のコード、パッチに含まれる上流部分、モデルの条件は変更しません。

## External nodes and patches / 外部ノードと互換差分

| Component / 対象 | License / ライセンス |
| --- | --- |
| ComfyUI-MiniMax-H3-Long-Video patch | [GPL-3.0-only](licenses/ComfyUI-MiniMax-H3-Long-Video.txt) |
| ComfyUI-H3-AudioRefine patch | [MIT](licenses/ComfyUI-H3-AudioRefine.txt) |
| ComfyUI-PlagueKind-Nodes patch | [MIT](licenses/ComfyUI-PlagueKind-Nodes.txt) |
| ComfyUI-Custom-Scripts patch | [MIT](licenses/ComfyUI-Custom-Scripts.txt) |

Full external node trees are obtained separately. Patches contain code and remain licensed material. Keep copyright and permission notices, the patch, its base revision and change notices together. Source provenance and reconstruction instructions are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [DEPENDENCIES.md](docs/DEPENDENCIES.md).

外部ノード一式は別途取得しますが、差分ファイルにもコードが含まれます。著作権表示、ライセンス、元コミット、変更の記録を保持してください。パッチ形式にすることは、ライセンス条件を免除する方法ではありません。

Merely collecting separate works does not automatically place every file under GPL. Conversely, labeling files MIT does not exempt a combined derivative program from GPL obligations. The helper loads the Long-Video timeline module at runtime; do not present their combined redistribution as unrestricted MIT-only software. Consult the applicable GPL terms for the distribution you actually make.

同じ配布物にまとめただけで全ファイルが自動的にGPLになるとは限りません。一方、結合した派生プログラムにGPLが適用される場合、その義務はMITの表示では消えません。本補助ノードは実行時にLong-Videoの時間計画モジュールを読み込みます。結合構成全体をMITのみとして再配布できるという説明は行いません。

## Models / モデル

No model weights are distributed here. Obtain models from their publishers and check each model card and terms. MiniMax H3 has a separate [Community License Agreement](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE), covering more than weight redistribution, including territorial and use restrictions. Its ordinary territory excludes the EU, UK, South Korea and USA. Commercial and related-product obligations may also apply. Linking to a model does not grant permission to use it. The supplied license copy is retained in `licenses/MINIMAX_H3_LICENSE.txt`; consult the publisher for current terms.

モデル重みは同梱しません。取得先のモデルカードと利用条件を確認してください。H3の条件は重みの再配布だけに限らず、通常の許諾地域から米国・EU・英国・韓国が除外されています。商用利用や関連製品にも条件があります。リンクを掲載していることは、利用許諾の代わりにはなりません。

Some quantized variants are published by third parties. A missing license field is not proof of unrestricted use; ask the publisher when terms or provenance are unclear. / 第三者による量子化版は、ライセンス欄が空でも無条件利用を意味しません。条件や出典が不明な場合は配布者へ確認してください。

## Media, privacy and affiliation / 素材・個人情報・関係者

Use images, music, voices and prompts you have rights to use. The package excludes private inputs and generated media. Public author names, software names and license notices are intentionally preserved. This is an independent community project, not an official MiniMax, ComfyUI or LM Studio release. This document records distribution scope; it is not a legal guarantee for every jurisdiction or use.

追加する画像や音声などの権利は利用者側で確認します。私有入力や生成物は含めず、出典に必要な公開著作権表示は残しています。本プロジェクトは独立したコミュニティー制作物です。あらゆる地域・用途での適法性を保証する文書ではありません。
