import test from 'node:test';
import assert from 'node:assert/strict';
import { installSceneRefresh, applySceneSize } from '../web/scene-refresh.mjs';

test('native wrapper updates the model before layers read its path', async () => {
 const node={properties:{file:'old'}};const seen=[];
 installSceneRefresh(node,()=>seen.push(node.properties.file));
 const previous=node.onExecuted;
 node.onExecuted=function(message){previous.call(this,message);this.properties.file=message.file;};
 node.onExecuted({file:'new'});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(seen,['new']);
});
test('also waits when native execution is asynchronous', async()=>{
 const node={properties:{file:'old'},async onExecuted(){await Promise.resolve();this.properties.file='new';return 42;}};
 const seen=[];installSceneRefresh(node,()=>seen.push(node.properties.file));
 assert.equal(await node.onExecuted({}),42);
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(seen,['new']);
});
test('a burst of outputs refreshes only the latest model',async()=>{
 const node={};const seen=[];installSceneRefresh(node,()=>seen.push(node.file));
 node.file='first';node.onExecuted({});node.file='last';node.onExecuted({});
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(seen,['last']);
});

test('new image dimensions replace old viewport and saved dimensions',()=>{
 const applied=[];const node={properties:{h3_scene_size:{width:1672,height:941}},widgets:['width','height'].map(name=>({name,value:0,callback:v=>applied.push([name,v])}))};
 applySceneSize(node,{width:746,height:527});
 assert.deepEqual(node.widgets.map(w=>w.value),[746,527]);
 assert.deepEqual(node.properties.h3_scene_size,{width:746,height:527});
 assert.deepEqual(applied,[['width',746],['height',527]]);
 applySceneSize(node,{width:NaN,height:0});
 assert.deepEqual(node.widgets.map(w=>w.value),[746,527]);
});
