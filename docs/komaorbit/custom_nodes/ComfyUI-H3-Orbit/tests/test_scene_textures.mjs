import assert from 'node:assert/strict';
import test from 'node:test';
import {safeCameraElevation,bindSceneWheel,sceneOrbitFrame,configureSceneNavigation} from '../web/scene-textures.mjs';
test('physical low orbit stays above floor at all supported angles',()=>{
 for(const angle of [-30,-15,0,30]){const e=angle*Math.PI/180;const actual=safeCameraElevation(.6,.15,e);assert.ok(.6*Math.sin(actual)>-.15);assert.ok(actual>=e);if(angle>=0)assert.equal(actual,e);}
 assert.throws(()=>safeCameraElevation(.6,-.15,0));
});

test('wheel on the visible viewer changes saved camera zoom and detaches cleanly',()=>{
 const visible=new EventTarget(),offscreen=new EventTarget();let renders=0;const camera={zoom:1,updateProjectionMatrix(){}};
 const view={domElement:visible,renderer:{domElement:offscreen},getRenderCamera:()=>camera,forceRender:()=>renders++};
 const dispose=bindSceneWheel(view);const wheel=delta=>{const e=new Event('wheel',{cancelable:true});Object.assign(e,{deltaY:delta,deltaMode:0});return e;};
 visible.dispatchEvent(wheel(-300));assert.ok(camera.zoom>1);const magnified=camera.zoom;visible.dispatchEvent(wheel(600));assert.ok(camera.zoom<1);assert.equal(renders,2);
 offscreen.dispatchEvent(wheel(-1000));assert.equal(renders,2);dispose();visible.dispatchEvent(wheel(-300));assert.equal(renders,2);assert.ok(camera.zoom<magnified);
});

test('panning preserves target and direction while keeping the camera above the floor',()=>{
 const target={x:.08,y:.06,z:0},position={x:.08,y:.06,z:.6};
 const result=sceneOrbitFrame(position,target,.6,.15,1.5);
 assert.deepEqual(result.target,target);assert.deepEqual(result.position,position);assert.equal(result.elevation,0);
 const low=sceneOrbitFrame({x:0,y:-.6,z:.6},{x:0,y:-.6,z:0},.6,.15,2);
 assert.ok(low.position.y>=-.15*.86-1e-8);assert.ok(Number.isFinite(low.elevation));
 const far=sceneOrbitFrame({x:10,y:0,z:.6},{x:10,y:0,z:0},.6,.15,1.5);
 assert.ok(Math.hypot(...Object.values(far.position))<1.5);
 assert.throws(()=>sceneOrbitFrame({x:NaN,y:0,z:1},target,.6,.15,1.5));
});
test('native controls bind right drag to pan and restore settings on detach',()=>{
 const element=new EventTarget();let keyTarget=null;
 const controls={enableDamping:true,enableZoom:true,enablePan:false,screenSpacePanning:false,mouseButtons:{LEFT:0,RIGHT:2},listenToKeyEvents(el){keyTarget=el;},stopListenToKeyEvents(){keyTarget=null;}};
 const cleanup=configureSceneNavigation({domElement:element},controls);
 assert.equal(controls.enableDamping,false);assert.equal(controls.enablePan,true);assert.equal(controls.enableZoom,false);assert.equal(controls.screenSpacePanning,true);assert.equal(controls.mouseButtons.RIGHT,2);assert.equal(keyTarget,element);
 cleanup();assert.equal(controls.enableDamping,true);assert.equal(controls.enablePan,false);assert.equal(controls.enableZoom,true);assert.equal(controls.screenSpacePanning,false);assert.equal(keyTarget,null);
});

test('reset recovers a previously fitted and translated model as well as camera',async()=>{
 const {resetSceneView}=await import('../web/scene-textures.mjs');
 const identity={position:{x:0,y:0,z:0},quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}};
 const base={position:{x:.01,y:.03,z:.43},target:{x:0,y:0,z:0},zoom:1};
 let model={position:{x:-.47,y:6.87,z:-.22},quaternion:{x:.2,y:0,z:0,w:.98},scale:{x:83.46,y:83.46,z:83.46}};
 let camera={position:{x:3,y:4,z:5},target:{x:2,y:3,z:4},zoom:4},updated=0,rendered=0,synced=0;const events=[];
 const view={applyModelTransform(value){model=value;},setCameraState(value){camera=value;},getCameraState:()=>camera,getRenderCamera:()=>camera,getSceneManager:()=>({async whenSplatsSorted(){}}),getControlsManager:()=>({controls:{update(){updated++;}}}),forceRender(){rendered++;},getEventManager:()=>({emitEvent:(...args)=>events.push(args)})};
 await resetSceneView(view,{base_camera:base},{syncLoad3dConfig(){synced++;}});
 assert.deepEqual(model,identity);assert.deepEqual(camera,base);assert.notEqual(camera,base);assert.equal(updated,1);assert.equal(rendered,3);assert.equal(synced,1);assert.equal(events[0][0],'cameraChanged');
 model.scale.x=17;camera.target.y=8;await resetSceneView(view,{base_camera:base});assert.equal(model.scale.x,1);assert.equal(camera.target.y,0);assert.equal(base.target.y,0);
});

test('scene drawing refreshes after asynchronous splat sorting completes',async()=>{
 const {settleSceneView}=await import('../web/scene-textures.mjs');
 const calls=[];let complete;const sorted=new Promise(resolve=>complete=resolve);const camera={zoom:1};
 const view={forceRender(){calls.push('draw');},getRenderCamera:()=>camera,getSceneManager:()=>({async whenSplatsSorted(value){assert.equal(value,camera);calls.push('sort');await sorted;calls.push('sorted');}})};
 const pending=settleSceneView(view);assert.deepEqual(calls,['draw','sort']);complete();await pending;assert.deepEqual(calls,['draw','sort','sorted','draw']);
});
