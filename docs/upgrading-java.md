# Upgrading Java

CodeCoreMorph upgrades a Maven project from one Java release to a newer one, from Java 8, 11,
17 or 21 up to Java 25, and modernizes its code on the way: the diamond operator, lambdas,
try-with-resources, `var`, switch expressions and records. As for C++, every change is built
and tested in the run's own worktree, and only verified units are committed.

The standards are the LTS releases: `java8`, `java11`, `java17`, `java21` and `java25`. A POM
that sets `1.8` is `java8`; a level between two LTS releases builds as the next one (`10` as
`java11`), and older code builds as `java8`.

## What you need

- A Maven project whose build and tests pass at the base commit, with a top-level `pom.xml`.
  Gradle comes later.
- The Java toolchain. Natively: `codecoremorph toolchain install java`, which installs a
  Temurin JDK for each standard (about 1 GB), Maven, the Spoon jar for indexing, and a Maven
  repository with OpenRewrite. In Docker: build the image once with `scripts/build-image java`
  (`.bat` on Windows).

Temurin publishes no JDK 8 for macOS on Apple silicon; there, build Java 8 projects in Docker.

## The manifest

`codecoremorph init` writes one for you. A Java 8 to 21 upgrade looks like this:

```yaml
version: "1.0"
project:
  name: legacy-ledger
  source_dirs: ["."]
  test_dirs: ["**/src/test"]          # every module's tests
migration:
  source: {language: java, standard: java8, build_system: maven}
  target:
    language: java
    standard: java21
    rules: [use_diamond_operator, use_lambdas, use_try_with_resources, use_switch_expressions,
            use_records]
```

`build.maven.configure_args` passes arguments to every Maven command, such as a profile:
`build: {maven: {configure_args: ["-Pci"]}}`.

## What a run does

1. **The bump comes first** (`bump_java_version`, automatic). OpenRewrite's migration recipe
   for the target (`UpgradeToJava21`, for example) runs on the *source's* JDK, which still has
   the APIs it replaces; then the Java level in the POMs is raised. From then on the build runs
   on the target's JDK, and the agent fixes what doesn't compile or behaves differently there:
   the Java EE modules removed in Java 11 (JAXB, JAX-WS, `javax.annotation`), JDK internals
   such as `sun.misc.BASE64Encoder`, and what the recipe changed. OpenRewrite also modernizes
   on the way (text blocks, `getFirst()`, switch expressions), so later units often have less
   to do.
2. **The modernization rules** run OpenRewrite's recipe for each (`UseDiamondOperator`,
   `UseLambdaForFunctionalInterface`, `UseTryWithResources`, `UseVar`, the switch-expression
   recipes) on the files of one unit, and the agent handles what the recipe left. When what is
   left should stay (an anonymous class that can't become a lambda, a switch that falls
   through), the agent submits no edits and says why; the unit is verified as it is.
   `use_records` has no recipe: the agent turns value classes into records, keeping the
   accessors callers use.
3. **The final verification** builds the branch from nothing and runs every test.

Each build and test run uses the JDK of the build's standard: the baseline runs on Java 8's
class library, so an API removed later breaks only after the bump, where the agent sees it.

## Builds, dependencies and the network

Maven writes `target/` beside every POM, which must never end up in a commit, so CodeCoreMorph
builds in a copy of the worktree in its cache. It never reads your `~/.m2/settings.xml`
(mirrors, credentials), and keeps each run's artifacts in a repository of the run's own.

Builds and tests run offline. Dependencies are fetched in a separate step with the network on
(`sandbox.network: fetch_only`), only when the POMs change: `dependency:go-offline`, plus the
Surefire provider for your test framework, which Maven would otherwise download only when it
runs the tests. OpenRewrite's runs also have the network, because its recipes look up the
dependencies they add. Before either turns the network on, CodeCoreMorph refuses build
extensions and `.mvn/` settings the project didn't have when the run began: Maven runs their
code while it reads the POMs.

A unit may not add plugins that run commands or download (exec, antrun, Groovy, frontend),
build extensions, repositories or annotation processors (invariant I-12).

## Tests

Surefire's reports give each test the ID `junit:<class>#<method>`, with `$` between nested
classes (`junit:org.example.TaskQueueTest$WhenEmpty#hasNoNext`); a parameterized test reports
each invocation (`describes(int, String)[1]`). JUnit 3, 4 and 5 all work. `@Ignore`,
`@Disabled` and assumptions count as disabling a test (I-3), and assertions are compared before
and after each unit (I-4).

## Limits

- Maven only, from a top-level `pom.xml`; a private repository your `settings.xml` names isn't
  used.
- A recipe that resolves the newest release of a dependency needs Maven Central.
- Characterization tests for untested Java code aren't written yet: a Java run skips that
  step, and its report says so.
