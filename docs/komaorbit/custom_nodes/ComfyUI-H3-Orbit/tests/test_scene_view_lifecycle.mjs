import test from 'node:test';
import assert from 'node:assert/strict';
import {installSceneViewCopy,createSceneViewContext} from '../web/scene-view-lifecycle.mjs';
test('native splat reload preserves placement, pan and zoom across viewer roundtrip',async()=>{
 const make=()=>({remove(){this.removed=true;},camera:{position:{x:1,y:2,z:3},target:{x:.1,y:.2,z:.3},zoom:2},model:{position:{x:0,y:0,z:0},quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1}},getCameraState(){return this.camera;},getModelInfo(){return this.model;},setCameraState(v){this.camera=v;},applyModelTransform(v){this.model=v;}});
 const node=make(),viewer=make();let installs=0,removals=0,copies=0;
 const service={async copyLoad3dState(a,b){copies++;b.model={position:{x:5,y:5,z:5}};b.camera={zoom:1};}};
 installSceneViewCopy(service);installSceneViewCopy(service);
 const context=createSceneViewContext(async()=>{installs++;return()=>removals++;});await context.attach(node);
 await service.copyLoad3dState(node,viewer);assert.deepEqual(viewer.model,node.model);assert.deepEqual(viewer.camera,node.camera);
 viewer.camera.target.x=.4;viewer.camera.zoom=1.5;
 await service.copyLoad3dState(viewer,node);assert.equal(node.camera.target.x,.4);assert.equal(node.camera.zoom,1.5);assert.equal(node.model.position.x,0);
 assert.equal(installs,3);assert.equal(removals,1);assert.equal(copies,2);viewer.remove();assert.equal(viewer.removed,true);assert.equal(removals,2);context.dispose();assert.equal(removals,3);
 const plain=make(),destination=make();await service.copyLoad3dState(plain,destination);assert.equal(destination.model.position.x,5);assert.equal(installs,3);
});
