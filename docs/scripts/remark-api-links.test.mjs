import assert from 'node:assert/strict';
import test from 'node:test';
import { createMarkdownProcessor } from '@astrojs/markdown-remark';
import remarkApiLinks, { symbolLookup } from './remark-api-links.mjs';

const registry = { version: 1, packages: [{ name: 'lyra', base: 'api/lyra', symbols: new Map([
  ['lyra.a.Widget', { href: '/api/lyra/a/#lyra.a.Widget' }],
  ['lyra.Widget', { href: '/api/lyra/a/#lyra.a.Widget' }],
  ['lyra.a.Widget.run', { href: '/api/lyra/a/#lyra.a.Widget.run' }],
  ['lyra.b.run', { href: '/api/lyra/b/#lyra.b.run' }],
]) }] };

test('qualified names, aliases, suffixes, ambiguity, and deployment bases', () => {
  for (const base of ['', '/', '/Lyra/latest', '/Lyra/v1/']) {
    const resolve = symbolLookup(registry, base);
    for (const name of ['Widget', 'Widget()', 'lyra.Widget', 'a.Widget']) {
      assert.equal(resolve(name), `${base.replace(/\/$/, '')}/api/lyra/a/#lyra.a.Widget`);
    }
    assert.ok(resolve('Widget.run()'));
    for (const name of ['run', 'widget', 'Unknown', 'Widget(x)', 'list[Widget]']) assert.equal(resolve(name), undefined);
  }
  assert.throws(() => symbolLookup(undefined));
  assert.throws(() => symbolLookup({ version: 2 }));
  assert.throws(() => symbolLookup({ version: 1, packages: [] }));
});

test('render prose while preserving headings, code blocks, links and raw code', async () => {
  const renderer = await createMarkdownProcessor({ remarkPlugins: [[remarkApiLinks, { root: '/docs', base: '', registry: () => registry }]] });
  const result = await renderer.render('# `Widget`\n\n`Widget` and [`Widget`](https://example.org).\n\n```py\nWidget\n```\n\n<code>Widget</code>', { fileURL: new URL('file:///docs/example.md') });
  assert.match(result.code, /<a href="\/api\/lyra\/a\/#lyra.a.Widget"><code>Widget<\/code><\/a>/);
  assert.equal((result.code.match(/href="\/api\/lyra/g) ?? []).length, 1);
});

test('nested MDX prose, exclusions, idempotence, and registry refresh', () => {
  const code = () => ({ type: 'inlineCode', value: 'Widget' });
  const tree = { type: 'root', children: [
    { type: 'mdxJsxFlowElement', name: 'Steps', children: [code()] },
    { type: 'mdxJsxTextElement', name: 'code', children: [code()] },
    { type: 'linkReference', children: [code()] },
    { type: 'heading', children: [code()] },
  ] };
  let current = registry;
  const transform = remarkApiLinks({ root: '/docs', base: '', registry: () => current });
  transform(tree, { path: '/docs/example.mdx' });
  assert.equal(tree.children[0].children[0].type, 'link');
  for (const node of tree.children.slice(1)) assert.equal(node.children[0].type, 'inlineCode');
  const first = structuredClone(tree);
  transform(tree, { path: '/docs/example.mdx' });
  assert.deepEqual(tree, first);
  current = { version: 1, packages: [{ name: 'lyra', base: 'api/lyra', symbols: new Map() }] };
  const fresh = { type: 'root', children: [code()] };
  transform(fresh, { path: '/docs/example.md' });
  assert.equal(fresh.children[0].type, 'inlineCode');
});
