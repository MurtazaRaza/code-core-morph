"""POMs as text: Java levels, the bump, modules, extensions, source roots (SPEC §11.2, D-63)."""

from __future__ import annotations

import pytest

from codecoremorph.adapters.java.pom import (
    bump_java_level,
    extensions,
    java_levels,
    level_text,
    lowest_standard,
    modules,
    source_roots,
    standard_for_level,
)

POM = """\
<project>
  <!-- <maven.compiler.source>1.6</maven.compiler.source> is an old comment -->
  <properties>
    <java.version>1.8</java.version>
    <maven.compiler.source>${java.version}</maven.compiler.source>
    <maven.compiler.target>${java.version}</maven.compiler.target>
  </properties>
  <modules>
    <module>core</module>
    <module> app </module>
  </modules>
  <build>
    <plugins>
      <plugin>
        <artifactId>maven-compiler-plugin</artifactId>
        <configuration>
          <source>1.7</source>
          <testSource>${java.version}</testSource>
        </configuration>
      </plugin>
      <plugin>
        <artifactId>maven-source-plugin</artifactId>
        <configuration><source>keep</source></configuration>
      </plugin>
    </plugins>
  </build>
</project>
"""


@pytest.mark.parametrize(
    ("level", "standard"),
    [
        ("1.8", "java8"),
        ("8", "java8"),
        ("1.6", "java8"),  # older code builds on JDK 8
        ("10", "java11"),  # between two LTS releases: the next one
        ("17", "java17"),
        ("22", "java25"),
        ("26", None),
        ("${java.version}", None),
    ],
)
def test_a_level_names_a_standard(level: str, standard: str | None) -> None:
    assert standard_for_level(level) == standard


def test_levels_are_text_as_poms_write_them() -> None:
    assert level_text("java8") == "1.8"
    assert level_text("java21") == "21"


def test_levels_resolve_the_poms_own_properties_and_skip_comments() -> None:
    assert java_levels(POM) == ["1.8", "1.8", "1.8", "1.7", "1.8"]
    assert lowest_standard([POM, "<project/>"]) == "java8"
    assert lowest_standard(["<project/>"]) is None


def test_the_bump_raises_literals_and_leaves_property_references() -> None:
    edit = bump_java_level(POM, "java21", top_level=True)
    assert edit.replacements == 2
    assert "<java.version>21</java.version>" in edit.text
    assert "<source>21</source>" in edit.text
    assert "<maven.compiler.source>${java.version}</maven.compiler.source>" in edit.text
    assert "<source>keep</source>" in edit.text  # another plugin's <source>
    assert "<!-- <maven.compiler.source>1.6</maven.compiler.source>" in edit.text
    assert java_levels(edit.text) == ["21", "21", "21", "21", "21"]


def test_the_bump_never_lowers_a_level() -> None:
    text = (
        "<project><properties><maven.compiler.release>25</maven.compiler.release>"
        "</properties></project>"
    )
    assert bump_java_level(text, "java21", top_level=True).replacements == 0


def test_a_top_level_pom_without_a_level_gets_one() -> None:
    crlf = "<project>\r\n  <properties>\r\n    <x>1</x>\r\n  </properties>\r\n</project>\r\n"
    edit = bump_java_level(crlf, "java17", top_level=True)
    assert edit.replacements == 2
    assert "    <maven.compiler.source>17</maven.compiler.source>\r\n" in edit.text
    assert edit.text.endswith("  </properties>\r\n</project>\r\n")
    bare = bump_java_level("<project>\n  <x/>\n</project>\n", "java11", top_level=True)
    assert "<properties>" in bare.text
    assert java_levels(bare.text) == ["11", "11"]
    # A module inherits its parent's level, so it gets nothing.
    assert bump_java_level("<project/>", "java11", top_level=False).replacements == 0


def test_modules_extensions_and_source_roots() -> None:
    assert modules(POM) == ["core", "app"]
    plugin = (
        "<project><build><extensions><extension><groupId>kr.motd.maven</groupId>"
        "</extension></extensions><plugins><plugin><extensions>true</extensions>"
        "</plugin></plugins></build></project>"
    )
    assert len(extensions(plugin)) == 2
    assert extensions("<project><!-- <extensions>true</extensions> --></project>") == []
    helper = """<project><build><sourceDirectory>${project.basedir}/main</sourceDirectory><plugins>
      <plugin><artifactId>build-helper-maven-plugin</artifactId>
        <configuration><sources><source>tests/codecoremorph_characterization</source></sources>
      </configuration></plugin></plugins></build></project>"""
    assert source_roots(helper) == [
        "src/main/java",
        "src/test/java",
        "main",
        "tests/codecoremorph_characterization",
    ]
