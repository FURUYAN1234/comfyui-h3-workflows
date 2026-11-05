import assert from 'node:assert/strict';
import test from 'node:test';
import {ensureSceneSplatQuality} from '../web/scene-splat-quality.mjs';
const vector=value=>({value,copy(other){this.value=other.value;}});
class Splat {
 constructor(options={}){this.options=options;this.extSplats=!!options.extSplats;this.splats={};this.isInitialized=true;this.position=vector(1);this.quaternion=vector(2);this.scale=vector(3);this.visible=true;this.renderOrder=4;this.initialized=options.url==='broken'?Promise.reject(Error('load failed')):Promise.resolve();}
 dispose(){this.disposed=true;}
}
class Renderer {constructor(options={}){Object.assign(this,options);}dispose(){this.disposed=true;}}
function setup(){const old=new Splat(),children=[old],model={children,add(x){children.push(x);},remove(x){children.splice(children.indexOf(x),1);}};const renderer=new Renderer({onDirty:()=>{}}),scene={add(){},remove(){}};const manager={sparkRenderer:renderer,scene},models={currentModel:model,originalModel:old};return {old,renderer,manager,models,view:{renderer:{},getSceneManager:()=>manager,getModelManager:()=>models}};}
test('full precision applies to source and accumulator without moving model; opacity matches PNG',async()=>{
 const s=setup();await ensureSceneSplatQuality(s.view,'subject.ply');const splat=s.models.currentModel.children[0],renderer=s.manager.sparkRenderer;
 assert.ok(splat.extSplats);assert.equal(splat.options.url,'subject.ply');assert.equal(splat.position.value,1);assert.equal(splat.quaternion.value,2);assert.equal(splat.scale.value,3);assert.equal(s.models.originalModel,splat);assert.ok(s.old.disposed);assert.ok(s.renderer.disposed);
 assert.ok(renderer.accumExtSplats);assert.equal(renderer.preBlurAmount,.3);assert.equal(renderer.blurAmount,0);assert.equal(renderer.onDirty,s.renderer.onDirty);
 await ensureSceneSplatQuality(s.view,'subject.ply');assert.equal(s.models.currentModel.children[0],splat);assert.equal(s.manager.sparkRenderer,renderer);
});
test('failed full precision load retains original model and renderer',async()=>{
 const s=setup();await assert.rejects(ensureSceneSplatQuality(s.view,'broken'),/load failed/);assert.equal(s.models.currentModel.children[0],s.old);assert.equal(s.models.originalModel,s.old);assert.equal(s.manager.sparkRenderer,s.renderer);assert.ok(!s.old.disposed);assert.ok(!s.renderer.disposed);
});
