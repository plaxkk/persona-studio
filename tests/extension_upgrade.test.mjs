import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { allowedRoute, desktopMessage, CLOUD_ORIGIN } from '../browser-extension/desktop.js';
import { EXTENSION_VERSION, extensionNeedsUpdate, isUpgradeRelatedRoute } from '../assets/web-admin/src/extension-info.ts';
test('website version tracks extension package and handles legacy versions',()=>{
 const manifest=JSON.parse(fs.readFileSync(new URL('../browser-extension/manifest.json',import.meta.url)));
 assert.equal(EXTENSION_VERSION,manifest.version);
 for(const value of [undefined,'1.2.0','1.3.1','1.3.2','invalid']) assert.equal(extensionNeedsUpdate(value),true);
 for(const value of ['1.3.3','1.3.10','2.0.0']) assert.equal(extensionNeedsUpdate(value),false);
});
test('Codex verification and selection allowed, credential writes still denied',()=>{
 for(const path of ['/engines/codex/verify','/engines/codex/select']) assert.equal(allowedRoute(path,'POST'),true);
 for(const path of ['/engines/codex','/engines/codex/credentials','/connections/x','/shell']) {
  assert.equal(allowedRoute(path,'PUT'),false);
  assert.equal(allowedRoute(path,'POST'),false);
 }
 assert.equal(isUpgradeRelatedRoute('/engines/codex/verify'),true);
 assert.equal(isUpgradeRelatedRoute('/engines/codex/credentials'),false);
});
test('connected handshake reports installed version and forwards Codex verification',async()=>{
 globalThis.chrome={runtime:{getManifest:()=>({version:EXTENSION_VERSION})},storage:{session:{get:async()=>({device:{local:'http://127.0.0.1:18880',token:'synthetic-device',expires:Date.now()/1000+300}})}}};
 let called='';globalThis.fetch=async url=>{called=url;return {ok:true,status:200,json:async()=>({task_id:'fixture'})};};
 const sender={url:CLOUD_ORIGIN,frameId:0,tab:{}};
 assert.equal((await desktopMessage({type:'desktop.status'},sender)).extensionVersion,EXTENSION_VERSION);
 const r=await desktopMessage({type:'desktop.api',path:'/engines/codex/verify',method:'POST'},sender);
 assert.equal(r.status,200);assert.equal(called,'http://127.0.0.1:18880/api/v1/engines/codex/verify');
});
