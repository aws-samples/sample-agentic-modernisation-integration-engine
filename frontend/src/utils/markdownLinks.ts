/**
 * Link and heading-anchor resolution for generated documentation.
 *
 * Generated markdown — both the ATX CLI's document tree and Claude's single-document
 * output — links internally: an index links siblings by relative path, and a table of
 * contents links headings by fragment. Neither form is a URL the SPA can serve, so both
 * have to be resolved against what the app actually has in hand before a link is rendered
 * as navigable.
 *
 * The rule this module encodes: a link is rendered as navigable only when its destination
 * is known to exist. Anything else is reported as unfollowable. A link that opens a dead
 * tab and a link that silently does nothing are both worse than one that says it cannot
 * be followed.
 */

/** URI schemes that are safe to hand to the browser in a new tab. */
const NAVIGABLE_SCHEMES = new Set(['http', 'https', 'mailto', 'tel']);

const SCHEME_PATTERN = /^([a-z][a-z0-9+.-]*):/i;

/**
 * Where a markdown link points, once resolved against the surrounding document.
 *
 * `unresolvable` is a first-class outcome, not an error path: generated markdown
 * routinely references documents and headings that were never written.
 */
export type ResolvedLink =
  /** An absolute URL with a safe scheme — opens in a new tab. */
  | { kind: 'external'; href: string }
  /** A fragment matching a heading in the document being rendered. */
  | { kind: 'same-document'; fragment: string }
  /** Another document in the same collected set, optionally at a fragment. */
  | { kind: 'cross-document'; path: string; fragment: string | null }
  /** Nothing in reach matches. `target` names what could not be found. */
  | { kind: 'unresolvable'; target: string; reason: string };

/** The collection a document is being read within, when there is one. */
export interface DocCollection {
  /** The open document's path relative to the collection root, e.g. `architecture/components.md`. */
  currentPath: string;
  /** Every document path in the collection, relative to the same root. */
  paths: string[];
}

/**
 * GitHub-compatible heading slug.
 *
 * Deliberately GitHub's rule rather than a simpler one: generated tables of contents
 * are written by models trained on GitHub markdown, so they assume GitHub's slugs.
 * The difference is not cosmetic — GitHub drops punctuation and then maps each
 * remaining space to a hyphen, so `Installation & Setup` becomes `installation--setup`
 * with two hyphens. A rule that collapses punctuation runs to a single hyphen produces
 * `installation-setup` and every such ToC entry silently leads nowhere.
 */
export function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s/g, '-');
}

/** Reduce inline markdown to the text a reader sees, so slugs match rendered headings. */
function stripInlineMarkdown(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .trim();
}

/**
 * The heading anchors a markdown document actually offers.
 *
 * Fenced code blocks are skipped. This is not a nicety: generated documentation is full
 * of shell snippets whose comments start with `#`, and counting those as headings would
 * advertise anchors that never render.
 */
