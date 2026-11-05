import { installSceneViewCopy, createSceneViewContext } from "./scene-view-lifecycle.mjs?rev=20261104-gray1";
import { applySceneSize } from "./scene-refresh.mjs";
import { attachTexturedScene } from "./scene-textures.mjs?rev=20261104-gray1";
// Keep the rigid subject orthographic while the environment retains perspective.
export function backgroundFov(camera, target) {
    const radius = Math.hypot(...['x','y','z'].map(k => camera.position[k]-target[k]));
    if (!camera.isOrthographicCamera) return { fov:camera.fov, zoom:camera.zoom };
    const span = camera.top-camera.bottom;
    if (![radius,span,camera.zoom].every(v=>Number.isFinite(v)&&v>0)) throw new Error('背景カメラの表示範囲が不正です。');
    return { fov:Math.atan(span/(2*radius*camera.zoom))*360/Math.PI, zoom:1 };
}

export function anchorProjectionShift(x,y,depth,radius,sx,sy) {
    if (![x,y,depth,radius,sx,sy].every(Number.isFinite) || depth<=0 || radius<=0) throw new Error('足元の投影位置が不正です。');
    const factor=radius/depth-1;
    return {x:sx*x*factor,y:sy*y*factor};
}

export async function attachSceneLayers(node, api, status) {
    node._h3LayersDispose?.();
    let cancelled=false, disposeScene=()=>{};
    node._h3LayersDispose=()=>{cancelled=true;disposeScene();};
    const modelFile=String(node.properties?.['Last Time Model File']||'').replaceAll('\\','/');
    const subfolder=modelFile.slice(0,modelFile.lastIndexOf('/'));
    if (!/^H3_ORB360\/[A-Za-z0-9_-]+$/.test(subfolder)) return;
    try {
        const response=await api.fetchApi(`/h3-orbit/scene-layers?scene_id=${encodeURIComponent(subfolder)}`);
        const info=await response.json();
        if (cancelled) return;
        if (!response.ok) throw new Error(info.error);
        applySceneSize(node,info);
        const service=(await import(info.module))[info.export]();
        let view;
        for (let i=0;i<100&&!cancelled;i++) {
            view=await service.getLoad3dAsync(node);
            if (view) break;
            await new Promise(resolve=>setTimeout(resolve,200));
        }
        if (cancelled) return;
        if (!view) throw new Error('3Dプレビューの準備が完了しませんでした。');
        await view.whenLoadIdle();
        if (cancelled) return;
        if (info.width && info.height) view.setTargetSize(info.width,info.height);
        const state=node.properties['Camera Config']?.state || view.getCameraState();
        const transform=view.getModelInfo();
        const url=filename=>api.apiURL(`/view?${new URLSearchParams({filename,subfolder,type:'output'})}`);
        if (await view.loadModel(url(info.subject))!=='loaded') throw new Error('人物3Dを読み込めませんでした。');
        if (cancelled) return;
        if (transform) view.applyModelTransform(transform);
        view.setCameraState(state);
        if(info.projection==='unified_textured_scene'){
            installSceneViewCopy(service);
            const context=createSceneViewContext(v=>attachTexturedScene(v,info,node,api,status));
            disposeScene=()=>context.dispose();
            await context.attach(view);if(cancelled)disposeScene();return;
        }
        const controls=view.getControlsManager().controls;
        const previousMaxPolar=controls.maxPolarAngle;
        controls.maxPolarAngle=(90-info.minimum_elevation)*Math.PI/180;
        controls.update();
        const manager=view.getSceneManager();
        const model=view.getModelManager().currentModel;
        const splat=model?.children?.[0];
        if (!splat?.initialized || !manager.sparkRenderer) throw new Error('このComfyUI版の3D描画に対応していません。');
        const bgCamera=view.getCameraManager().perspectiveCamera.clone();
        const bgManager=new manager.constructor(view.rendererView,()=>bgCamera,()=>view.getControlsManager().controls,{emitEvent(){}});
        bgManager.gridHelper.visible=false;
        const bgModel=new model.constructor();
        const bgSplat=new splat.constructor({url:url(info.background)});
        await bgSplat.initialized;
        bgSplat.quaternion.copy(splat.quaternion);
        bgModel.add(bgSplat);bgManager.scene.add(bgModel);
        const originalBackground=manager.renderBackground;
        const render=()=>{
            const camera=view.getRenderCamera();
            camera.updateProjectionMatrix();camera.updateMatrixWorld(true);
            bgCamera.position.copy(camera.position);bgCamera.quaternion.copy(camera.quaternion);
            Object.assign(bgCamera,backgroundFov(camera,view.getControlsManager().controls.target));
            bgCamera.aspect=view.targetWidth/view.targetHeight;
            bgCamera.near=camera.near;bgCamera.far=camera.far;
            bgCamera.view=camera.view?{...camera.view}:null;bgCamera.updateProjectionMatrix();
            bgModel.position.copy(model.position);bgModel.quaternion.copy(model.quaternion);bgModel.scale.copy(model.scale);
            if (camera.isOrthographicCamera && info.contact_anchor) {
                const [x,y,z]=info.contact_anchor;
                const anchor=model.position.clone().set(x,-y,-z);
                model.updateMatrixWorld(true);model.localToWorld(anchor);
                anchor.applyMatrix4(camera.matrixWorldInverse);
                const radius=camera.position.distanceTo(controls.target);
                const shift=anchorProjectionShift(anchor.x,anchor.y,-anchor.z,radius,camera.projectionMatrix.elements[0],camera.projectionMatrix.elements[5]);
                camera.projectionMatrix.elements[12]+=shift.x;
                camera.projectionMatrix.elements[13]+=shift.y;
                camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
            }
            originalBackground.call(manager);
            view.renderer.render(bgManager.scene,bgCamera);
        };
        manager.renderBackground=render;
        disposeScene=()=>{
            controls.maxPolarAngle=previousMaxPolar;
            if(manager.renderBackground===render) manager.renderBackground=originalBackground;
            bgSplat.dispose();bgManager.sparkRenderer.dispose();bgManager.dispose();
        };
        if(cancelled){disposeScene();return;}
        view.forceRender();
        await bgManager.whenSplatsSorted(bgCamera);
        view.forceRender();
        status.textContent='人物の体形固定・背景付き。画像内ドラッグで回転、ホイールで構図を拡大縮小';
    } catch(error) {
        if (!cancelled) status.textContent=`背景の表示準備に失敗: ${error.message}`;
        console.error('H3 scene background',error);
    }
}
