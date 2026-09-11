const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'custom_nodes/comfyui-h3-standard-prompt/web/lm_studio_execution_status.js'),'utf8').replace(/^import .*;$/gm,'');
let extension,tick;const events={},elements=[];
const n={type:'H3StandardPrompt',widgets:[{name:'japanese_instruction',value:'dance'}],setDirtyCanvas(){}};
const ctx=vm.createContext({Date,console,api:{queuePrompt:async()=>({}),addEventListener:(k,f)=>events[k]=f},app:{graph:{_nodes:[n],getNodeById:()=>n},registerExtension:e=>extension=e},document:{createElement:()=>({dataset:{},setAttribute(){}}),head:{appendChild(){}},body:{appendChild:e=>elements.push(e)},addEventListener(){}},window:{setTimeout:()=>1,clearTimeout(){},setInterval:f=>tick=f}});
vm.runInContext(source,ctx);extension.setup();
(async()=>{
for(const mode of ['I2VA','T2VA','Ref2VA (R2V)']){
await ctx.api.queuePrompt(0,{output:{7:{class_type:n.type,inputs:{japanese_instruction:'dance',mode}}}});
assert.equal(elements[0].dataset.state,'queued');
events.execution_start();events.executing({detail:'7'});tick();
assert.equal(elements[0].dataset.state,'running');assert.match(elements[0].textContent,/CPU/);
events.executed({detail:{node:'7'}});assert.equal(elements[0].dataset.state,'done');
}
await ctx.api.queuePrompt(0,{output:{7:{class_type:n.type,inputs:{japanese_instruction:' '}}}});
assert.equal(elements[0].dataset.visible,'false');
n.widgets[0].value=' ';assert.equal(vm.runInContext('isLmStudioNode(app.graph._nodes[0])',ctx),false);
n.inputs=[{name:'japanese_instruction',link:5}];assert.equal(vm.runInContext('isLmStudioNode(app.graph._nodes[0])',ctx),true);
await ctx.api.queuePrompt(0,{output:{7:{class_type:n.type,inputs:{japanese_instruction:['5',0]}}}});
events.executing({detail:'7'});events.execution_error({detail:{node_id:'7'}});
assert.equal(elements[0].dataset.state,'error');
n.type='UnrelatedNode';assert.equal(vm.runInContext('isLmStudioNode(app.graph._nodes[0])',ctx),false);
console.log('PASS: 3 modes queued/running/elapsed/done; direct excluded; linked input; error; unrelated excluded');
})().catch(e=>{console.error(e);process.exitCode=1});