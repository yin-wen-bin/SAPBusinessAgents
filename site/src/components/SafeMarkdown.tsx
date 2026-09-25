import type { ReactNode } from "react";

/** Small, deliberately non-HTML Markdown subset for untrusted assistant text. */
export default function SafeMarkdown({ text }: { text: string }) {
  const inline = (value: string): ReactNode[] => {
    const result: ReactNode[] = [];
    const source = value.replace(/!\[([^\]]*)\]\([^)]*\)/g, "[$1]");
    const pattern = /\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*/g;
    let cursor = 0;
    for (const match of source.matchAll(pattern)) {
      const position = match.index ?? 0;
      if (position > cursor) result.push(source.slice(cursor, position));
      if (match[1] !== undefined) {
        const href = match[2].trim();
        result.push(/^https?:\/\//i.test(href) || /^\/(?!\/)/.test(href)
          ? <a key={position} href={href} rel="noopener noreferrer" target="_blank">{match[1]}</a>
          : <span key={position}>{match[1]}</span>);
      } else if (match[3] !== undefined) result.push(<code key={position}>{match[3]}</code>);
      else result.push(<strong key={position}>{match[4]}</strong>);
      cursor = position + match[0].length;
    }
    if (cursor < source.length) result.push(source.slice(cursor));
    return result;
  };
  const lines = text.slice(0, 30_000).split(/\r?\n/);
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    if (line.startsWith("```")) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push(<pre key={blocks.length}><code>{code.join("\n")}</code></pre>);
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      blocks.push(<h4 key={blocks.length}>{inline(heading[2])}</h4>);
      index += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: ReactNode[] = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(<li key={items.length}>{inline(lines[index++].replace(/^\s*[-*]\s+/, ""))}</li>);
      }
      blocks.push(<ul key={blocks.length}>{items}</ul>);
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !lines[index].startsWith("```")
      && !/^\s*[-*]\s+/.test(lines[index]) && !/^#{1,3}\s+/.test(lines[index])) {
      paragraph.push(lines[index++]);
    }
    blocks.push(<p key={blocks.length}>{inline(paragraph.join(" "))}</p>);
  }
  return <div className="draft-safe-markdown">{blocks}</div>;
}
