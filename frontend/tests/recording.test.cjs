const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),ts=require('typescript');
const code=ts.transpileModule(fs.readFileSync(require('path').join(__dirname,'../src/recording.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
(async()=>{
  let active, uploaded, calls=0;
  class Recorder {
    static isTypeSupported(type){return type==='video/webm';}
    constructor(){active=this;this.state='inactive';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable({data:new Blob(['final-chunk'])});this.onstop();}
  }
  const exports={};vm.runInNewContext(code,{exports,MediaRecorder:Recorder,Blob,FormData,setTimeout,clearTimeout,require:()=>({logStep(){},loggedFetch:async(url,opts)=>{calls++;uploaded=opts.body;return {ok:true,json:async()=>({video_ref:'test.webm'})};}})});
  const recording=exports.recordCamera({},'http://localhost','test');
  active.ondataavailable({data:new Blob(['first-chunk'])});
  assert.equal(calls,0);await recording.save();assert.equal(calls,1);
  assert.equal(await uploaded.get('video').text(),'first-chunkfinal-chunk','Final dataavailable event must be preserved before upload');
  const absent={};vm.runInNewContext(code,{exports:absent,require:()=>({})});assert.equal(absent.recordCamera({},'',''),null);
  console.log('PASS: continuous video retains the final recorder chunk; unsupported recorder degrades to images');
})().catch(e=>{console.error(e);process.exitCode=1;});
