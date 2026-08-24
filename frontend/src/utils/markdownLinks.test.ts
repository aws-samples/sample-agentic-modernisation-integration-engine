import { describe, it, expect } from 'vitest';
import { slugify, extractHeadingIds, resolveMarkdownLink } from './markdownLinks';

/**
 * Link and anchor resolution for generated documentation.
 *
 * The paths and headings here are taken from real output: the ATX CLI's
 * `AWS/comprehensive-codebase-analysis` document tree, and Claude's single-document
 * analysis documentation.
 */

const ATX_PATHS = [
  'README.md',
  'project-overview.md',
  'technical-debt-report.md',
  'architecture/system-overview.md',
  'architecture/components.md',
  'reference/api-reference.md',
  'diagrams/structural/components.md',
];

const NO_HEADINGS = new Set<string>();

describe('slugify', () => {
  it('matches GitHub for headings whose punctuation sits between words', () => {
    // Real ToC anchor from generated documentation: `](#installation--setup)`. A rule that
    // collapses punctuation runs to one hyphen yields `installation-setup` and the link
    // silently leads nowhere.
    expect(slugify('Installation & Setup')).toBe('installation--setup');
    expect(slugify('Build & Development Instructions')).toBe('build--development-instructions');
    expect(slugify('Project Overview — TaskFlow')).toBe('project-overview--taskflow');
  });

  it('drops punctuation without inserting separators', () => {
    expect(slugify('1. Java 8 Runtime')).toBe('1-java-8-runtime');
    expect(slugify('Class Diagram (Domain Model)')).toBe('class-diagram-domain-model');
    expect(slugify('CI/CD Pipeline')).toBe('cicd-pipeline');
  });
});

describe('extractHeadingIds', () => {
  it('collects anchors for every heading level', () => {
    const ids = extractHeadingIds('# One\n## Two\n### Three\n#### Four\n##### Five\n###### Six\n');
    expect([...ids]).toEqual(['one', 'two', 'three', 'four', 'five', 'six']);
  });

  it('uses the rendered text of a heading containing inline markup', () => {
    // Real ATX heading. Stringifying the React children of this heading yields
    // "[object Object]", which is not an anchor anyone can link to.
    const ids = extractHeadingIds('### App Module (`app.module.ts`)\n');
    expect([...ids]).toEqual(['app-module-appmodulets']);
    expect([...extractHeadingIds('### 1. **Executive Summary**\n')]).toEqual([
      '1-executive-summary',
    ]);
  });

  it('ignores shell comments inside fenced code blocks', () => {
    // Generated documentation is full of these. Counting them would advertise anchors
    // that never render.
    const ids = extractHeadingIds(
      '## Build Instructions\n\n```bash\n# Install dependencies\nnpm ci\n```\n\n## Testing\n'
    );
    expect([...ids]).toEqual(['build-instructions', 'testing']);
  });
});

describe('resolveMarkdownLink — same document', () => {
  it('accepts a fragment that matches a heading', () => {
    expect(resolveMarkdownLink('#installation--setup', new Set(['installation--setup']))).toEqual({
      kind: 'same-document',
      fragment: 'installation--setup',
    });
  });

  it('reports a fragment with no matching heading rather than doing nothing', () => {
    // A ToC entry pointing at a heading the model never wrote. Clicking it previously
    // looked like a working link and moved nowhere.
    const link = resolveMarkdownLink('#technology-stack', new Set(['2-technology-stack']));
    expect(link.kind).toBe('unresolvable');
    expect(link).toMatchObject({ target: '#technology-stack' });
  });
});

describe('resolveMarkdownLink — external', () => {
  it.each(['https://aws.amazon.com/', 'http://example.com', 'mailto:team@example.com'])(
    'treats %s as external',
    (href) => {
      expect(resolveMarkdownLink(href, NO_HEADINGS)).toEqual({ kind: 'external', href });
    }
  );

  it('refuses to render a script scheme as navigable', () => {
    expect(resolveMarkdownLink('javascript:alert(1)', NO_HEADINGS).kind).toBe('unresolvable');
  });
});

describe('resolveMarkdownLink — cross document', () => {
  const from = (currentPath: string, href: string) =>
    resolveMarkdownLink(href, NO_HEADINGS, { currentPath, paths: ATX_PATHS });

  it('resolves a sibling reference against the open document\'s directory', () => {
    // The same href means different documents depending on where it is written.
    expect(from('architecture/components.md', 'system-overview.md')).toEqual({
      kind: 'cross-document',
      path: 'architecture/system-overview.md',
      fragment: null,
    });
    expect(from('README.md', 'project-overview.md')).toMatchObject({
      path: 'project-overview.md',
    });
  });

  it('resolves ./ and ../ segments', () => {
    expect(from('README.md', './project-overview.md')).toMatchObject({
      path: 'project-overview.md',
    });
    expect(from('architecture/components.md', '../reference/api-reference.md')).toMatchObject({
      path: 'reference/api-reference.md',
    });
    expect(from('diagrams/structural/components.md', '../../README.md')).toMatchObject({
      path: 'README.md',
    });
  });

  it('carries a trailing fragment through to the target document', () => {
    expect(from('architecture/components.md', '../reference/api-reference.md#authentication'))
      .toEqual({
        kind: 'cross-document',
        path: 'reference/api-reference.md',
        fragment: 'authentication',
      });
  });

  it('tolerates percent-encoding, a missing extension, and directory-style references', () => {
    expect(from('README.md', 'architecture/system%2Doverview.md')).toMatchObject({
      path: 'architecture/system-overview.md',
    });
    expect(from('README.md', 'project-overview')).toMatchObject({ path: 'project-overview.md' });
    expect(
      resolveMarkdownLink('guide/', NO_HEADINGS, {
        currentPath: 'README.md',
        paths: ['README.md', 'guide/index.md'],
      })
    ).toMatchObject({ path: 'guide/index.md' });
  });

  it('tolerates a case difference when only one document can be meant', () => {
    expect(from('README.md', 'Architecture/System-Overview.md')).toMatchObject({
      path: 'architecture/system-overview.md',
    });
  });

  it('treats an ambiguous case-insensitive match as unresolvable rather than guessing', () => {
    const link = resolveMarkdownLink('notes.md', NO_HEADINGS, {
      currentPath: 'README.md',
      paths: ['Notes.md', 'NOTES.md'],
    });
    expect(link.kind).toBe('unresolvable');
    expect(link).toMatchObject({ reason: expect.stringContaining('more than one') });
  });

  it('reports a reference to a document the collection does not have', () => {
    const link = from('README.md', 'architecture/missing.md');
    expect(link).toEqual({
      kind: 'unresolvable',
      target: 'architecture/missing.md',
      reason: 'no collected document matches',
    });
  });

  it('reports a path that climbs above the collection root', () => {
    expect(from('README.md', '../../etc/passwd').kind).toBe('unresolvable');
  });

  it('reports a relative link when the document has no collection to resolve against', () => {
    // The analysis Documentation tab renders one AI-generated document. A relative link
    // there has no destination, and opening it as a URL gives the reader a 404 tab.
    const link = resolveMarkdownLink('architecture/system-overview.md', NO_HEADINGS);
    expect(link.kind).toBe('unresolvable');
    expect(link).toMatchObject({ target: 'architecture/system-overview.md' });
  });
});
