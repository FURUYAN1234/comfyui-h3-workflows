import test from 'node:test';
import assert from 'node:assert/strict';
import { backgroundFov } from '../web/scene-layers.mjs';

test('native background camera matches saved target scale at each zoom',()=>{
    for(const zoom of [12,24,48]) {
        const camera={isOrthographicCamera:true,position:{x:0,y:-3,z:4},top:5,bottom:-5,zoom};
        const bg=backgroundFov(camera,{x:0,y:0,z:0});
        assert.ok(Math.abs(Math.tan(bg.fov*Math.PI/360)-1/zoom)<1e-12);
        assert.equal(bg.zoom,1);
        assert.equal(camera.zoom,zoom);
    }
});

test('invalid projection fails and existing perspective keeps its zoom',()=>{
    assert.deepEqual(backgroundFov({position:{x:0,y:0,z:1},fov:35,zoom:2},{x:0,y:0,z:0}),{fov:35,zoom:2});
    assert.throws(()=>backgroundFov({isOrthographicCamera:true,position:{x:0,y:0,z:0},top:1,bottom:-1,zoom:1},{x:0,y:0,z:0}));
});

 test('native foot anchor aligns with perspective ground without scaling body',async()=>{
    const module=await import('../web/scene-layers.mjs');
    assert.equal(typeof module.anchorProjectionShift,'function');
    for (const depth of [.6,1,1.4]) {
        const shift=module.anchorProjectionShift(.2,.1,depth,1,4,5);
        assert.ok(Math.abs(.2*4+shift.x-.2*4/depth)<1e-10);
        assert.ok(Math.abs(.1*5+shift.y-.1*5/depth)<1e-10);
    }
 });
