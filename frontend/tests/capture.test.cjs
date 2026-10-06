const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const ts = require('typescript');
const source = fs.readFileSync(require('path').join(__dirname, '../src/App.tsx'), 'utf8').replace('import.meta.env.VITE_API_URL', 'undefined');
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}}).outputText;
function harness({voiceThrows = false, apiFails = false, secure = true, permissionFails = false, frameDeferred = null} = {}) {
  const slots = []; let cursor = 0; let stops = 0; let cameraCalls = 0; let fetchCalls = 0; let finishCalls = 0; let frameUploads = 0; let imageNumber = 0; const uploadedImages = []; const uploadedShelves = [];
  const effects = [];
  const video = {srcObject: null, readyState: 2, videoWidth: 640, videoHeight: 480, play: async () => {}};
  const stream = {getTracks: () => [{stop: () => stops++}]};
  const react = {
    useRef: value => { const i = cursor++; return slots[i] ??= {current: value}; },
    useState: value => { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], next => {slots[i] = typeof next === 'function' ? next(slots[i]) : next;}]; },
    useEffect: fn => {cursor++; effects.push(fn);}
  };
  const jsx = (type, props) => {if (type === 'video') props.ref.current = video; if(type === 'canvas') props.ref.current = {getContext: () => ({drawImage() {}}), toDataURL: () => 'frame', toBlob: fn => fn({imageNumber: ++imageNumber})}; return {type, props};};
  const exports = {};
  const context = {FormData: class {append(key, value) {if (key === 'image') this.image = value; if (key === 'shelf') this.shelf = value;}}, exports, require: name => name === 'react' ? react : name === './recording' ? {recordCamera: () => null} : name === './ReviewTools' ? {ReviewTools: () => null} : name === './logger' ? {logStep() {}, errorDetails: error => ({error: String(error)}), loggedFetch: (...args) => context.fetch(...args)} : {jsx, jsxs: jsx}, window: {isSecureContext: secure, SpeechRecognition: class {start() {if(voiceThrows) throw new Error('voice denied');} stop() {} }}, navigator: {mediaDevices: {getUserMedia: async () => {cameraCalls++; if(permissionFails) throw new Error('Camera permission denied'); return stream;}}}, fetch: async (url, options) => {fetchCalls++; if(url.endsWith('/frames')) {frameUploads++; uploadedImages.push(options.body.image.imageNumber); uploadedShelves.push(options.body.shelf);} if(url.endsWith('/frames') && frameDeferred) return frameDeferred; if(url.endsWith('/finish')) finishCalls++; return {ok: !apiFails, status: 503, json: async () => apiFails ? {detail:'Backend unavailable'} : {sweep_id:'test', packet: {books:[],items:[],totals:{book_count:0},review_queue:[]},json_file:'test.json',report_file:'test.html'}};}, SpeechSynthesisUtterance: class {}};
  vm.runInNewContext(code, context);
  function render() {cursor = 0; return exports.default();}
  function find(node, predicate) {if (!node || typeof node !== 'object') return; if(predicate(node)) return node; const children = node.props?.children; for(const child of Array.isArray(children) ? children.flat(Infinity) : [children]) {const found = find(child,predicate); if(found) return found;}}
  const button = tree => find(tree, n => n.type === 'button' && ['Start camera sweep','Opening camera…','Finish sweep'].includes(n.props.children));
  return {render, button, find, video, effects, finishCalls: () => finishCalls, uploadedImages, uploadedShelves, frameUploads: () => frameUploads, counts: () => ({stops,cameraCalls,fetchCalls})};
}
(async () => {
  const h = harness({voiceThrows:true});
  const start = h.button(h.render()).props.onClick;
  const pending = start();
  const opening = h.render();
  assert.equal(h.button(opening).props.disabled, true);
  assert.equal(h.find(opening,n=>n.type==='video').props.className,'camera-video');
  await start(); await pending;
  assert.deepEqual(h.counts(),{stops:0,cameraCalls:1,fetchCalls:1});
  assert.equal(h.button(h.render()).props.children,'Finish sweep');
  assert.ok(h.video.srcObject);
  await h.button(h.render()).props.onClick();
  assert.equal(h.counts().stops,1); assert.equal(h.video.srcObject,null);
  const failure = harness({apiFails:true}); await failure.button(failure.render()).props.onClick();
  assert.equal(failure.counts().stops,1); assert.equal(failure.video.srcObject,null);
  assert.equal(failure.button(failure.render()).props.children,'Start camera sweep');
  const insecure = harness({secure:false}); await insecure.button(insecure.render()).props.onClick(); assert.equal(insecure.counts().cameraCalls,0);
  const denied = harness({permissionFails:true}); await denied.button(denied.render()).props.onClick(); assert.equal(denied.counts().fetchCalls,0);
  const unmount = harness(); unmount.render(); const cleanup = unmount.effects[0](); await unmount.button(unmount.render()).props.onClick(); cleanup(); assert.equal(unmount.counts().stops,1);
  let resolveFrame;
  const frameDeferred = new Promise(resolve => resolveFrame = resolve);
  const race = harness({frameDeferred});
  await race.button(race.render()).props.onClick();
  const capture = race.find(race.render(), n => n.type === 'button' && n.props.children === 'Capture this shelf now');
  capture.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  capture.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  capture.props.onClick();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(race.frameUploads(), 1, 'Only one inference request may be in flight');
  const saving = race.button(race.render()).props.onClick();
  await Promise.resolve();
  assert.equal(race.finishCalls(),0,'Must not finish before frame upload returns');
  resolveFrame({ok:true,json:async()=>({packet:{books:[],items:[],totals:{},review_queue:[]},candidate:{notes:[]}})});
  await saving;
  assert.equal(race.finishCalls(),1,'Must finish after frame upload returns');
  assert.equal(race.frameUploads(), 4, 'Every sampled view must survive slow inference');
  assert.deepEqual(race.uploadedImages, [1,2,3,4], 'Different books within one shelf label must not be erased by the final view');
  let release;
  const shelves = harness({frameDeferred: new Promise(resolve => {release=resolve;})});
  await shelves.button(shelves.render()).props.onClick();
  const captureShelf = () => shelves.find(shelves.render(), n => n.type === 'button' && ['Capture this shelf now','Queue this view next'].includes(n.props.children)).props.onClick();
  captureShelf(); await Promise.resolve(); await Promise.resolve();
  captureShelf(); await Promise.resolve(); await Promise.resolve();
  shelves.find(shelves.render(), n => n.type === 'input' && n.props.value === 'Shelf 1').props.onChange({target:{value:'Shelf 2'}});
  captureShelf(); await Promise.resolve(); await Promise.resolve();
  const finishingShelves = shelves.button(shelves.render()).props.onClick();
  release({ok:true,json:async()=>({packet:{books:[],items:[],totals:{},review_queue:[]},candidate:{notes:[]}})});
  await finishingShelves;
  assert.deepEqual(shelves.uploadedShelves,['Shelf 1','Shelf 1','Shelf 2','Shelf 2'],'Moving shelves must not erase the pending prior shelf');
  console.log('PASS: finish waits for in-flight frame upload; visible startup preview, duplicate-start guard, voice failure isolation, finish cleanup, backend failure cleanup, insecure origin, permission denial, unmount cleanup');
})().catch(error => {console.error(error); process.exitCode=1;});
