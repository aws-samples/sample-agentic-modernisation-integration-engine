import { useMemo } from 'react';
import {
  extractHeadingIds,
  resolveMarkdownLink,
  slugify,
  type DocCollection,
} from './markdownLinks';

/**
 * The `components` map every generated-markdown surface hands to react-markdown.
 *
 * One implementation, because the three surfaces that render generated markdown — the ATX
 * documentation tab, the analysis Documentation tab, and the streaming documentation
 * viewer — share a single contract: headings get anchors, internal links navigate, and
 * links that lead nowhere say so. They differ only in whether a relative link has a
 * collection to resolve against, which is one option rather than a second implementation.
 * Three separate copies previously carried three different slug rules, which is exactly the
 * divergence this consolidation exists to stop.
 */

/** Style for text that is only exposed to assistive technology. */
const visuallyHidden: React.CSSProperties = {
  position: 'absolute',
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
  border: 0,
};

const unresolvedStyle: React.CSSProperties = {
  textDecoration: 'underline dotted',
  textDecorationThickness: 'from-font',
  cursor: 'not-allowed',
  opacity: 0.75,
};

/** Flatten rendered children to the text a reader sees. */
function nodeText(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join('');
  if (typeof node === 'object' && 'props' in node) {
    const element = node as React.ReactElement<{ children?: React.ReactNode }>;
    return nodeText(element.props.children);
  }
  return '';
}

/** Navigation within a collected document set. */
export interface DocNavigation extends DocCollection {
  /**
   * Open another document in the set.
   *
   * `fragment` is the anchor to scroll to once that document has rendered — the caller
   * owns the timing, because the target markup does not exist in the tick the link is
   * clicked.
   */
  onNavigate: (path: string, fragment: string | null) => void;
}

export interface MarkdownComponentOptions {
  /** The markdown being rendered, used to know which heading anchors exist. */
  markdown: string;
  /** Present only when the document belongs to a navigable collection. */
  navigation?: DocNavigation;
}

function scrollToHeading(id: string): void {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' });
}

/**
 * Build the react-markdown `components` map for one document.
 *
 * Call through {@link useMarkdownComponents} in components; this form exists for tests and
 * for callers that already memoise.
 */
export function createMarkdownComponents({ markdown, navigation }: MarkdownComponentOptions) {
  const headingIds = extractHeadingIds(markdown);

  const heading = (Tag: 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6') => {
    const Heading = ({ children, ...props }: React.HTMLAttributes<HTMLHeadingElement>) => (
      // Slug from the flattened text, not `String(children)`: a heading containing a code
      // span or bold run arrives as an array of nodes, and stringifying that yields
      // `[object Object]` — which is what real headings like
      // "App Module (`app.module.ts`)" produced before.
      <Tag id={slugify(nodeText(children))} {...props}>
        {children}
      </Tag>
    );
    Heading.displayName = `Markdown${Tag.toUpperCase()}`;
    return Heading;
  };

  const Anchor = ({ href, children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const link = resolveMarkdownLink(href, headingIds, navigation);

    switch (link.kind) {
      case 'external':
        return (
          <a href={link.href} target="_blank" rel="noopener noreferrer" {...props}>
            {children}
          </a>
        );

      case 'same-document':
        return (
          <a
            href={`#${link.fragment}`}
            onClick={(event) => {
              event.preventDefault();
              scrollToHeading(link.fragment);
            }}
            {...props}
          >
            {children}
          </a>
        );

      case 'cross-document':
        return (
          <a
            href={`#${link.path}`}
            data-doc-path={link.path}
            onClick={(event) => {
              event.preventDefault();
              navigation?.onNavigate(link.path, link.fragment);
            }}
            {...props}
          >
            {children}
          </a>
        );

      case 'unresolvable': {
        const explanation = link.target
          ? `Link target “${link.target}” cannot be opened: ${link.reason}.`
          : `This link cannot be opened: ${link.reason}.`;
        return (
          // Not an <a>: there is nothing to navigate to, and a dead tab or a click that
          // silently does nothing both misrepresent the state. The role is kept so
          // assistive technology still announces it as an unavailable link.
          <span
            role="link"
            aria-disabled="true"
            title={explanation}
            data-unresolved-link={link.target}
            style={unresolvedStyle}
            {...props}
          >
            {children}
            <span style={visuallyHidden}>{` (${explanation})`}</span>
          </span>
        );
      }
    }
  };
  Anchor.displayName = 'MarkdownAnchor';

  return {
    h1: heading('h1'),
    h2: heading('h2'),
    h3: heading('h3'),
    h4: heading('h4'),
    h5: heading('h5'),
    h6: heading('h6'),
    a: Anchor,
  };
}

/** Memoised {@link createMarkdownComponents} for use in components. */
export function useMarkdownComponents(options: MarkdownComponentOptions) {
  const { markdown, navigation } = options;
  return useMemo(
    () => createMarkdownComponents({ markdown, navigation }),
    [markdown, navigation]
  );
}
