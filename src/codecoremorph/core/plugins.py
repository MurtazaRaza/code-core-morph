"""Plugin API versions (FR-15.3, NFR-10.2).

Each kind of plugin (language adapter, LLM provider, rule pack) has an API version, a whole
number as a string. A plugin declares the version it implements: ``api_version = "1"`` on an
adapter or provider class, ``api_version: "1"`` in a rule pack's ``pack.yaml``. A registry
loads only plugins that declare a version this CodeCoreMorph supports.

Versions change only on a major release. Additions that existing plugins don't have to know
about (a new optional adapter method with a default, a new optional rule field) keep the
version; removing or changing anything a plugin implements or relies on raises it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PluginApi:
    """The API version of one kind of plugin."""

    kind: str  # "adapter", "provider" or "rule pack"
    version: str
    declare: str  # how a plugin of this kind declares it, for the message

    def problem(self, declared: object, where: str) -> str | None:
        """Why a plugin declaring ``declared`` can't load, or None when it can."""
        if declared is None:
            return (
                f"{where}: doesn't declare the {self.kind} API version it implements "
                f"(this CodeCoreMorph supports {self.version}: {self.declare})"
            )
        if declared != self.version:
            return (
                f"{where}: implements {self.kind} API {declared}, but this CodeCoreMorph "
                f"supports {self.kind} API {self.version}; install a release of the plugin "
                f"made for API {self.version}, or a CodeCoreMorph that supports API {declared}"
            )
        return None


ADAPTER_API = PluginApi("adapter", "1", 'api_version = "1" on the adapter class')
PROVIDER_API = PluginApi("provider", "1", 'api_version = "1" on the provider class')
RULE_PACK_API = PluginApi("rule pack", "1", 'api_version: "1" in pack.yaml')
