// Monaco's side-by-side diff editor over one file's hunks. Loaded lazily (it is most of the
// bundle) and with only the languages migrations touch; nothing is fetched from a CDN.
import "monaco-esm/base/browser/ui/codicons/codicon/codicon.css";
import "monaco-esm/languages/definitions/cpp/register.js";
import "monaco-esm/languages/definitions/csharp/register.js";
import "monaco-esm/languages/definitions/ini/register.js";
import "monaco-esm/languages/definitions/java/register.js";
import "monaco-esm/languages/definitions/markdown/register.js";
import "monaco-esm/languages/definitions/python/register.js";
import "monaco-esm/languages/definitions/rust/register.js";
import "monaco-esm/languages/definitions/shell/register.js";
import "monaco-esm/languages/definitions/xml/register.js";
import "monaco-esm/languages/definitions/yaml/register.js";

import * as monaco from "monaco-esm/editor/editor.api.js";
import EditorWorker from "monaco-esm/editor/editor.worker.js?worker";
import { useEffect, useRef } from "react";

import { type DiffSides, GAP } from "../diff";
import type { Theme } from "../theme";

declare global {
  interface Window {
    MonacoEnvironment?: { getWorker: (workerId: string, label: string) => Worker };
  }
}

window.MonacoEnvironment = { getWorker: () => new EditorWorker() };

const LINE_HEIGHT = 19;

function numbers(list: (number | null)[]) {
  return (line: number) => {
    const value = list[line - 1];
    return value === null ? GAP : value === undefined ? "" : String(value);
  };
}

export default function DiffView({
  sides,
  language,
  theme,
}: {
  sides: DiffSides;
  language: string;
  theme: Theme;
}) {
  const host = useRef<HTMLDivElement>(null);
  const lines = Math.max(sides.originalNumbers.length, sides.modifiedNumbers.length);
  const height = Math.min(640, Math.max(4, lines + 2) * LINE_HEIGHT);

  useEffect(() => {
    if (!host.current) return;
    const editor = monaco.editor.createDiffEditor(host.current, {
      readOnly: true,
      originalEditable: false,
      automaticLayout: true,
      renderSideBySide: true,
      useInlineViewWhenSpaceIsLimited: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      renderOverviewRuler: false,
      fontSize: 12,
      lineHeight: LINE_HEIGHT,
      theme: theme === "dark" ? "vs-dark" : "vs",
    });
    const original = monaco.editor.createModel(sides.original, language);
    const modified = monaco.editor.createModel(sides.modified, language);
    editor.setModel({ original, modified });
    editor.getOriginalEditor().updateOptions({ lineNumbers: numbers(sides.originalNumbers) });
    editor.getModifiedEditor().updateOptions({ lineNumbers: numbers(sides.modifiedNumbers) });
    return () => {
      editor.dispose();
      original.dispose();
      modified.dispose();
    };
    // The theme is applied by the effect below, without rebuilding the editor.
  }, [sides, language]);

  useEffect(() => {
    monaco.editor.setTheme(theme === "dark" ? "vs-dark" : "vs");
  }, [theme]);

  return <div ref={host} className="diff-editor" style={{ height }} />;
}
