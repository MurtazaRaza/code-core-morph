# Product Requirements Document (PRD): Polyglot Autonomous Migration Engine (PAME)

---

## 1. Executive Summary & Vision

**Polyglot Autonomous Migration Engine (PAME)** is an enterprise-grade agentic developer tool designed to automate large-scale software modernizations, language version upgrades, and cross-language migrations (e.g., C++11 to C++20, C++ FFI to Rust, Java to C#).

Unlike pure text generation tools, PAME pairs LLM-driven reasoning loops with deterministic compilers, AST parsers, and build systems to guarantee that generated code is structurally sound, compilable, and semantically compliant.

---

## 2. Core Architectural Pillars & Capabilities

```
+-------------------------------------------------------------------------+
|                        Orchestrator Core (Python)                       |
|  +---------------------+   +-------------------+   +-----------------+  |
|  | Multi-Agent Graph   |   | Global Dependency |   | Verification &  |  |
|  | (LangGraph/Custom)  |   | Symbol Graph (DAG)|   | Rollback Engine |  |
|  +----------+----------+   +---------+---------+   +--------+--------+  |
+-------------|------------------------|----------------------|-----------+
              |                        |                      |
              v                        v                      v
+-------------------------------------------------------------------------+
|                    Unified Language Adapter Protocol                    |
|  +--------------------+    +-------------------+    +----------------+  |
|  | C++ Driver         |    | C# Driver         |    | Java Driver    |  |
|  | (Clang / CMake)    |    | (Roslyn / MSBuild)|    | (Spoon / Maven)|  |
|  +--------------------+    +-------------------+    +----------------+  |
+-------------------------------------------------------------------------+
              |
              v
+-------------------------------------------------------------------------+
|                 Sandboxed Execution & Toolchain Service                 |
|       [ Isolated Containers / Ephemeral Docker / Native Tooling ]       |
+-------------------------------------------------------------------------+

```

### 2.1 Unified Language Adapter Protocol

The engine abstracts language-specific toolchains behind a uniform driver interface. Every supported language driver must implement:

* **`parse_ast(file_path)`**: Generates a normalized semantic graph / AST representation.
* **`apply_patch(diff)`**: Applies structural AST patches or diffs safely.
* **`compile_check()`**: Executes the native build toolchain and parses error outputs into structured objects (`file`, `line`, `col`, `code`, `message`).
* **`run_tests()`**: Executes test suites (GoogleTest, NUnit, JUnit, pytest) and captures failure stack traces.

### 2.2 Sandboxed Execution & Dynamic Provisioning

* **On-Demand Toolchain Fetching**: Downloads, caches, and initializes isolated container images or toolchain binaries (Clang-Tooling, Roslyn analyzers, Spoon, build tools) based on the target migration manifest.
* **Isolated Execution**: All compiler invocations and test runs occur inside isolated ephemeral environments or Docker containers to prevent host contamination.

### 2.3 Deterministic Guardrails & Verification Loops

* **Compiler-in-the-Loop Feedback**: If a build fails, raw compiler diagnostics are formatted as structured context fed back into the agent's context window.
* **Max Retry Budgets**: Limits iterative fix cycles (e.g., 3-5 retries per module).
* **Atomic Rollbacks**: Automatic git-worktree or snapshot rollbacks when an agentic loop fails or violates safety invariants.
* **Differential Testing**: Validates that legacy and refactored versions yield identical outputs for given inputs.

---

## 3. Detailed Technical Requirements

### 3.1 Functional Requirements

#### FR-1: Environment & Toolchain Provisioner

* **FR-1.1**: The system shall parse a user-provided `migration_config.yaml` specifying source language(s), target language/standard, and build systems.
* **FR-1.2**: The system shall dynamically pull and verify the required Language Adapter Pack and sandbox container image prior to execution.
* **FR-1.3**: The system shall verify local installation of required compilers or spin up lightweight container instances without manual user setup.

#### FR-2: AST & Symbol Mapping Engine

* **FR-2.1**: The driver layer shall extract semantic symbols and construct a global cross-file Directed Acyclic Graph (DAG) representing dependencies and FFI interfaces.
* **FR-2.2**: The system shall flag breaking API boundary changes across language boundaries before applying patches.

#### FR-3: Agent Execution & ReAct Cycle

* **FR-3.1**: The Orchestrator shall utilize a ReAct (Reasoning + Acting) loop state machine to sequence refactoring operations step-by-step.
* **FR-3.2**: The agent shall output standard git-compatible unified diffs before applying modifications.
* **FR-3.3**: The system shall handle multi-file changes atomically, applying patches across files sharing a symbol boundary.

#### FR-4: Self-Correction & Verification Loop

* **FR-4.1**: Upon patch application, the system shall run `compile_check()`.
* **FR-4.2**: If errors are returned, the engine shall format logs into a `CompilerFeedback` prompt payload and trigger a targeted retry.
* **FR-4.3**: Upon build success, the engine shall run unit tests and verify output parity via differential testing.

---

### 3.2 Non-Functional Requirements

#### NFR-1: Performance & Scalability

* **NFR-1.1**: AST parsing and dependency graph generation for codebases up to 100k LOC shall complete in under 60 seconds.
* **NFR-1.2**: Memory consumption of the Python Orchestrator process shall not exceed 4 GB RAM.

#### NFR-2: Safety & Reliability

* **NFR-2.1**: The system shall never modify uncommitted workspace files; all operations must run on isolated worktrees or temporary branches.
* **NFR-2.2**: Zero data loss: All failed refactoring attempts must automatically revert to the pre-attempt commit state.

#### NFR-3: Extensibility

* **NFR-3.1**: Adding support for a new target language shall only require creating a module implementing the `LanguageAdapter` interface without modifying the core orchestrator state graph.

---

## 4. API & Data Contracts

### 4.1 Language Adapter Interface (Python Abstract Class)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class CompilerError:
    file_path: str
    line: int
    column: int
    error_code: str
    message: str

@dataclass
class TestResult:
    passed: bool
    total_tests: int
    failed_tests: List[str]
    output_log: str

class LanguageAdapter(ABC):
    
    @abstractmethod
    def parse_ast(self, file_path: str) -> dict:
        """Parses the source file and returns normalized AST/Symbol metadata."""
        pass

    @abstractmethod
    def apply_patch(self, file_path: str, diff_patch: str) -> bool:
        """Applies a patch/diff safely to the target file."""
        pass

    @abstractmethod
    def compile_check(self, project_root: str) -> List[CompilerError]:
        """Triggers the compiler toolchain; returns empty list if build succeeds."""
        pass

    @abstractmethod
    def run_tests(self, project_root: str) -> TestResult:
        """Executes the project's test suite and returns structured results."""
        pass

```

---

### 4.2 Migration Manifest Schema (`migration_config.yaml`)

```yaml
version: "1.0"
project:
  name: "LegacyEngineCore"
  root_path: "./src"

migration:
  source:
    language: "cpp"
    standard: "cpp11"
    build_system: "cmake"
  target:
    language: "cpp"
    standard: "cpp20"
    rules:
      - "convert_raw_pointers_to_smart_pointers"
      - "use_concepts_for_templates"
      - "replace_typedefs_with_using"

guardrails:
  max_retry_budget: 3
  require_passing_tests: true
  rollback_on_failure: true
  docker_sandbox: true

```

---

## 5. Milestone Implementation Plan

```
Phase 1: Core Foundation (C++ Focus)
├── Implement LanguageAdapter interface
├── Build C++ Driver (Clang Tooling + CMake integration)
└── Single-agent ReAct loop for basic C++ modernizations

Phase 2: Sandboxing & Toolchain Provisioning
├── Docker / Ephemeral container isolation runner
├── Dynamic environment downloader for compilers & tools
└── Git worktree atomic rollback mechanism

Phase 3: Multi-Language & Dependency Mapping
├── Cross-language symbol DAG generator
├── C# Driver (Roslyn) / Java Driver (Spoon)
└── Multi-agent orchestration for inter-language boundaries

```

---

## 6. Prompting Guide to Expand This PRD

To expand specific sections of this document using another AI agent, use the following focused prompts:

1. **For Architecture & State Machine Design:**
> *"Using Section 2 and Section 3 of this PRD, write a detailed technical specification for the Python LangGraph state graph. Define all graph nodes, state schemas, conditional edges, and error handling loops for the compiler-in-the-loop verification mechanism."*


2. **For C++ Clang Adapter Implementation:**
> *"Act as a Principal C++ / Python Systems Engineer. Based on Section 4.1 of this PRD, implement the full `CppLanguageAdapter` class in Python using LibClang / Clang-Tidy bindings and CMake process invocations."*


3. **For Containerized Toolchain Provisioning:**
> *"Provide a complete Dockerfile and Python manager script for Section 2.2 of this PRD that dynamically pulls and sets up Clang-20, Roslyn CLI, Maven, and CMake in an isolated sandbox environment."*