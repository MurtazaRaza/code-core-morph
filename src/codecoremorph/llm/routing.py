"""Which model serves each role, and when to escalate (FR-3.7, SPEC §7.7).

Roles left unset fall back to ``editor``. With ``llm.escalation``, the ``fixer`` switches to
the escalation model once a unit has failed ``after_failed_attempts`` attempts; a model switch
starts a new conversation (append-only conversations never change model mid-way).
"""

from __future__ import annotations

from dataclasses import dataclass

from codecoremorph.config.models import LlmConfig, RoleModelConfig
from codecoremorph.core.models import Role
from codecoremorph.core.models.llm import JsonObject
from codecoremorph.llm.catalog import LOCAL_PROVIDERS


@dataclass(frozen=True)
class Route:
    role: Role
    provider: str
    model: str
    options: JsonObject
    escalated: bool = False

    @property
    def name(self) -> str:
        return f"{self.provider}/{self.model}"


def _options(config: RoleModelConfig) -> JsonObject:
    options: JsonObject = dict(config.provider_options)
    if config.effort is not None:
        options["effort"] = config.effort
    if config.num_ctx is not None:
        options["num_ctx"] = config.num_ctx
    return options


class Router:
    def __init__(self, llm: LlmConfig):
        self.llm = llm

    def route(self, role: Role, *, failed_attempts: int = 0) -> Route:
        config = self.llm.for_role(role)
        escalation = self.llm.escalation
        if (
            role is Role.FIXER
            and escalation is not None
            and failed_attempts >= escalation.after_failed_attempts
        ):
            return Route(
                role, escalation.to.provider, escalation.to.model, _options(escalation.to), True
            )
        return Route(role, config.provider, config.model, _options(config))

    def all_configs(self) -> list[RoleModelConfig]:
        configs = [self.llm.for_role(role) for role in Role]
        if self.llm.escalation is not None:
            configs.append(self.llm.escalation.to)
        return configs

    def providers(self) -> list[str]:
        return sorted({c.provider for c in self.all_configs()})

    def local_only(self) -> bool:
        """Whether every role runs on this machine, so no code leaves it (FR-8.5)."""
        return all(c.provider in LOCAL_PROVIDERS for c in self.all_configs())
