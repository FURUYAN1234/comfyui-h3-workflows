import { ensureSceneSplatQuality } from "./scene-splat-quality.mjs?rev=20261104-gray1";
// A single physical camera for the rigid subject and its textured environment.
export function safeCameraElevation(baseRadius, floorHeight, elevation) {
    if (![baseRadius,floorHeight,elevation].every(Number.isFinite)||baseRadius<=0||floorHeight<=0) throw new Error('床とカメラの位置が不正です。');
    return Math.max(elevation, Math.asin(-Math.min(1,floorHeight*.86/baseRadius)));
}

export function sceneOrbitFrame(position,target,baseRadius,floorY,environmentRadius) {
    const keys=['x','y','z'];
    if (![...keys.map(k=>position[k]),...keys.map(k=>target[k]),baseRadius,floorY,environmentRadius].every(Number.isFinite)||baseRadius<=0||floorY<=0||environmentRadius<=baseRadius) throw new Error('カメラと床の位置が不正です。');
    const delta=keys.map(k=>position[k]-target[k]);const length=Math.hypot(...delta);
    if(length<1e-7)throw new Error('カメラと注視点が重なっています。');
    const nextTarget={...target};const limit=environmentRadius*.95-baseRadius;const distance=Math.hypot(...keys.map(k=>nextTarget[k]));
    if(distance>limit)for(const k of keys)nextTarget[k]*=Math.max(0,limit)/distance;
    // A pan changes both the eye and target. Keep that target; do not snap it back.
    const minimum=Math.asin(Math.max(-1,Math.min(1,(-floorY*.86-nextTarget.y)/baseRadius)));
    const elevation=Math.max(minimum,Math.asin(Math.max(-1,Math.min(1,delta[1]/length))));
    const yaw=Math.atan2(delta[0],delta[2]);
    return {target:nextTarget,elevation,minimum,
        position:{x:nextTarget.x+baseRadius*Math.cos(elevation)*Math.sin(yaw),
                  y:nextTarget.y+baseRadius*Math.sin(elevation),
                  z:nextTarget.z+baseRadius*Math.cos(elevation)*Math.cos(yaw)}};
}

export function configureSceneNavigation(view,controls) {
    const previous={damping:controls.enableDamping,zoom:controls.enableZoom,pan:controls.enablePan,screen:controls.screenSpacePanning,buttons:{...controls.mouseButtons}};
    controls.enableDamping=false;controls.enableZoom=false;controls.enablePan=true;controls.screenSpacePanning=true;
    Object.assign(controls.mouseButtons,{LEFT:0,RIGHT:2}); // THREE.MOUSE.ROTATE / PAN
    const element=view.domElement;const previousTabIndex=element.tabIndex;element.tabIndex=0;
    const focus=()=>queueMicrotask(()=>element.focus?.({preventScroll:true}));
    element.addEventListener('pointerdown',focus);
    // Focused arrow keys use the same native pan path and do not move ComfyUI nodes.
    const key=event=>{if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.code||event.key)){event.preventDefault();event.stopPropagation();}};
    element.addEventListener('keydown',key);
    controls.listenToKeyEvents?.(element);
    return ()=>{element.removeEventListener('pointerdown',focus);element.removeEventListener('keydown',key);element.tabIndex=previousTabIndex;controls.stopListenToKeyEvents?.();controls.enableDamping=previous.damping;controls.enableZoom=previous.zoom;controls.enablePan=previous.pan;controls.screenSpacePanning=previous.screen;Object.assign(controls.mouseButtons,previous.buttons);};
}

export function bindSceneWheel(view) {
    // ComfyUI shares an offscreen WebGL renderer; input arrives on this view's canvas.
    const target=view.domElement;
    if(!target?.addEventListener)throw new Error('3D表示の操作領域が見つかりません。');
    const wheel=event=>{
        event.preventDefault();event.stopImmediatePropagation();
        const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?(target.clientHeight||600):1);
        if(!Number.isFinite(delta))return;
        const cam=view.getRenderCamera();cam.zoom=Math.min(4,Math.max(.5,cam.zoom*Math.exp(-delta*.001)));
        cam.updateProjectionMatrix();view.forceRender();view.getEventManager?.().emitEvent("cameraChanged",view.getCameraState());
    };
    target.addEventListener('wheel',wheel,{capture:true,passive:false});
    return ()=>target.removeEventListener('wheel',wheel,{capture:true});
}

