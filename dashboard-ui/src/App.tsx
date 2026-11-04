import { useEffect } from "react";

import { useSession } from "./api";
import { Link, paths, useRoute } from "./router";
import { RunPage } from "./pages/RunPage";
import { RunsPage } from "./pages/RunsPage";
import { UnitPage } from "./pages/UnitPage";
import { useTheme } from "./theme";

export function App() {
  const route = useRoute();
  const [theme, toggleTheme] = useTheme();
  const session = useSession();
  const project = session.data?.project;

  useEffect(() => {
    const where =
      route.name === "run" ? route.runId : route.name === "unit" ? `${route.unitId} · ${route.runId}` : null;
    document.title = ["CodeCoreMorph", project, where].filter(Boolean).join(" · ");
  }, [route, project]);

  return (
    <>
      <header className="topbar">
        <Link to={paths.runs()} className="brand">
          <img src="/favicon.svg" alt="" />
          CodeCoreMorph
        </Link>
        {project ? <span className="project">{project}</span> : null}
        <span className="spacer" />
        {session.data ? <span className="muted mono">v{session.data.version}</span> : null}
        <button onClick={toggleTheme} aria-label={`Use the ${theme === "dark" ? "light" : "dark"} theme`}>
          {theme === "dark" ? "Light" : "Dark"}
        </button>
      </header>
      <main>
        {route.name === "runs" ? <RunsPage /> : null}
        {route.name === "run" ? <RunPage key={route.runId} runId={route.runId} tab={route.tab} theme={theme} /> : null}
        {route.name === "unit" ? (
          <UnitPage
            key={`${route.runId}/${route.unitId}`}
            runId={route.runId}
            unitId={route.unitId}
            attempt={route.attempt}
            theme={theme}
          />
        ) : null}
        {route.name === "notFound" ? (
          <div className="card empty">
            <h2>There is no page {route.path}</h2>
            <p>
              <Link to={paths.runs()}>See the runs</Link>
            </p>
          </div>
        ) : null}
      </main>
    </>
  );
}
