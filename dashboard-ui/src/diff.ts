// A unified diff (an attempt's diff.patch) split into files, and each file as the two sides a
// side-by-side diff editor shows: the hunks' old and new lines, with their real line numbers.

export interface DiffLine {
  kind: " " | "-" | "+";
  text: string;
}

export interface DiffHunk {
  oldStart: number;
  newStart: number;
  header: string;
  lines: DiffLine[];
}

export interface DiffFile {
  oldPath: string | null; // null: a new file
  newPath: string | null; // null: a deleted file
  hunks: DiffHunk[];
  binary: boolean;
  added: number;
  removed: number;
}

export interface DiffSides {
  original: string;
  modified: string;
  originalNumbers: (number | null)[]; // per displayed line; null for a gap between hunks
  modifiedNumbers: (number | null)[];
}

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/;

function stripPrefix(path: string): string | null {
  const clean = path.split("\t")[0]?.trim() ?? "";
  if (clean === "/dev/null") return null;
  return clean.replace(/^[ab]\//, "");
}

export function parseUnifiedDiff(text: string): DiffFile[] {
  const files: DiffFile[] = [];
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let oldLeft = 0;
  let newLeft = 0;

  const startFile = (): DiffFile => {
    const next: DiffFile = {
      oldPath: null,
      newPath: null,
      hunks: [],
      binary: false,
      added: 0,
      removed: 0,
    };
    files.push(next);
    hunk = null;
    return next;
  };

  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    // Inside a hunk its counts say which lines belong to it, so a removed line that starts
    // with "--" is never read as a file header.
    if (hunk && (oldLeft > 0 || newLeft > 0)) {
      const kind = line[0];
      if (kind === "\\") continue; // "\ No newline at end of file"
      if (kind === "+" && newLeft > 0) {
        hunk.lines.push({ kind: "+", text: line.slice(1) });
        newLeft -= 1;
        if (file) file.added += 1;
        continue;
      }
      if (kind === "-" && oldLeft > 0) {
        hunk.lines.push({ kind: "-", text: line.slice(1) });
        oldLeft -= 1;
        if (file) file.removed += 1;
        continue;
      }
      if ((kind === " " || line === "") && oldLeft > 0 && newLeft > 0) {
        hunk.lines.push({ kind: " ", text: line.slice(1) });
        oldLeft -= 1;
        newLeft -= 1;
        continue;
      }
      hunk = null; // a malformed hunk ends early
    }
    if (line.startsWith("\\")) continue;
    if (line.startsWith("diff --git ")) {
      file = startFile();
      const match = /^diff --git a\/(.+) b\/(.+)$/.exec(line);
      if (match) {
        file.oldPath = match[1] ?? null;
        file.newPath = match[2] ?? null;
      }
      continue;
    }
    if (line.startsWith("--- ")) {
      if (!file || file.hunks.length > 0) file = startFile();
      file.oldPath = stripPrefix(line.slice(4));
      continue;
    }
    if (line.startsWith("+++ ") && file) {
      file.newPath = stripPrefix(line.slice(4));
      continue;
    }
    if (line.startsWith("new file mode") && file) {
      file.oldPath = null;
      continue;
    }
    if (line.startsWith("deleted file mode") && file) {
      file.newPath = null;
      continue;
    }
    if (line.startsWith("Binary files ") && file) {
      file.binary = true;
      continue;
    }
    const match = HUNK.exec(line);
    if (match && file) {
      hunk = {
        oldStart: Number(match[1]),
        newStart: Number(match[3]),
        header: (match[5] ?? "").trim(),
        lines: [],
      };
      oldLeft = match[2] === undefined ? 1 : Number(match[2]);
      newLeft = match[4] === undefined ? 1 : Number(match[4]);
      file.hunks.push(hunk);
    }
  }
  return files;
}

export const GAP = "⋯";

/** The file's hunks as old and new text, a gap line between hunks. */
export function sidesOf(file: DiffFile): DiffSides {
  const original: string[] = [];
  const modified: string[] = [];
  const originalNumbers: (number | null)[] = [];
  const modifiedNumbers: (number | null)[] = [];
  file.hunks.forEach((hunk, index) => {
    if (index > 0) {
      original.push(GAP);
      modified.push(GAP);
      originalNumbers.push(null);
      modifiedNumbers.push(null);
    }
    let oldLine = hunk.oldStart;
    let newLine = hunk.newStart;
    for (const line of hunk.lines) {
      if (line.kind !== "+") {
        original.push(line.text);
        originalNumbers.push(oldLine);
        oldLine += 1;
      }
      if (line.kind !== "-") {
        modified.push(line.text);
        modifiedNumbers.push(newLine);
        newLine += 1;
      }
    }
  });
  return {
    original: original.join("\n"),
    modified: modified.join("\n"),
    originalNumbers,
    modifiedNumbers,
  };
}

export function displayPath(file: DiffFile): string {
  if (file.oldPath && file.newPath && file.oldPath !== file.newPath) {
    return `${file.oldPath} → ${file.newPath}`;
  }
  return file.newPath ?? file.oldPath ?? "(unknown file)";
}

const LANGUAGES: [RegExp, string][] = [
  [/\.(c|h)$/i, "c"],
  [/\.(cc|cpp|cxx|hh|hpp|hxx|ipp|inl|tpp)$/i, "cpp"],
  [/\.java$/i, "java"],
  [/\.cs$/i, "csharp"],
  [/\.rs$/i, "rust"],
  [/\.py$/i, "python"],
  [/\.(md|markdown)$/i, "markdown"],
  [/\.(sh|bash)$/i, "shell"],
  [/\.(ya?ml)$/i, "yaml"],
  [/\.(xml|csproj|props|targets)$/i, "xml"],
  [/\.(ini|cfg|toml)$/i, "ini"],
];

export function languageOf(path: string): string {
  return LANGUAGES.find(([pattern]) => pattern.test(path))?.[1] ?? "plaintext";
}
