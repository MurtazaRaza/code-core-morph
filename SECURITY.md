# Security

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub's private vulnerability reporting:
the repository's **Security** tab, then **Report a vulnerability**. Don't open a public issue.

Say what an attacker controls (the repository being migrated, a plugin, the network, another
local user), what they gain, and how to reproduce it. We aim to acknowledge a report within a
week and to agree on a disclosure date with you once a fix is ready.

## Supported versions

CodeCoreMorph is in early development. Security fixes go into the latest release only.

## What CodeCoreMorph protects

CodeCoreMorph runs code you didn't write: the project's build and tests, and edits an LLM
proposed. Its design assumes that the repository it migrates, and therefore everything the
model reads, may be hostile. The full threat model is in [docs/SPEC.md §10](https://github.com/MurtazaRaza/code-core-morph/blob/master/docs/SPEC.md).

| Threat | What stops it |
|---|---|
| Builds and tests that misbehave | A Docker container by default: a non-root user, every capability dropped, `no-new-privileges`, CPU, memory and process limits, and no network while building and testing. Native mode runs on the host and says so. |
| Instructions hidden in the repository (prompt injection) | The agent has no tool that changes anything except its edit submission. Every edit is checked for its path, its scope and its effect on the tests, and has to build and pass the tests (invariants I-1 to I-12). Each system prompt and the Claude Code skill tell the agent to treat repository content as data. The branch only exists for you to review. |
| Edits that escape the worktree | PatchEngine rejects absolute paths, `..`, `.git`, symbolic links and junctions out of the worktree, Windows device names and the names Windows would rewrite, and excluded paths in any letter case. |
| Commands that run at build time | A new `execute_process`, custom command, download or `FetchContent` in a build file fails the attempt (I-12). |
| Weakened tests | Deleted or disabled tests fail the attempt (I-3); lost assertions are flagged, or need your approval (I-4). |
| Your checkout and branches | Work happens in a separate git worktree on `codecoremorph/<run-id>`. CodeCoreMorph never pushes, merges, rebases or deletes a branch it didn't create, and checks your checkout before and after each unit. |
| Leaked credentials | Keys come only from the environment or the provider SDK, are never passed into the sandbox, and are redacted from logs, transcripts, events, reports and the dashboard. Manifests that contain credentials are refused. |
| The dashboard | Listens on 127.0.0.1, with a new token at every launch, a Host allowlist, `SameSite=Strict` cookies, same-origin writes, no CORS and a content security policy. |
| The supply chain | Every download is pinned by version and SHA-256 and checked before use; Python dependencies are locked in `uv.lock`; toolchain images are pinned by digest. |

## What it doesn't protect

- **Changes within the rules.** An edit that stays in its unit's files, builds and passes the
  tests can still be wrong or unwanted, for example a line a hostile comment asked for. That is
  why every run ends on a branch for you to review, never on your own branch.
- **Native mode.** Without Docker, the project's build and tests run as you, with your files
  and network.
- **What leaves your machine.** The files of each unit go to the model provider you configure.
  A run configured with local models only (Ollama) sends nothing; the report lists the
  providers that received code.
- **Plugins.** Adapters, rule packs and providers are Python packages that run with
  CodeCoreMorph's rights. Install only those you trust.

## Security review

Each release is reviewed against [docs/SPEC.md §10](https://github.com/MurtazaRaza/code-core-morph/blob/master/docs/SPEC.md). The review for v0.1.0 (Phase
8, decision D-52) fuzzes edit paths, runs an agent that obeys every instruction hidden in the
`injection` fixture, checks the sandbox container from inside, audits where credentials are
redacted, and tests the dashboard's access checks. What it found and fixed is in D-52.