const vertexShader=`varying vec2 vUv;void main(){vUv=position.xy*.5+.5;gl_Position=vec4(position,1.);}`;
const fragmentShader=`
precision highp float;
varying vec2 vUv;
uniform mat4 invProjection,cameraWorld,worldToModel;
uniform sampler2D panorama,ground;
uniform float radius,floorY,groundRadius;
uniform vec2 groundOrigin;
const float PI=3.141592653589793;
void main(){
 vec4 v=invProjection*vec4(vUv*2.-1.,1.,1.);vec3 dc=normalize(v.xyz/v.w);
 vec3 eye=(worldToModel*cameraWorld*vec4(0.,0.,0.,1.)).xyz;
 vec3 ray=(worldToModel*cameraWorld*vec4(dc,0.)).xyz;
 eye*=vec3(1.,-1.,-1.);ray*=vec3(1.,-1.,-1.);
 float a=dot(ray,ray),b=dot(ray,eye),c=dot(eye,eye)-radius*radius;
 float ts=(-b+sqrt(max(b*b-a*c,0.)))/a;vec3 p=eye+ray*ts;
 vec2 uv=vec2(atan(p.x,p.z)/(2.*PI)+.5,1.-(asin(clamp(p.y/length(p),-1.,1.))/PI+.5));
 vec3 col=texture2D(panorama,uv).rgb;
 float dy=abs(ray.y)>1.e-9?ray.y:1.e-9;float tg=(floorY-eye.y)/dy;vec3 gp=eye+ray*tg;
 vec2 guv=(gp.xz-groundOrigin+groundRadius)/(2.*groundRadius);
 float radial=max(abs(guv.x-.5),abs(guv.y-.5))*2.;float alpha=clamp((1.-radial)/.12,0.,1.);
 if(tg<=0.||tg>=ts||ray.y<=0.)alpha=0.;
 vec3 gc=texture2D(ground,vec2(clamp(guv.x,0.,1.),1.-clamp(guv.y,0.,1.))).rgb;
 gl_FragColor=vec4(mix(col,gc,alpha),1.);
 #include <colorspace_fragment>
}`;

// Old native Fit-to-view commands changed the model itself, sometimes by >80x.
// A recoverable home action must reset that placement as well as the camera.
export async function settleSceneView(view) {
    // Spark sorts splats asynchronously; a forced draw before completion can
    // leave an empty/stale subject on screen until the next mouse event.
    view.forceRender();
    await view.getSceneManager().whenSplatsSorted(view.getRenderCamera());
    view.forceRender();
}

export async function resetSceneView(view,info,node) {
    view.applyModelTransform({position:{x:0,y:0,z:0},quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}});
    view.setCameraState(structuredClone(info.base_camera));
    view.getControlsManager().controls.update();
    view.forceRender();
    view.getEventManager().emitEvent("cameraChanged",view.getCameraState());
    if(!view.isViewerMode)node?.syncLoad3dConfig?.();
    await settleSceneView(view);
}

