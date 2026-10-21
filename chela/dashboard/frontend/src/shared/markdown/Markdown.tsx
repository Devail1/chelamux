// The ONE place the app sets innerHTML from a string (CMX-228): markdown.ts escapes every
// character of the source and links only safe URLs, so its output is the only HTML React
// is handed raw.
import { markdownHtml, inlineHtml } from './markdown.ts';

type Props = {
  source: string;
  /** Inline markdown only (code, links, bold), in a <span>: for a title. */
  inline?: boolean;
  className?: string;
};

export function Markdown({ source, inline, className }: Props) {
  if (inline) return <span className={className} dangerouslySetInnerHTML={{ __html: inlineHtml(source) }} />;
  return <div className={className} dangerouslySetInnerHTML={{ __html: markdownHtml(source) }} />;
}
