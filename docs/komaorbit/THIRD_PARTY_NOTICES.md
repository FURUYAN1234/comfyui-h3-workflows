# Credits and licenses / クレジット・ライセンス

## Included source / 同梱ソース

- KomaOrbit original workflow, Python/JavaScript, installer and documentation: FURUYAN1234, MIT; see LICENSE. / 独自部分はMITです。
- T8mars Qwen Union adapter: MIT, upstream commit 1101f891b7c1fbefae22fb61e8b8b013666564f2. Its added ComfyUI compatibility module is GPL-3.0-only, with full corresponding source and license in that package. / T8marsの元のノードはMIT、追加のComfyUI互換部はGPL-3.0-only。ソース・全文を各パッケージに収録。
  https://github.com/T8mars/Comfyui-Qwen-Image-2.1-Fun-Controlnet-Union-T8
- hysts anime-face-detector: unmodified inference source, MIT, commit 98a9fb480fa04bdd96acbe4d98da191a898267f3. Included OpenMMLab mmpose/mmdetection/mmcv portions are Apache-2.0. File headers, complete licenses and THIRD_PARTY_LICENSES.md are retained under vendor/anime_face_detector_runtime. / hystsの推論コードとOpenMMLab由来部分の権利表示・ライセンス全文を保持。
  https://github.com/hysts/anime-face-detector

- ComfyUI-Idol: PedroMarinhoDev, unmodified source at commit 59ed4b43f0d04a2c8bac689f99e51901dd7c2329. GPL-3.0; Apache-2.0-derived Idol preprocessing/camera logic is identified in its NOTICE. Full source, both licenses and NOTICE are included. / Idol前処理・入力カメラ・精度設定の公式ノード。未改変ソース、GPL・Apache全文とNOTICEを保持。
  https://github.com/PedroMarinhoDev/ComfyUI-Idol

## Separately obtained components / 別途取得する構成要素

| Component / 構成要素 | Author and source / 作者・配布元 |
|---|---|
| MiniMax H3 | [MiniMax](https://huggingface.co/MiniMaxAI/MiniMax-H3), [Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) |
| FL2VA 360 Orbit LoRA | [pablodawson / Pablo Dawson](https://huggingface.co/pablodawson/MiniMax-H3-360-Orbit-LoRA) |
| Ref2VA ORB360 LoRA | [MATLOWAI](https://huggingface.co/MATLOWAI/MiniMax-H3-ORB360-CardSpin) |
| HunyuanWorld-Mirror | [Tencent Hunyuan](https://github.com/Tencent-Hunyuan/HunyuanWorld-Mirror), [Community License](https://huggingface.co/tencent/HunyuanWorld-Mirror/blob/main/License.txt) |
| Qwen Image / Qwen VL | [Qwen](https://huggingface.co/Qwen), [Comfy-Org repacks](https://huggingface.co/Comfy-Org) |
| Qwen Union | [Alibaba PAI](https://huggingface.co/alibaba-pai/Qwen-Image-2.1-Fun-Controlnet-Union), [T8 converted checkpoint](https://huggingface.co/t8star/Qwen-Image-2.1-Fun-Controlnet-Union-Comfy) |
| Multiple Angles LoRA | [fal](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) |
| Qwen Lightning | [LightX2V](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) |
| Idol / TripoSplat | [Idol repack](https://huggingface.co/PedroMarinhoDev/Idol-ComfyUI), [VAST-AI](https://huggingface.co/VAST-AI/TripoSplat) |
| BiRefNet | [BiRefNet](https://github.com/ZhengPeng7/BiRefNet), [Comfy-Org repack](https://huggingface.co/Comfy-Org/BiRefNet) |
| Face detector weights / 顔検出重み | [hysts HRNetV2](https://huggingface.co/hysts/anime-face-detector-hrnetv2), [hysts YOLOv3](https://huggingface.co/hysts/anime-face-detector-yolov3) |
| AnimeSharp | [Kim2091](https://huggingface.co/Kim2091/AnimeSharp) |
| Real-ESRGAN | [Xintao et al.](https://github.com/xinntao/Real-ESRGAN), [Comfy-Org repack](https://huggingface.co/Comfy-Org/Real-ESRGAN_repackaged) |
| ComfyUI | [Comfy-Org](https://github.com/Comfy-Org/ComfyUI), GPL-3.0 |
| gsplat | [Nerfstudio contributors](https://github.com/nerfstudio-project/gsplat), Apache-2.0; installed separately / 別途インストール |
| Spark | [Spark](https://github.com/sparkjsdev/spark), supplied by ComfyUI's viewer / ComfyUIビューアーが提供 |

The code package does not contain model/LoRA weights or the official MiniMax/Tencent model implementations. WorldMirror's pinned official source is fetched only by the explicit setup command; its own license and notice remain with that local download. / 配布物にモデル・LoRA重みやMiniMax/Tencentの公式モデル実装は含みません。WorldMirrorの公式コードは明示的な導入コマンドで別途取得し、取得先にも元のライセンスとNoticeを保持します。

Use each model and external implementation under its upstream terms, including applicable noncommercial and territorial restrictions. This independent project does not imply affiliation or endorsement. / 各モデル・外部実装は非商用・地域条件を含む配布元の条件に従って利用してください。本作は独立したプロジェクトで、提携・公認を示すものではありません。
