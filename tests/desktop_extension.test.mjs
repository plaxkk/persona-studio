import { test } from 'node:test';
import assert from 'node:assert/strict';
import { allowedRoute, desktopMessage, CLOUD_ORIGIN } from '../browser-extension/desktop.js';
test('route allowlist rejects secrets, traversal and arbitrary commands',()=>{
  for(const path of ['/connections/x','/engines/hermes','/auth/setup','/../.env','/%2e%2e/.env','//evil.example','/shell']) assert.equal(allowedRoute(path,'PUT'),false);
  assert.equal(allowedRoute('/drafts/abc-123','PUT'),true);
  assert.equal(allowedRoute('/generate','POST'),true);
});
test('disconnect forgets capability even while local backend is offline',async()=>{
  let removed=false;
  globalThis.chrome={storage:{session:{get:async()=>({device:{local:'http://127.0.0.1:18880',token:'fixture-device',expires:Date.now()/1000+300}}),remove:async()=>{removed=true;}}}};
  globalThis.fetch=async()=>{throw new TypeError('offline');};
  const reply=await desktopMessage({type:'desktop.api',path:'/auth/logout',method:'POST'}, {url:CLOUD_ORIGIN,frameId:0,tab:{}});
  assert.equal(reply.code,'offline');assert.equal(removed,true);assert.equal(JSON.stringify(reply).includes('fixture-device'),false);
});
