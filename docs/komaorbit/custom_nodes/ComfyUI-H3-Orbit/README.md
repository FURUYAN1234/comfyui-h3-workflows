# KomaOrbit custom nodes / KomaOrbit追加ノード

See the package README for full installation, model downloads, controls and outputs. / 詳しい導入・モデル取得・操作・保存先は配布ルートのREADMEを参照してください。

Install requirements.txt with the ComfyUI Python, separately obtain WorldMirror with prepare_runtime.py --worldmirror after reviewing its terms, and build/check gsplat with prepare_runtime.py --renderer. / ComfyUI用Pythonで依存を導入し、WorldMirrorを条件確認後に別途取得、gsplatをビルド・確認します。

Left drag: orbit; right drag: pan; wheel: composition zoom. / 左ドラッグ＝回転、右ドラッグ＝平行移動、ホイール＝保存構図の拡大縮小。

MIT for original code; bundled face-detector source retains MIT/Apache-2.0 notices. Model weights are separate and the face detector downloads its pinned checkpoints on first use. / 独自コードはMIT、同梱顔検出コードのMIT/Apache-2.0表示を保持します。重みは別途取得し、顔検出重みは初回に自動取得します。