export function extractHeadingIds(markdown: string): Set<string> {
  const ids = new Set<string>();
  let fenceChar: string | null = null;

  for (const rawLine of markdown.split('\n')) {
    const line = rawLine.trimEnd();

    const fence = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (fence) {
      const char = fence[1][0];
      if (fenceChar === null) fenceChar = char;
      else if (fenceChar === char) fenceChar = null;
      continue;
    }
    if (fenceChar !== null) continue;

    const heading = /^ {0,3}(#{1,6})\s+(.*)$/.exec(line);
    if (!heading) continue;

    // Drop a closing `###` run, which ATX-style headings may carry.
    const id = slugify(stripInlineMarkdown(heading[2].replace(/\s+#+\s*$/, '')));
    if (id) ids.add(id);
  }

  return ids;
}

function decodeSegment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    // A stray `%` is likelier than a real escape in hand-written markdown.
    return value;
  }
}

function splitFragment(href: string): { path: string; fragment: string | null } {
  const index = href.indexOf('#');
  if (index < 0) return { path: href, fragment: null };
  const fragment = href.slice(index + 1);
  return { path: href.slice(0, index), fragment: fragment ? decodeSegment(fragment) : null };
}

/**
 * Resolve a relative path against the open document's own directory.
 *
 * The directory matters: `system-overview.md` inside `architecture/components.md` means
 * `architecture/system-overview.md`, while the same href inside `README.md` means a
 * top-level file. Real ATX output uses both forms, so resolving everything against the
 * collection root would break every sibling link inside a subdirectory.
 *
 * Returns `null` when the path climbs above the collection root.
 */
function resolveSegments(
  currentPath: string,
  hrefPath: string
): { path: string; isDirectory: boolean } | null {
  const isDirectory = hrefPath.endsWith('/');
  const segments = hrefPath.startsWith('/') ? [] : currentPath.split('/').slice(0, -1);
  const out = [...segments];

  for (const segment of hrefPath.split('/')) {
    if (segment === '' || segment === '.') continue;
    if (segment === '..') {
      if (out.length === 0) return null;
      out.pop();
      continue;
    }
    out.push(segment);
  }

  if (out.length === 0) return null;
  return { path: out.join('/'), isDirectory };
}

/**
 * Candidate paths for one resolved reference, in decreasing confidence.
 *
 * Generated markdown is inconsistent about extensions and directory indexes, so a
 * reference to `architecture` may legitimately mean `architecture.md` or
 * `architecture/index.md`.
 */
function candidatePaths(path: string, isDirectory: boolean): string[] {
  const candidates: string[] = [];
  if (!isDirectory) {
    candidates.push(path);
    if (!/\.[a-z0-9]+$/i.test(path)) candidates.push(`${path}.md`);
  }
  candidates.push(`${path}/index.md`, `${path}/README.md`);
  return candidates;
}

/**
 * Pick the one document a reference names.
 *
 * Exact matches are tried across every candidate before any case-insensitive match, so a
 * precise reference is never beaten by a loose one. A case-insensitive tie is reported as
 * ambiguous — guessing between two real documents is worse than admitting the reference
 * is unusable.
 */
function matchPath(candidates: string[], paths: string[]): string | 'ambiguous' | null {
  for (const candidate of candidates) {
    const exact = paths.find((path) => path === candidate);
    if (exact) return exact;
  }

  for (const candidate of candidates) {
    const lowered = candidate.toLowerCase();
    const hits = paths.filter((path) => path.toLowerCase() === lowered);
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) return 'ambiguous';
  }

  return null;
}

/**
 * Classify a markdown link href against the document being rendered.
 *
 * @param href The raw href from the markdown.
 * @param headingIds Anchors the rendered document offers, from {@link extractHeadingIds}.
 * @param collection The collected document set, when the document belongs to one. Absent
 *   for a standalone document, in which case relative links have no destination and are
 *   reported unresolvable rather than opened as URLs the SPA cannot serve.
 */
export function resolveMarkdownLink(
  href: string | undefined,
  headingIds: Set<string>,
  collection?: DocCollection
): ResolvedLink {
  const raw = href?.trim() ?? '';
  if (!raw) {
    return { kind: 'unresolvable', target: '', reason: 'the link has no target' };
  }

  const scheme = SCHEME_PATTERN.exec(raw);
  if (scheme) {
    if (NAVIGABLE_SCHEMES.has(scheme[1].toLowerCase())) {
      return { kind: 'external', href: raw };
    }
    return {
      kind: 'unresolvable',
      target: raw,
      reason: `“${scheme[1]}:” links are not followed`,
    };
  }

  // Protocol-relative (`//host/...`) is an external URL, not a collection path.
  if (raw.startsWith('//')) {
    return { kind: 'external', href: raw };
  }

  const { path: hrefPath, fragment } = splitFragment(raw);

  if (!hrefPath) {
    if (!fragment) {
      return { kind: 'unresolvable', target: raw, reason: 'the link has no target' };
    }
    if (headingIds.has(fragment)) {
      return { kind: 'same-document', fragment };
    }
    return {
      kind: 'unresolvable',
      target: `#${fragment}`,
      reason: 'this document has no matching heading',
    };
  }

  if (!collection) {
    return {
      kind: 'unresolvable',
      target: raw,
      reason: 'this document is not part of a navigable set, so relative links have no destination',
    };
  }

  const resolved = resolveSegments(collection.currentPath, decodeSegment(hrefPath));
  if (!resolved) {
    return {
      kind: 'unresolvable',
      target: raw,
      reason: 'the path points outside the collected documentation',
    };
  }

  const match = matchPath(candidatePaths(resolved.path, resolved.isDirectory), collection.paths);
  if (match === 'ambiguous') {
    return {
      kind: 'unresolvable',
      target: resolved.path,
      reason: 'more than one collected document matches',
    };
  }
  if (match === null) {
    return {
      kind: 'unresolvable',
      target: resolved.path,
      reason: 'no collected document matches',
    };
  }

  return { kind: 'cross-document', path: match, fragment };
}
