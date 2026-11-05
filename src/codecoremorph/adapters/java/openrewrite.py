"""OpenRewrite codemods (SPEC §11.2, D-63): a rule's recipes, run by OpenRewrite's Maven plugin
in the mirror, of which only the unit's files are kept.

A codemod's ``checks`` are recipe names; its options name a recipe for a migration target
(``recipe_for_java21: org.openrewrite.java.migrate.UpgradeToJava21``), which runs only when
that is the target. The recipes go into a composite recipe in a ``rewrite.yml`` of the cache,
so the run names one active recipe. ``rewrite:runNoFork`` changes the files without compiling
first (the ``dryRun`` goals compile, which a Java 8 project can't do on a newer JDK).
"""

from __future__ import annotations

import re

from codecoremorph.core.errors import ConfigError
from codecoremorph.core.models import CodemodSpec

COMPOSITE = "org.codecoremorph.Codemod"
OPTION_PREFIX = "recipe_for_"
_RECIPE = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)+$")


def recipes(spec: CodemodSpec, target: str) -> list[str]:
    """The recipes a codemod runs for the migration's ``target`` standard."""
    unknown = [key for key in spec.options if not key.startswith(OPTION_PREFIX)]
    if unknown:
        raise ConfigError(
            f"the openrewrite codemod has no option {unknown[0]!r}",
            hint=f"Its options name a recipe per target: {OPTION_PREFIX}java21: <recipe>.",
        )
    names = list(spec.checks)
    chosen = spec.options.get(OPTION_PREFIX + target)
    if chosen:
        names.append(chosen)
    bad = [n for n in names if not _RECIPE.match(n)]
    if bad:
        raise ConfigError(f"{bad[0]!r} is not an OpenRewrite recipe name")
    return names


def config(names: list[str]) -> str:
    """The ``rewrite.yml`` holding the composite recipe."""
    lines = [
        "type: specs.openrewrite.org/v1beta/recipe",
        f"name: {COMPOSITE}",
        "displayName: CodeCoreMorph codemod",
        "recipeList:",
        *[f"  - {name}" for name in names],
    ]
    return "\n".join(lines) + "\n"


def run_arguments(
    plugin: str, recipe_artifacts: str, config_path: str, cache_dir: str
) -> list[str]:
    """Maven's arguments for the run (``plugin`` is the plugin's coordinates)."""
    return [
        f"{plugin}:runNoFork",
        f"-Drewrite.activeRecipes={COMPOSITE}",
        f"-Drewrite.configLocation={config_path}",
        f"-Drewrite.recipeArtifactCoordinates={recipe_artifacts}",
        "-Drewrite.exportDatatables=false",
        "-Drewrite.failOnInvalidActiveRecipes=true",
        # Its POM cache would go to ~/.rewrite-cache otherwise.
        f"-Drewrite.pomCacheDirectory={cache_dir}",
    ]
