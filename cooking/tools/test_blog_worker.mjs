import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const worker = (await import('data:text/javascript;base64,' + Buffer.from(readFileSync(new URL('../_worker.js', import.meta.url))).toString('base64'))).default;
const assets = {
  '/': '<!--og--><title>Default</title><!--/og-->',
  '/data/og.json': JSON.stringify({'1001':{t:'Existing NYT',d:'Existing',i:'card/example.jpg'}}),
  '/data/smitten_kitchen_og.json': JSON.stringify({'10000000001':{t:'Pancakes',d:'Smitten Kitchen',i:'og-cover.jpg'}}),
  '/data/cookie_and_kate_og.json': JSON.stringify({'10000000002':{t:'Paella',d:'Cookie and Kate',i:'og-cover.jpg'}}),
};
const env = {ASSETS:{fetch:async request => new Response(assets[new URL(request.url || request).pathname] || 'Not found')}};
for (const [id, title] of [['1001','Existing NYT'], ['10000000001','Pancakes'], ['10000000002','Paella']]) {
  const result = await worker.fetch(new Request('https://example.com/r/' + id + '-recipe'), env);
  assert.match(await result.text(), new RegExp('<title>' + title));
}
console.log('Existing NYT and both blog share-preview routes passed');
