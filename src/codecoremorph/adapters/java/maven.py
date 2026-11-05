"""Maven: the toolchain the Java adapter reads from ``java.env``, its command lines, and what
the dependency step fetches besides ``dependency:go-offline`` (SPEC §11.2, D-63).

Maven starts from its own launcher class on the JDK of the build's standard, not through the
``mvn`` script: so the JDK is the one asked for, and no ``cmd.exe`` re-parses the arguments
on Windows. Every command reads CodeCoreMorph's ``settings.xml`` (never the user's), keeps
the run's artifacts in a repository of the adapter's cache, and finds the plugins the
toolchain installed in a read-only repository chained behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from codecoremorph.adapters.java.pom import STANDARDS, standard_number
from codecoremorph.core.errors import SandboxError

LAUNCHER_CLASS = "org.codehaus.plexus.classworlds.launcher.Launcher"
INSTALL_HINT = (
    "Install the pinned native toolchain with `codecoremorph toolchain install java` "
    "(or scripts/install-toolchain java), or use Docker (sandbox.mode: docker)."
)
_REQUIRED = (
    "CODECOREMORPH_MAVEN_HOME",
    "CODECOREMORPH_MAVEN_LAUNCHER",
    "CODECOREMORPH_MAVEN_SETTINGS",
    "CODECOREMORPH_MAVEN_TOOLS_REPO",
    "CODECOREMORPH_MAVEN_DEPENDENCY_PLUGIN",
    "CODECOREMORPH_REWRITE_PLUGIN",
    "CODECOREMORPH_REWRITE_RECIPES",
    "CODECOREMORPH_SPOON_JAR",
)


@dataclass(frozen=True)
class JavaToolchain:
    """The toolchain's paths as the sandbox sees them, from ``java.env``."""

    jdks: dict[int, str]  # standard number -> JDK home
    maven_home: str
    launcher: str
    settings: str
    tools_repo: str
    dependency_plugin: str  # groupId:artifactId:version
    rewrite_plugin: str
    rewrite_recipes: str  # comma-separated coordinates
    spoon_jar: str
    windows: bool  # paths are Windows paths (native mode on Windows)

    @classmethod
    def from_env(cls, env: dict[str, str], *, windows: bool) -> JavaToolchain:
        missing = [name for name in _REQUIRED if not env.get(name)]
        jdks = {
            standard_number(s): env[f"CODECOREMORPH_JDK_{standard_number(s)}"]
            for s in STANDARDS
            if env.get(f"CODECOREMORPH_JDK_{standard_number(s)}")
        }
        if missing or not jdks:
            raise SandboxError(
                "the Java toolchain environment is incomplete",
                reason="java.env doesn't set " + ", ".join(missing or ["any JDK"]),
                hint=INSTALL_HINT,
            )
        return cls(
            jdks=jdks,
            maven_home=env["CODECOREMORPH_MAVEN_HOME"],
            launcher=env["CODECOREMORPH_MAVEN_LAUNCHER"],
            settings=env["CODECOREMORPH_MAVEN_SETTINGS"],
            tools_repo=env["CODECOREMORPH_MAVEN_TOOLS_REPO"],
            dependency_plugin=env["CODECOREMORPH_MAVEN_DEPENDENCY_PLUGIN"],
            rewrite_plugin=env["CODECOREMORPH_REWRITE_PLUGIN"],
            rewrite_recipes=env["CODECOREMORPH_REWRITE_RECIPES"],
            spoon_jar=env["CODECOREMORPH_SPOON_JAR"],
            windows=windows,
        )

    def join(self, base: str, *parts: str) -> str:
        path = PureWindowsPath(base) if self.windows else PurePosixPath(base)
        return str(path.joinpath(*parts))

    def jdk_home(self, standard: str) -> str:
        number = standard_number(standard)
        home = self.jdks.get(number)
        if home is None:
            raise SandboxError(
                f"the Java toolchain has no JDK {number}, which builds {standard} code",
                reason="java.env names no CODECOREMORPH_JDK_" + str(number),
                hint="Temurin publishes no JDK 8 for macOS on Apple silicon; build in Docker "
                "(sandbox.mode: docker). Otherwise reinstall: " + INSTALL_HINT,
            )
        return home

    def java(self, home: str) -> str:
        return self.join(home, "bin", "java.exe" if self.windows else "java")

    def javac(self, home: str) -> str:
        return self.join(home, "bin", "javac.exe" if self.windows else "javac")

    def newest_jdk(self, minimum: int = 17) -> str:
        """The newest JDK's home (the index helper's: Spoon needs 17 or newer)."""
        number = max(self.jdks)
        if number < minimum:
            raise SandboxError(
                f"the Java toolchain has no JDK {minimum} or newer", hint=INSTALL_HINT
            )
        return self.jdks[number]

    @property
    def classpath_separator(self) -> str:
        return ";" if self.windows else ":"


