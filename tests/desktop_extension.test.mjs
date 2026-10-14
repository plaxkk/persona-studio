import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowedRoute, desktopMessage, CLOUD_ORIGIN } from '../browser-extension/desktop.js';
test('route allowlist rejects secrets, traversal and arbitrary commands',()=>{
  for(const path of ['/connections/x','/engines/hermes','/auth/setup','/../.env','/%2e%2e/.env','//evil.example','/shell']) assert.equal(allowedRoute(path,'PUT'),false);
  assert.equal(allowedRoute('/drafts/abc-123','PUT'),true);
  assert.equal(allowedRoute('/generate','POST'),true);
  assert.equal(allowedRoute('/persona-imports','POST'),true);
  assert.equal(allowedRoute('/persona-browser','POST'),false);
  assert.equal(allowedRoute('/persona-imports/0123456789abcdef0123456789abcdef/evidence','POST'),false);
});
// The extension's list and the studio's list have to agree on the 06B-2 routes,
// or the cloud panel gets a 403 the client cannot explain. The Python half of
// this pair lives in tests/test_deconstruct.py.
test('the benchmark deconstruction routes are allowed and nothing wider is',()=>{
  assert.equal(allowedRoute('/benchmarks/deconstructions/lifesinger','GET'),true);
  assert.equal(allowedRoute('/benchmarks/deconstruction/2065286424128331927','GET'),true);
  assert.equal(allowedRoute('/benchmarks/deconstruct/2065286424128331927','POST'),true);
  // A post id is digits; a handle is not a post id, and neither is a traversal.
  assert.equal(allowedRoute('/benchmarks/deconstruct/abc','POST'),false);
  assert.equal(allowedRoute('/benchmarks/deconstruction/..','GET'),false);
  assert.equal(allowedRoute('/benchmarks/deconstructions/lifesinger','POST'),false);
});
// Same pair as above, for 06C-3: the watchroom is written from the cloud panel,
// so all four paths have to survive this list as well as `desktop_route`.
test('the watchroom routes are allowed and nothing wider is',()=>{
  assert.equal(allowedRoute('/accounts/watchroom','GET'),true);
  assert.equal(allowedRoute('/accounts/watchroom/lifesinger/watch','POST'),true);
  assert.equal(allowedRoute('/accounts/watchroom/lifesinger/review','POST'),true);
  assert.equal(allowedRoute('/accounts/watchroom/lifesinger','DELETE'), true);
  assert.equal(allowedRoute('/accounts/watchroom/lifesinger','GET'),false);
  assert.equal(allowedRoute('/accounts/watchroom/lifesinger/hack','POST'),false);
  assert.equal(allowedRoute('/accounts/watchroom/lifesinger/watch','GET'),false);
  assert.equal(allowedRoute('/accounts/watchroom','POST'),false);
});
// And the same pair again for 06D. The Python half lives in
// tests/test_algorithm_watch.py; a route that only one list knows about is a
// 403 the cloud panel cannot explain.
test('the algorithm routes are allowed and nothing wider is',()=>{
  assert.equal(allowedRoute('/algorithm/events','GET'),true);
  assert.equal(allowedRoute('/algorithm/proposals/0123456789abcdef0123456789abcdef','GET'),true);
  assert.equal(allowedRoute('/algorithm/check','POST'),true);
  assert.equal(allowedRoute('/algorithm/impact/2065286424128331927','POST'),true);
  assert.equal(allowedRoute('/algorithm/watching/on','POST'),true);
  assert.equal(allowedRoute('/algorithm/watching/off','POST'),true);
  // An event id is hex and a sha is hex; neither is a traversal or a name.
  assert.equal(allowedRoute('/algorithm/proposals/..','GET'),false);
  assert.equal(allowedRoute('/algorithm/proposals/not-an-id','GET'),false);
  assert.equal(allowedRoute('/algorithm/impact/zzz','POST'),false);
  assert.equal(allowedRoute('/algorithm/watching/maybe','POST'),false);
  assert.equal(allowedRoute('/algorithm/events','POST'),false);
  assert.equal(allowedRoute('/algorithm/check','GET'),false);
  assert.equal(allowedRoute('/algorithm/watching/on','GET'),false);
  assert.equal(allowedRoute('/algorithm','GET'),false);
});
test('disconnect forgets capability even while local backend is offline',async()=>{
  let removed=false;
  globalThis.chrome={storage:{session:{get:async()=>({device:{local:'http://127.0.0.1:18880',token:'fixture-device',expires:Date.now()/1000+300}}),remove:async()=>{removed=true;}}}};
  globalThis.fetch=async()=>{throw new TypeError('offline');};
  const reply=await desktopMessage({type:'desktop.api',path:'/auth/logout',method:'POST'}, {url:CLOUD_ORIGIN,frameId:0,tab:{}});
  assert.equal(reply.code,'offline');assert.equal(removed,true);assert.equal(JSON.stringify(reply).includes('fixture-device'),false);
});
