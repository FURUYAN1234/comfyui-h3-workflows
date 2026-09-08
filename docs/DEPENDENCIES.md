# Dependency installation / 依存ノードの導入

These steps install one original helper and four pinned external projects. Run them only in the ComfyUI environment you intend to use. They download source code, not model weights. / 自作ノード1個と外部4パッケージを導入します。使用するComfyUIの場所を確認してから進めてください。モデル重みはこの手順では取得しません。

## Before starting / 作業前

1. Install [Git](https://git-scm.com/downloads), then open a new terminal and run `git --version`. / Gitを導入し、新しく開いた端末でバージョンが表示されることを確認します。
2. Extract this package into a separate folder. Keep `patches/` alongside `dependencies.lock.json`. / 配布ZIPを別フォルダーへ展開し、パッチと固定版一覧を同じ階層のまま保管します。
3. Stop ComfyUI yourself before replacing nodes. If a listed node already exists, preserve it outside `custom_nodes/` first. These commands intentionally refuse to overwrite it. / 差し替え前にComfyUIを終了してください。同名フォルダーがある場合は、まず`custom_nodes/`の外へ退避します。以下の手順は既存フォルダーを上書きしません。

The example paths below are placeholders. Windows Portable's application directory is usually `ComfyUI_windows_portable/ComfyUI`, not the Portable root. In WSL use Linux paths to the ComfyUI running inside WSL. / 以下のパスは例です。Portable版では一段内側の`ComfyUI`を指定します。WSLで動くComfyUIには、WSLから見えるパスを使います。

## Windows PowerShell / Windowsの手順

Open PowerShell. Replace the first two paths, then run the entire block. An error stops the block; do not continue by skipping the failed dependency. / PowerShellを開き、先頭2行を自分の場所に直してからブロック全体を実行します。エラーになったら、その原因を確認してから再開してください。

```powershell
$h3Package = (Resolve-Path 'C:\path\to\ComfyUI_H3_Workflows').Path
$h3Comfy = (Resolve-Path 'C:\path\to\ComfyUI_windows_portable\ComfyUI').Path
$ErrorActionPreference = 'Stop'
if (!(Test-Path "$h3Comfy\main.py")) { throw 'Select the ComfyUI folder containing main.py' }
$h3Nodes = Join-Path $h3Comfy 'custom_nodes'
$h3Dependencies = Get-Content "$h3Package\dependencies.lock.json" -Raw | ConvertFrom-Json
$h3Names = @('comfyui-h3-standard-prompt') + @($h3Dependencies.name)
foreach ($h3Name in $h3Names) {
    if (Test-Path (Join-Path $h3Nodes $h3Name)) { throw "Preserve existing node outside custom_nodes first: $h3Name" }
}
Copy-Item -LiteralPath "$h3Package\custom_nodes\comfyui-h3-standard-prompt" -Destination $h3Nodes -Recurse
foreach ($h3Dependency in $h3Dependencies) {
    $h3Target = Join-Path $h3Nodes $h3Dependency.name
    git clone --config core.autocrlf=false $h3Dependency.repository $h3Target
    if ($LASTEXITCODE -ne 0) { throw 'Clone failed' }
    git -C $h3Target checkout --detach $h3Dependency.commit
    if ($LASTEXITCODE -ne 0) { throw 'Pinned revision unavailable' }
    $h3Patch = Join-Path $h3Package $h3Dependency.patch
    git -C $h3Target apply --check $h3Patch
    if ($LASTEXITCODE -ne 0) { throw 'Patch check failed; do not force it' }
    git -C $h3Target apply $h3Patch
    if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }
}
```

Then run the read-only content check using Portable's Python. / 次にPortable版自身のPythonで配置内容を検査します。

```powershell
& "$h3Comfy\..\python_embeded\python.exe" "$h3Package\verify_dependencies.py" $h3Comfy
& "$h3Comfy\..\python_embeded\python.exe" -m pip install -r "$h3Package\requirements.txt"
```

`Dependency content checks passed` confirms the listed source files match. It does not confirm GPU compatibility. / 合格表示は指定ソースの一致を意味し、GPUでの実行成功とは別です。

## Linux / WSL

Run this block in Bash, replacing both paths. It runs in a subshell so an error exits the setup block without closing your terminal. / Bashで先頭2行を変更して実行します。サブシェル内で処理するため、エラー時はこのブロックだけが終了します。

```bash
(
set -eu
h3_package='/path/to/ComfyUI_H3_Workflows'
h3_comfy='/path/to/ComfyUI'
test -f "$h3_comfy/main.py"
for name in comfyui-h3-standard-prompt ComfyUI-Custom-Scripts ComfyUI-H3-AudioRefine ComfyUI-MiniMax-H3-Long-Video ComfyUI-PlagueKind-Nodes; do
  if test -e "$h3_comfy/custom_nodes/$name"; then
    printf 'Preserve existing node outside custom_nodes first: %s\n' "$name"
    exit 1
  fi
done
cp -R "$h3_package/custom_nodes/comfyui-h3-standard-prompt" "$h3_comfy/custom_nodes/"
while read -r name owner revision; do
  target="$h3_comfy/custom_nodes/$name"
  git clone --config core.autocrlf=false "https://github.com/$owner/$name" "$target"
  git -C "$target" checkout --detach "$revision"
  git -C "$target" apply --check "$h3_package/patches/$name.patch"
  git -C "$target" apply "$h3_package/patches/$name.patch"
done <<'H3_DEPENDENCIES'
ComfyUI-Custom-Scripts pythongosssss 609f3afaa74b2f88ef9ce8d939626065e3247469
ComfyUI-H3-AudioRefine Adudeguyman fd5a8dfe8b4c636b1877abd07d11ebc985eddbed
ComfyUI-MiniMax-H3-Long-Video palealloy2999-prog f3fc996068a9319ace1d99d7c6ef54747a4018d2
ComfyUI-PlagueKind-Nodes PlagueKind 59f54d359bbabff8bb813b1e3e381dd29843e720
H3_DEPENDENCIES
"$h3_comfy/.venv/bin/python" "$h3_package/verify_dependencies.py" "$h3_comfy"
"$h3_comfy/.venv/bin/python" -m pip install -r "$h3_package/requirements.txt"
)
```

If your virtual environment has a different name, replace `.venv/bin/python` with the interpreter used to launch ComfyUI. Do not install libraries into an unrelated system Python. / 仮想環境名が異なる場合は、ComfyUIの起動に使うPythonへ変更してください。

## Expected layout / 完成時の配置

```text
ComfyUI/
  main.py
  custom_nodes/
    comfyui-h3-standard-prompt/__init__.py
    ComfyUI-Custom-Scripts/__init__.py
    ComfyUI-H3-AudioRefine/__init__.py
    ComfyUI-MiniMax-H3-Long-Video/__init__.py
    ComfyUI-PlagueKind-Nodes/__init__.py
```

There must not be a second identically named folder between the package folder and `__init__.py`. / パッケージ名のフォルダーを二重にしないでください。

## Why patches are required / パッチが必要な理由

The supplied source includes changes beyond the original prompt helper. In particular, the workflow expects long-video audio-refinement inputs and planning/saving behavior not provided by an unpatched dependency. Each patch is a source diff against the exact commit in `dependencies.lock.json`. Keep the corresponding license and change notice with it. / 元の配布構成には、自作プロンプトノード以外にも互換修正が含まれます。特に長尺生成の音声再精錬入力、実行計画、保存処理は無修正の上流版だけではそろいません。差分の元コミットと対象ライセンスを固定版一覧に記録しています。

The reconstruction check covers the 92 third-party files present in the original supplied package, normalizing CRLF to LF. Upstream checkouts may contain additional files; this check is not a complete runtime test of those files. / 復元検査は元配布物にあった第三者ファイル92個を対象とし、改行をLFへそろえて比較します。上流にはそれ以外のファイルもあり、全機能の実行検証を意味しません。

## Restart, updates and failures / 再起動・更新・エラー

- Finish the [model setup](MODELS.md) and CUDA/Triton checks in the [README](../README.md), restart ComfyUI, and import a workflow. / モデル配置とCUDA/Tritonの確認後にComfyUIを再起動し、JSONを読み込みます。
- A detached HEAD is expected: it fixes the dependency version. Do not blindly use Manager's Update All or `git pull` on these patched folders. / detached HEAD表示は固定版を使っているためです。一括更新すると互換修正が失われる可能性があります。
- If a patch fails, confirm the exact commit and that no earlier patch or local change is present. Preserve the failed folder outside `custom_nodes/` before starting clean. Do not force-apply or reset someone else's work. / 失敗時はコミットと既存変更を確認し、強制適用や既存作業の消去を行わないでください。
- An upstream license, model access restriction, missing revision, CUDA error or missing package is not repaired by ignoring it. Save the error text without tokens and consult that project's documentation. / 取得制限や依存エラーは無視せず、秘密情報を除いたエラー内容から原因を確認します。

[Return to README / READMEへ戻る](../README.md)
