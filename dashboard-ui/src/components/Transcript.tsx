import type { Diagnostic, TranscriptBlock, TranscriptEntry } from "../types";

const LONG = 1200;

function Long({ text, label }: { text: string; label: string }) {
  if (text.length <= LONG) return <pre>{text}</pre>;
  return (
    <details>
      <summary>
        {label} ({text.length.toLocaleString("en-US")} characters)
      </summary>
      <pre>{text}</pre>
    </details>
  );
}

function Block({ block }: { block: TranscriptBlock }) {
  switch (block.type) {
    case "text":
      return <pre>{block.text ?? ""}</pre>;
    case "tool_call":
      return (
        <>
          <div className="tool">→ {block.name}</div>
          <Long text={JSON.stringify(block.arguments ?? {}, null, 2)} label="arguments" />
        </>
      );
    case "tool_result":
      return (
        <>
          <div className="tool">
            ← {block.name}
            {block.is_error ? <span className="badge tone-bad">error</span> : null}
          </div>
          <Long text={block.content ?? ""} label="result" />
        </>
      );
    case "opaque":
      return (
        <details>
          <summary>{block.kind ?? "provider block"}</summary>
          <pre>{block.text ?? ""}</pre>
        </details>
      );
    default:
      return <pre>{JSON.stringify(block, null, 2)}</pre>;
  }
}

export function Transcript({ entries, truncated }: { entries: TranscriptEntry[]; truncated: boolean }) {
  if (entries.length === 0) return <p className="muted">The transcript is empty.</p>;
  return (
    <div>
      {entries.map((entry, index) => (
        <article className="message" key={index}>
          <header>
            <strong>{entry.role === "assistant" ? "model" : "CodeCoreMorph"}</strong>
            {entry.model ? <span className="muted mono">{entry.model}</span> : null}
            {entry.usage ? (
              <span className="muted">
                {Object.entries(entry.usage)
                  .map(([k, v]) => `${k.replaceAll("_", " ")} ${v.toLocaleString("en-US")}`)
                  .join(" · ")}
              </span>
            ) : null}
          </header>
          {entry.content.map((block, i) => (
            <div className="block" key={i}>
              <Block block={block} />
            </div>
          ))}
        </article>
      ))}
      {truncated ? <p className="muted">The transcript is longer; only its start is shown.</p> : null}
    </div>
  );
}

function location(d: Diagnostic): string {
  if (!d.file_path) return "";
  return [d.file_path, d.line, d.column].filter((p) => p !== null).join(":");
}

export function Diagnostics({ diagnostics }: { diagnostics: Diagnostic[] }) {
  if (diagnostics.length === 0) return <p className="muted">No compiler messages.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Severity</th>
            <th>Where</th>
            <th>Message</th>
          </tr>
        </thead>
        <tbody>
          {diagnostics.map((d, i) => (
            <tr key={i}>
              <td>
                <span
                  className={`badge tone-${d.severity === "error" ? "bad" : d.severity === "warning" ? "wait" : "idle"}`}
                >
                  {d.severity}
                </span>
              </td>
              <td className="mono">{location(d) || d.origin}</td>
              <td>
                <pre>{d.message}</pre>
                {d.code ? <span className="chip">{d.code}</span> : null}
                {d.notes.map((note, j) => (
                  <div key={j} className="muted">
                    <span className="mono">{location(note)}</span> note: {note.message}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
