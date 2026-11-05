"""Install cc's small OAuth error-metadata patch without replacing the local OAuth build."""
from pathlib import Path


def patch_source(source):
    marker = "import { oauthErrorResponse } from './cc-oauth-errors.mjs';\n"
    if marker not in source:
        source = marker + source
    old = '''      return toErrorResponse(
        error instanceof Error ? error.message : "Unexpected server error.",
        500,
        "server_error"
      );'''
    if old in source:
        source = source.replace(old, '      return oauthErrorResponse(error);', 1)
    elif 'return oauthErrorResponse(error);' not in source:
        raise ValueError('OAuth error handler changed; refusing an unrecognized build.')
    stream_start = '  const stream = new ReadableStream({\n    async start(controller) {'
    preflight = '''  const quotaIterator = result.fullStream[Symbol.asyncIterator]();
  let quotaFirst;
  do {
    quotaFirst = await quotaIterator.next();
  } while (!quotaFirst.done && ["start", "start-step", "step-start"].includes(quotaFirst.value?.type));
  if (quotaFirst.value?.type === "error") {
    await quotaIterator.return?.();
    return oauthErrorResponse(quotaFirst.value.error);
  }
  const quotaParts = { async *[Symbol.asyncIterator]() {
    if (!quotaFirst.done) yield quotaFirst.value;
    yield* { [Symbol.asyncIterator]: () => quotaIterator };
  }};
'''
    if 'const quotaIterator =' not in source:
        if stream_start not in source or 'for await (const part of result.fullStream)' not in source:
            raise ValueError('OAuth streaming handler changed; refusing an unrecognized build.')
        source = source.replace(stream_start, preflight + stream_start, 1)
        source = source.replace('for await (const part of result.fullStream)', 'for await (const part of quotaParts)', 1)
    return source


def main():
    import os
    root = Path.home() / '.config/cc/openai-oauth-patched/node_modules/openai-oauth/dist'
    matches = [p for p in root.glob('chunk-*.js') if 'var createOpenAIOAuthRuntime =' in p.read_text(encoding='utf-8')]
    if len(matches) != 1:
        raise SystemExit('Expected one installed OAuth server bundle.')
    target = matches[0]
    before = target.read_text(encoding='utf-8')
    after = patch_source(before)
    helper = Path(__file__).with_name('oauth-errors.mjs').read_bytes()
    destination = root / 'cc-oauth-errors.mjs'
    destination.write_bytes(helper)
    if after != before:
        backup = target.with_suffix('.js.before-quota-patch')
        if not backup.exists():
            backup.write_bytes(target.read_bytes())
        temp = target.with_suffix('.js.tmp')
        temp.write_text(after, encoding='utf-8', newline='\n')
        os.replace(temp, target)
    print(f'OAuth quota metadata patch installed: {target}')


if __name__ == '__main__':
    main()
