// Keep provider quota metadata, never credentials or arbitrary upstream response fields.
export function oauthErrorResponse(error) {
  for (let depth = 0; depth < 4 && !error?.responseBody; depth++) {
    const nested = error?.lastError ?? error?.cause;
    if (!nested || nested === error) break;
    error = nested;
  }
  let payload;
  try { payload = JSON.parse(error?.responseBody ?? '{}'); } catch { payload = {}; }
  const detail = payload?.error && typeof payload.error === 'object' ? payload.error : {};
  const quota = /usage[_ ]limit|quota[_ ]exceeded/i.test(`${detail.type ?? ''} ${detail.code ?? ''} ${detail.message ?? ''} ${error?.message ?? ''}`);
  const status = quota ? 429 : [400, 401, 402, 403, 404, 408, 429, 500, 502, 503].includes(error?.statusCode) ? error.statusCode : 500;
  const safe = { type: quota ? 'usage_limit_reached' : status === 429 ? 'rate_limit_error' : 'server_error',
    message: quota ? 'The usage limit has been reached.' : `OAuth upstream request failed (HTTP ${status}).` };
  for (const name of ['resets_at', 'reset_at', 'resets_in_seconds']) {
    const value = detail[name];
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) safe[name] = value;
    else if (name !== 'resets_in_seconds' && typeof value === 'string' && value.length <= 64 &&
      /^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))) safe[name] = value;
  }
  const headers = new Headers({ 'content-type': 'application/json' });
  const upstream = new Headers(error?.responseHeaders ?? {});
  for (const name of ['retry-after', 'anthropic-ratelimit-unified-reset', 'anthropic-ratelimit-unified-status',
    'anthropic-ratelimit-unified-representative-claim']) {
    if (upstream.has(name)) headers.set(name, upstream.get(name));
  }
  return new Response(JSON.stringify({ error: safe }), { status, headers });
}
