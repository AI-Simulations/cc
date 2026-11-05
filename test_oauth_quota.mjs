// Offline checks for metadata the Codex bridge must preserve, with no credentials or requests.
import assert from 'node:assert/strict';
import { oauthErrorResponse } from './oauth-errors.mjs';

const response = oauthErrorResponse({ statusCode: 429, responseBody: JSON.stringify({ error: {
  type: 'usage_limit_reached', message: 'The usage limit has been reached', resets_at: 2000013000,
  private_token: 'never-forward',
}}), responseHeaders: { 'retry-after': '13000', 'authorization': 'secret', 'set-cookie': 'secret' } });
assert.equal(response.status, 429);
assert.equal(response.headers.get('retry-after'), '13000');
assert.equal(response.headers.get('authorization'), null);
assert.equal(response.headers.get('set-cookie'), null);
const body = await response.json();
assert.equal(body.error.type, 'usage_limit_reached');
assert.equal(body.error.resets_at, 2000013000);
assert.equal(body.error.private_token, undefined);
const wrapped = oauthErrorResponse({ name: 'AI_RetryError', lastError: { statusCode: 429,
  responseBody: JSON.stringify({ error: { type: 'usage_limit_reached', resets_at: 2000013000 } }) } });
assert.equal((await wrapped.json()).error.resets_at, 2000013000);
assert.equal(oauthErrorResponse({ statusCode: 401, message: 'secret' }).status, 401);
const iso = oauthErrorResponse({statusCode:429,responseBody:JSON.stringify({error:{type:'usage_limit_reached',resets_at:'2033-05-18T03:33:20Z'}})});
assert.equal((await iso.json()).error.resets_at,'2033-05-18T03:33:20Z');
const generic = await oauthErrorResponse(new Error('secret')).json();
assert.equal(generic.error.message.includes('secret'), false);
console.log('OAuth quota metadata checks passed.');