def maven_command(
    tools: JavaToolchain,
    standard: str,
    *,
    project: str,
    repository: str,
    args: list[str],
    pom: bool = True,
) -> tuple[list[str], dict[str, str]]:
    """The argv and environment of a Maven run on ``standard``'s JDK, in ``project`` (the
    mirror; ``pom=False`` for a run outside any project, which reads no POM)."""
    home = tools.jdk_home(standard)
    argv = [
        tools.java(home),
        "-classpath",
        tools.launcher,
        f"-Dclassworlds.conf={tools.join(tools.maven_home, 'bin', 'm2.conf')}",
        f"-Dmaven.home={tools.maven_home}",
        f"-Dlibrary.jansi.path={tools.join(tools.maven_home, 'lib', 'jansi-native')}",
        f"-Dmaven.multiModuleProjectDirectory={project}",
        LAUNCHER_CLASS,
        "--batch-mode",
        "--no-transfer-progress",
        "-Dstyle.color=never",
        "--settings",
        tools.settings,
        f"-Dmaven.repo.local={repository}",
        f"-Dmaven.repo.local.tail={tools.tools_repo}",
    ]
    if pom:
        argv += ["--file", tools.join(project, "pom.xml")]
    return [*argv, *args], {"JAVA_HOME": home}


def _artifact_path(repository: Path, group: str, artifact: str, version: str) -> Path:
    return repository.joinpath(*group.split("."), artifact, version, f"{artifact}-{version}.jar")


def _versions(repositories: list[Path], group: str, artifact: str) -> set[str]:
    found: set[str] = set()
    for repository in repositories:
        directory = repository.joinpath(*group.split("."), artifact)
        if directory.is_dir():
            found |= {
                entry.name
                for entry in directory.iterdir()
                if entry.is_dir() and (entry / f"{artifact}-{entry.name}.pom").is_file()
            }
    return found


def surefire_providers(repositories: list[Path]) -> list[str]:
    """The artifacts Surefire resolves only when it runs the tests, which
    ``dependency:go-offline`` leaves out (the spike's offline run failed on
    ``surefire-junit4``, D-63): for each Surefire release the project's build downloaded,
    the providers of the test frameworks it downloaded, and the JUnit Platform launcher of
    each platform release. ``repositories`` are host paths, the run's own first; only the
    artifacts none of them holds are listed."""
    surefire = _versions(repositories, "org.apache.maven.plugins", "maven-surefire-plugin")
    if not surefire:
        return []
    wanted: list[tuple[str, str, str]] = []
    platform = _versions(repositories, "org.junit.platform", "junit-platform-engine")
    junit = _versions(repositories, "junit", "junit")
    testng = _versions(repositories, "org.testng", "testng")
    for version in sorted(surefire):
        if platform:
            wanted.append(("org.apache.maven.surefire", "surefire-junit-platform", version))
        if any(v.startswith("4.") for v in junit):
            wanted.append(("org.apache.maven.surefire", "surefire-junit4", version))
            wanted.append(("org.apache.maven.surefire", "surefire-junit47", version))
        if any(v.startswith("3.") for v in junit):
            wanted.append(("org.apache.maven.surefire", "surefire-junit3", version))
        if testng:
            wanted.append(("org.apache.maven.surefire", "surefire-testng", version))
    for version in sorted(platform):
        wanted.append(("org.junit.platform", "junit-platform-launcher", version))
    return [
        f"{group}:{artifact}:{version}"
        for group, artifact, version in wanted
        if not any(_artifact_path(r, group, artifact, version).is_file() for r in repositories)
    ]
