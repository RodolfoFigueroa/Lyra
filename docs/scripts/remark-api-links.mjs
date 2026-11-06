import path from 'node:path';

const registryKey = Symbol.for('starlight-pydocs');
const identifier = /^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(\))?$/;

export function symbolLookup(registry, base = '') {
  if (registry?.version !== 1 || !Array.isArray(registry.packages)) {
    throw new Error('API autolinking requires the starlight-pydocs version 1 symbol registry.');
  }
  const pkg = registry.packages.find((item) => item.name === 'lyra' && item.base === 'api/lyra');
  if (!(pkg?.symbols instanceof Map)) throw new Error('API autolinking cannot find the lyra symbol registry.');
  const suffixes = new Map();
  for (const [name, symbol] of pkg.symbols) {
    const parts = name.split('.');
    for (let i = 0; i < parts.length; i++) {
      const key = parts.slice(i).join('.');
      const targets = suffixes.get(key) ?? new Set();
      targets.add(symbol.href);
      suffixes.set(key, targets);
    }
  }
  return (text) => {
    if (!identifier.test(text)) return undefined;
    const name = text.replace(/\(\)$/, '');
    const exact = pkg.symbols.get(name)?.href;
    const candidates = suffixes.get(name);
    const target = exact ?? (candidates?.size === 1 ? [...candidates][0] : undefined);
    return target === undefined ? undefined : `${base.replace(/\/$/, '')}${target}`;
  };
}

export default function remarkApiLinks({ root, base, registry = () => globalThis[registryKey] }) {
  return (tree, file) => {
    if (!file.path || !/\.mdx?$/.test(file.path)) return;
    const relative = path.relative(root, file.path);
    if (relative.startsWith('..') || path.isAbsolute(relative)) return;
    const resolve = symbolLookup(registry(), base);
    function walk(node) {
      if (['heading', 'link', 'linkReference', 'code', 'html'].includes(node.type)) return;
      if (node.type.startsWith('mdxJsx') && ['a', 'code', 'pre'].includes(node.name)) return;
      if (!node.children) return;
      let rawDepth = 0;
      node.children = node.children.map((child) => {
        if (child.type === 'html') {
          for (const match of child.value.matchAll(/<(\/?)(a|code|pre)\b[^>]*>/gi)) {
            rawDepth = Math.max(0, rawDepth + (match[1] ? -1 : 1));
          }
          return child;
        }
        if (rawDepth) return child;
        if (child.type === 'inlineCode') {
          const url = resolve(child.value);
          if (url) return { type: 'link', url, children: [child] };
        }
        walk(child);
        return child;
      });
    }
    walk(tree);
  };
}

// Restart on Python edits so Astro's content cache and the API registry are
// recreated together, including pages whose Markdown did not itself change.
export function refreshApiLinks() {
  return {
    name: 'refresh-api-links',
    hooks: {
      'astro:server:setup': ({ server }) => {
        let timer;
        server.watcher.on('all', (event, file) => {
          if (!['add', 'change', 'unlink'].includes(event) || !/\.pyi?$/.test(file)) return;
          if (!file.includes('/src/lyra/')) return;
          clearTimeout(timer);
          timer = setTimeout(() => { void server.restart(); }, 150);
        });
        server.httpServer?.once('close', () => clearTimeout(timer));
      },
    },
  };
}
