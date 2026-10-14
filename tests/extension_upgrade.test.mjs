import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { allowedRoute, desktopMessage, CLOUD_ORIGIN } from '../browser-extension/desktop.js';
import { EXTENSION_VERSION, extensionNeedsUpdate, isUpgradeRelatedRoute } from '../assets/web-admin/src/extension-info.ts';
test('website version tracks extension package and handles legacy versions',()=>{
 const manifest=JSON.parse(fs.readFileSync(new URL('../browser-extension/manifest.json',import.meta.url)));
 assert.equal(EXTENSION_VERSION,manifest.version);
 for(const value of [undefined,'1.2.0','1.3.1','1.3.2','1.3.3','1.3.7','1.3.8','1.3.9','1.3.10','1.3.11','1.3.13','invalid']) assert.equal(extensionNeedsUpdate(value),true);
 for(const value of ['1.3.14','2.0.0']) assert.equal(extensionNeedsUpdate(value),false);
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

test('creation workflow routes pass through the extension',()=>{
 const id='a'.repeat(32);
 for (const [path,method] of [['/creations','GET'],['/creations','POST'],['/creations/'+id,'GET'],['/creations/'+id,'PUT'],['/creations/'+id+'/turn','POST'],['/creations/'+id+'/finalize','POST']]) {
  assert.equal(allowedRoute(path,method),true); assert.equal(isUpgradeRelatedRoute(path),true);
 }
 assert.equal(allowedRoute('/creations/'+id+'/delete','POST'),false);
});
test('creation attachments pass through the extension while other sub-paths stay shut',()=>{
 const id='a'.repeat(32),media='b'.repeat(32);
 for (const [path,method] of [
  ['/creations/'+id+'/media','GET'],['/creations/'+id+'/media','POST'],
  ['/creations/'+id+'/media/'+media+'/chunks','POST'],
  ['/creations/'+id+'/media/'+media+'/complete','POST'],
  ['/creations/'+id+'/media/'+media+'/chunks/3','GET'],
  ['/creations/'+id+'/media/'+media,'DELETE']
 ]) {
  assert.equal(allowedRoute(path,method),true); assert.equal(isUpgradeRelatedRoute(path),true);
 }
 for (const [path,method] of [
  ['/creations/'+id+'/media/'+media,'GET'],
  ['/creations/'+id+'/media/'+media+'/chunks','GET'],
  ['/creations/'+id+'/media/'+media+'/complete','GET'],
  ['/creations/'+id+'/media/'+media+'/chunks/3','POST'],
  ['/creations/'+id+'/media/'+media+'/chunks/3','DELETE'],
  ['/creations/'+id+'/media/'+media+'/chunks?a=1','GET'],
  ['/creations/'+id+'/media/'+media+'/chunks/-1','GET'],
  ['/creations/'+id+'/media/'+media.toUpperCase()+'/chunks/0','GET']
 ]) assert.equal(allowedRoute(path,method),false,path+' '+method);
});
test('interaction paging and review are scoped to the local bridge',()=>{
 assert.equal(allowedRoute('/interactions/page?view=all&offset=200&limit=25&q=%E6%97%85%E8%A1%8C','GET'),true);
 assert.equal(allowedRoute('/interactions/skills','GET'),true);
 assert.equal(allowedRoute('/interactions/skills','POST'),true);
 assert.equal(allowedRoute('/interactions/skills/3','PUT'),true);
 assert.equal(allowedRoute('/interactions/skills/3','DELETE'),true);
 assert.equal(allowedRoute('/interactions/123/context','POST'),true);
 assert.equal(allowedRoute('/interactions/123/review','PUT'),true);
 assert.equal(allowedRoute('/interactions/page?url=https%3A%2F%2Fevil.example','GET'),false);
 assert.equal(allowedRoute('/interactions/123/review','GET'),false);
});
test('growth and publication metrics routes stay scoped to the local bridge',()=>{
 const id='a'.repeat(32);
 for(const [path,method] of [
  ['/growth','GET'],['/growth/goal','PUT'],['/growth/read','POST'],
  ['/drafts/'+id+'/metrics','GET'],['/drafts/'+id+'/metrics/read','POST']
 ]) { assert.equal(allowedRoute(path,method),true); assert.equal(isUpgradeRelatedRoute(path),true); }
 assert.equal(allowedRoute('/growth/goal','GET'),false);
 assert.equal(allowedRoute('/drafts/'+id+'/metrics/export','POST'),false);
});
test('weekly report routes pass through the extension and nothing wider does',()=>{
 const id='w20261013-kkplayit';
 for (const [path,method] of [['/reports/weekly','GET'],['/reports/weekly/generate','POST'],['/reports/weekly/'+id,'GET']]) {
  assert.equal(allowedRoute(path,method),true,path+' '+method);
  assert.equal(isUpgradeRelatedRoute(path),true,path);
 }
 for (const [path,method] of [
  ['/reports','GET'],['/reports/weekly','POST'],
  ['/reports/weekly/'+id,'POST'],['/reports/weekly/'+id,'DELETE'],
  ['/reports/weekly/'+id+'/../settings','GET'],['/reports/weekly/a b','GET'],
  ['/reports/weekly?a=1','GET']
 ]) assert.equal(allowedRoute(path,method),false,path+' '+method);
 // The read pattern accepts any id, so this GET is routed — but it can only ask
 // for a report named "generate", and generating is POST-only.
 assert.equal(allowedRoute('/reports/weekly/generate','GET'),true);
 assert.equal(allowedRoute('/reports/weekly/generate','POST'),true);
});
test('benchmark routes pass through the extension and nothing wider does',()=>{
 for(const [path,method] of [
  ['/benchmarks/accounts','GET'],['/benchmarks/accounts','POST'],
  ['/benchmarks/posts/lifesinger','GET'],['/benchmarks/read/lifesinger','POST']
 ]) { assert.equal(allowedRoute(path,method),true,path+' '+method);
      assert.equal(isUpgradeRelatedRoute(path),true,path); }
 for(const [path,method] of [
  ['/benchmarks','GET'],['/benchmarks/posts','GET'],
  ['/benchmarks/posts/lifesinger','POST'],['/benchmarks/read/lifesinger','GET'],
  ['/benchmarks/posts/lifesinger/export','POST'],['/benchmarks/accounts/delete','POST'],
  // A query string is refused by the bridge unless the route is on its short
  // list, which is why the handle travels in the path.
  ['/benchmarks/posts?handle=lifesinger','GET'],
  ['/benchmarks/posts/'+'a'.repeat(16),'GET'],['/benchmarks/posts/'+'a b','GET']
 ]) assert.equal(allowedRoute(path,method),false,path+' '+method);
});
test('Subscription analytics read routes are specific and credentials stay local',()=>{
 assert.equal(allowedRoute('/analytics/content','GET'),true);
 assert.equal(allowedRoute('/analytics/content/read','POST'),true);
 assert.equal(allowedRoute('/analytics/content/backfill','POST'),true);
 assert.equal(allowedRoute('/analytics/content/read','GET'),false);
 assert.equal(allowedRoute('/analytics/content/export','POST'),false);
 assert.equal(allowedRoute('/connections/supabase','PUT'),false);
 const manifest=JSON.parse(fs.readFileSync(new URL('../browser-extension/manifest.json',import.meta.url)));
 // Two page collectors, each scoped to its own page kind and both in the MAIN
 // world: analytics reads its own page, search reads the search result page.
 assert.deepEqual(manifest.content_scripts.map(({world,run_at})=>[world,run_at]),[['MAIN','document_start'],['MAIN','document_start']]);
 assert.deepEqual(manifest.content_scripts.map(item=>item.js),[['analytics-capture.js'],['search-capture.js']]);
});
test('extension download contains every local script referenced by its manifest and imports',()=>{
 const build=fs.readFileSync(new URL('../assets/web-admin/build-desktop.mjs',import.meta.url),'utf8');
 const names=new Set([...build.matchAll(/"([\w-]+\.(?:js|json|html|css|md))"/g)].map(match=>match[1]));
 const manifest=JSON.parse(fs.readFileSync(new URL('../browser-extension/manifest.json',import.meta.url)));
 const referenced=[manifest.background.service_worker,...manifest.content_scripts.flatMap(item=>item.js)];
 for(const name of names) if(name.endsWith('.js')) {
  const source=fs.readFileSync(new URL('../browser-extension/'+name,import.meta.url),'utf8');
  referenced.push(...[...source.matchAll(/from\s+["']\.\/([\w-]+\.js)["']/g)].map(match=>match[1]));
 }
 for(const name of referenced) assert.ok(names.has(name),`${name} is missing from the ZIP`);
});