export async function attachTexturedScene(view,info,node,api,status) {
    const exports=await import(info.three_module);const C=Object.fromEntries(Object.entries(info.three_exports).map(([key,value])=>[key,exports[value]]));
    for(const key of ['Scene','Mesh','ShaderMaterial','TextureLoader','Float32BufferAttribute'])if(!C[key])throw new Error(`3D描画の${key}が見つかりません。`);
    const env=info.texture_environment;
    const url=filename=>api.apiURL(`/view?${new URLSearchParams({filename,subfolder:info.subfolder,type:'output'})}`);
    await ensureSceneSplatQuality(view,url(info.subject));
    const loader=new C.TextureLoader();const textures=await Promise.all([loader.loadAsync(url(env.panorama)),loader.loadAsync(url(env.ground))]);
    for(const texture of textures){texture.colorSpace='srgb';texture.anisotropy=Math.min(8,view.renderer.capabilities.getMaxAnisotropy());}
    textures[0].wrapS=1000; // THREE.RepeatWrapping, only horizontal; poles remain clamped.
    const manager=view.getSceneManager();const model=view.getModelManager().currentModel;const controls=view.getControlsManager().controls;
    const oldDimensions=view.getDimensionsCallback;
    view.getDimensionsCallback=()=>({width:info.width,height:info.height});
    view.setTargetSize(info.width,info.height);
    const oldCenter=view.centerCameraOnModel,oldFit=view.fitToViewer;
    const reset=()=>resetSceneView(view,info,node);
    view.centerCameraOnModel=reset;
    view.fitToViewer=reset;
    if(!view.isViewerMode)node._h3ResetCamera=()=>view.fitToViewer();
    // Outline the exact output gate; outside remains visible but dimmed.
    const gate=document.createElement('div');gate.dataset.komaorbitSaveFrame='true';
    gate.style.cssText='position:absolute;pointer-events:none;box-sizing:border-box;border:1px solid #ffffffb0;box-shadow:0 0 0 4000px #0007;z-index:2';
    const parent=view.domElement.parentElement;
    const updateGate=()=>{const w=parent.clientWidth,h=parent.clientHeight,a=info.width/info.height;
        const fw=Math.min(w,h*a),fh=fw/a;
        Object.assign(gate.style,{left:`${(w-fw)/2}px`,top:`${(h-fh)/2}px`,width:`${fw}px`,height:`${fh}px`});};
    const gateClip=document.createElement('div');gateClip.style.cssText='position:absolute;inset:0;overflow:hidden;pointer-events:none;z-index:2';gateClip.appendChild(gate);parent.appendChild(gateClip);const resize=new ResizeObserver(updateGate);resize.observe(parent);updateGate();
    const oldRemove=view.remove;view.remove=function(){dispose();return oldRemove.call(this);};
    const previousMaxPolar=controls.maxPolarAngle;const unbindNavigation=configureSceneNavigation(view,controls);
    let camera=view.getRenderCamera();if(!camera.isPerspectiveCamera){view.setCameraState(info.base_camera);camera=view.getRenderCamera();}
    const base=info.base_camera;const baseRadius=Math.hypot(...['x','y','z'].map(k=>base.position[k]-base.target[k]));const tangent=Math.tan(base.fov*Math.PI/360);
    controls.maxPolarAngle=Math.PI/2-safeCameraElevation(baseRadius,env.floor+base.target.y,-Math.PI/6);
    const pre=()=>{
        const cam=view.getRenderCamera();if(!cam.isPerspectiveCamera)return;
        const frame=sceneOrbitFrame(cam.position,controls.target,baseRadius,env.floor,env.radius);
        const elevation=frame.elevation;
        controls.target.set(frame.target.x,frame.target.y,frame.target.z);
        cam.position.set(frame.position.x,frame.position.y,frame.position.z);
        controls.maxPolarAngle=Math.PI/2-Math.max(-Math.PI/6,frame.minimum);
        cam.lookAt(controls.target);
        const margin=1+.12*Math.max(0,Math.min(1,-Math.sin(elevation)/.5));cam.fov=Math.atan(tangent*margin)*360/Math.PI;
        cam.updateProjectionMatrix();cam.updateMatrixWorld(true);
    };
    const removePre=view.addPreRenderCallback(pre);
    const geometry=new manager.gridHelper.geometry.constructor();geometry.setAttribute('position',new C.Float32BufferAttribute([-1,-1,0,1,-1,0,1,1,0,-1,-1,0,1,1,0,-1,1,0],3));
    const uniforms={invProjection:{value:camera.projectionMatrixInverse.clone()},cameraWorld:{value:camera.matrixWorld.clone()},worldToModel:{value:model.matrixWorld.clone()},panorama:{value:textures[0]},ground:{value:textures[1]},radius:{value:env.radius},floorY:{value:env.floor},groundRadius:{value:env.ground_radius},groundOrigin:{value:env.ground_origin}};
    const material=new C.ShaderMaterial({uniforms,vertexShader,fragmentShader,depthTest:false,depthWrite:false,toneMapped:false});
    const mesh=new C.Mesh(geometry,material);mesh.frustumCulled=false;const scene=new C.Scene();scene.add(mesh);
    const original=manager.renderBackground;
    const render=()=>{
        const cam=view.getRenderCamera();cam.updateMatrixWorld(true);model.updateMatrixWorld(true);
        uniforms.invProjection.value.copy(cam.projectionMatrixInverse);uniforms.cameraWorld.value.copy(cam.matrixWorld);uniforms.worldToModel.value.copy(model.matrixWorld).invert();
        original.call(manager);view.renderer.render(scene,cam);
    };
    manager.renderBackground=render;
    const unbindWheel=bindSceneWheel(view);
    view.forceRender();status.textContent=`白枠内を保存 ${info.width} × ${info.height}（元画像と同じ縦横比）｜左：回転／右：パン／ホイール：ズーム`;
    let disposed=false;
    function dispose(){if(disposed)return;disposed=true;removePre();unbindWheel();unbindNavigation();resize.disconnect();gateClip.remove();
        view.remove=oldRemove;view.getDimensionsCallback=oldDimensions;view.centerCameraOnModel=oldCenter;view.fitToViewer=oldFit;
        controls.maxPolarAngle=previousMaxPolar;if(manager.renderBackground===render)manager.renderBackground=original;
        geometry.dispose();material.dispose();textures.forEach(t=>t.dispose());}
    await settleSceneView(view);
    return dispose;
}
