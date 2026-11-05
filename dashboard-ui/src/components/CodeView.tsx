// A read-only Monaco editor over one file: one side of a port's two-sided view. Loaded lazily,
// like DiffView, whose Monaco set-up (languages, worker) it shares.
import "monaco-esm/base/browser/ui/codicons/codicon/codicon.css";
import "monaco-esm/languages/definitions/cpp/register.js";
import "monaco-esm/languages/definitions/ini/register.js";
import "monaco-esm/languages/definitions/rust/register.js";

import * as monaco from "monaco-esm/editor/editor.api.js";
import EditorWorker from "monaco-esm/editor/editor.worker.js?worker";
import { useEffect, useRef } from "react";

import type { Theme } from "../theme";

window.MonacoEnvironment = window.MonacoEnvironment ?? { getWorker: () => new EditorWorker() };

const LINE_HEIGHT = 19;

export default function CodeView({
  text,
  language,
  theme,
}: {
  text: string;
  language: string;
  theme: Theme;
}) {
  const host = useRef<HTMLDivElement>(null);
  const lines = text.split("\n").length;
  const height = Math.min(640, Math.max(4, lines + 1) * LINE_HEIGHT);

  useEffect(() => {
    if (!host.current) return;
    const model = monaco.editor.createModel(text, language);
    const editor = monaco.editor.create(host.current, {
      model,
      readOnly: true,
      automaticLayout: true,
      minimap: { enabled: false },
      scrollBeyondLastLine: false,
      overviewRulerLanes: 0,
      fontSize: 12,
      lineHeight: LINE_HEIGHT,
      theme: theme === "dark" ? "vs-dark" : "vs",
    });
    return () => {
      editor.dispose();
      model.dispose();
    };
    // The theme is applied by the effect below, without rebuilding the editor.
  }, [text, language]);

  useEffect(() => {
    monaco.editor.setTheme(theme === "dark" ? "vs-dark" : "vs");
  }, [theme]);

  return <div ref={host} className="code-editor" style={{ height }} />;
}
