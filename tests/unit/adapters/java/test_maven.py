"""The Java toolchain from java.env, Maven's command line, the Surefire providers the
dependency step fetches, the mirror and OpenRewrite's arguments (D-63, D-64)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecoremorph.adapters.java import mirror, openrewrite
from codecoremorph.adapters.java.maven import JavaToolchain, maven_command, surefire_providers
from codecoremorph.core.errors import ConfigError, SandboxError
from codecoremorph.core.models import CodemodSpec

ENV = {
    "CODECOREMORPH_JDK_8": "/opt/jdk8",
    "CODECOREMORPH_JDK_21": "/opt/jdk21",
    "CODECOREMORPH_MAVEN_HOME": "/opt/maven",
    "CODECOREMORPH_MAVEN_LAUNCHER": "/opt/maven/boot/plexus-classworlds-2.11.0.jar",
    "CODECOREMORPH_MAVEN_SETTINGS": "/opt/java/settings.xml",
    "CODECOREMORPH_MAVEN_TOOLS_REPO": "/opt/java/m2",
    "CODECOREMORPH_MAVEN_DEPENDENCY_PLUGIN": (
        "org.apache.maven.plugins:maven-dependency-plugin:3.11.0"
    ),
    "CODECOREMORPH_REWRITE_PLUGIN": "org.openrewrite.maven:rewrite-maven-plugin:6.46.1",
    "CODECOREMORPH_REWRITE_RECIPES": "org.openrewrite.recipe:rewrite-migrate-java:3.42.1",
    "CODECOREMORPH_SPOON_JAR": "/opt/spoon.jar",
}


def test_maven_runs_from_its_launcher_on_the_standards_jdk() -> None:
    tools = JavaToolchain.from_env(ENV, windows=False)
    argv, env = maven_command(
        tools, "java8", project="/cache/p", repository="/cache/repo", args=["-o", "test"]
    )
    assert argv[0] == "/opt/jdk8/bin/java"
    assert env == {"JAVA_HOME": "/opt/jdk8"}
    assert "org.codehaus.plexus.classworlds.launcher.Launcher" in argv
    assert "-Dclassworlds.conf=/opt/maven/bin/m2.conf" in argv
    assert "-Dmaven.multiModuleProjectDirectory=/cache/p" in argv
    assert argv[argv.index("--settings") + 1] == "/opt/java/settings.xml"
    assert "-Dmaven.repo.local=/cache/repo" in argv
    assert "-Dmaven.repo.local.tail=/opt/java/m2" in argv
    assert argv[-4:] == ["--file", "/cache/p/pom.xml", "-o", "test"]
    outside, _ = maven_command(tools, "java21", project="/x", repository="/r", args=[], pom=False)
    assert "--file" not in outside


def test_windows_paths_and_the_newest_jdk() -> None:
    env = {**ENV, "CODECOREMORPH_JDK_21": "E:\\home\\jdk21", "CODECOREMORPH_JDK_25": "E:\\jdk25"}
    tools = JavaToolchain.from_env(env, windows=True)
    assert tools.java(tools.jdk_home("java21")) == "E:\\home\\jdk21\\bin\\java.exe"
    assert tools.newest_jdk() == "E:\\jdk25"
    assert tools.classpath_separator == ";"


def test_a_missing_jdk_or_tool_says_how_to_install() -> None:
    tools = JavaToolchain.from_env(ENV, windows=False)
    with pytest.raises(SandboxError, match="no JDK 17"):
        tools.jdk_home("java17")
    with pytest.raises(SandboxError, match="incomplete"):
        JavaToolchain.from_env({"CODECOREMORPH_JDK_8": "/x"}, windows=False)


def _artifact(repo: Path, group: str, artifact: str, version: str, *, jar: bool = True) -> None:
    directory = repo.joinpath(*group.split("."), artifact, version)
    directory.mkdir(parents=True)
    (directory / f"{artifact}-{version}.pom").write_text("<project/>", encoding="utf-8")
    if jar:
        (directory / f"{artifact}-{version}.jar").write_bytes(b"jar")


def test_the_providers_surefire_resolves_at_test_time(tmp_path: Path) -> None:
    """go-offline left out surefire-junit4 in the spike (D-63)."""
    repo = tmp_path / "repo"
    assert surefire_providers([repo]) == []
    _artifact(repo, "org.apache.maven.plugins", "maven-surefire-plugin", "3.2.5")
    _artifact(repo, "junit", "junit", "4.13.2")
    _artifact(repo, "org.junit.platform", "junit-platform-engine", "1.10.2")
    _artifact(repo, "org.apache.maven.surefire", "surefire-junit4", "3.2.5")  # already there
    assert surefire_providers([repo]) == [
        "org.apache.maven.surefire:surefire-junit-platform:3.2.5",
        "org.apache.maven.surefire:surefire-junit47:3.2.5",
        "org.junit.platform:junit-platform-launcher:1.10.2",
    ]


def test_the_mirror_follows_the_worktree_and_keeps_mavens_outputs(tmp_path: Path) -> None:
    worktree, copy = tmp_path / "wt", tmp_path / "mirror"
    (worktree / "src").mkdir(parents=True)
    (worktree / "pom.xml").write_text("<project/>", encoding="utf-8")
    (worktree / "src" / "A.java").write_text("class A {}", encoding="utf-8")
    (worktree / "src" / "B.java").write_text("class B {}", encoding="utf-8")
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    assert mirror.sync(worktree, copy) == ["pom.xml", "src/A.java", "src/B.java"]
    (copy / "target" / "classes").mkdir(parents=True)
    (copy / "target" / "classes" / "A.class").write_bytes(b"\xca\xfe")
    assert mirror.sync(worktree, copy) == []
    (worktree / "src" / "B.java").unlink()
    (worktree / "src" / "A.java").write_text("class A { int x; }", encoding="utf-8")
    assert mirror.sync(worktree, copy) == ["src/A.java", "src/B.java"]
    assert not (copy / "src" / "B.java").exists()
    assert not (copy / ".git").exists()
    assert (copy / "target" / "classes" / "A.class").is_file()
    assert mirror.output_dirs(copy) == [copy / "target"]
    mirror.clean(copy)
    assert not (copy / "target").exists()


def test_openrewrite_recipes_for_the_target() -> None:
    spec = CodemodSpec(
        tool="openrewrite",
        checks=["org.openrewrite.staticanalysis.UseDiamondOperator"],
        options={"recipe_for_java21": "org.openrewrite.java.migrate.UpgradeToJava21"},
    )
    assert openrewrite.recipes(spec, "java21") == [
        "org.openrewrite.staticanalysis.UseDiamondOperator",
        "org.openrewrite.java.migrate.UpgradeToJava21",
    ]
    assert openrewrite.recipes(spec, "java17") == [
        "org.openrewrite.staticanalysis.UseDiamondOperator"
    ]
    assert openrewrite.config(["a.B"]).endswith("recipeList:\n  - a.B\n")
    with pytest.raises(ConfigError, match="no option"):
        openrewrite.recipes(CodemodSpec(tool="openrewrite", options={"version": "21"}), "java21")
    with pytest.raises(ConfigError, match="not an OpenRewrite recipe"):
        openrewrite.recipes(CodemodSpec(tool="openrewrite", checks=["nope"]), "java21")
    args = openrewrite.run_arguments("g:a:1", "g:r:2", "/c/rewrite.yml", "/c/cache")
    assert args[0] == "g:a:1:runNoFork"
    assert "-Drewrite.pomCacheDirectory=/c/cache" in args
