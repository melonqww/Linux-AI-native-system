"""Small deterministic state machine that plays a journey user."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import ActionKind, JourneyAction, JourneySpec, Observation


class StopReason(StrEnum):
    POLICY_STOP = "policy_stop"
    TERMINAL_STATUS = "terminal_status"
    MAX_TURNS = "max_turns"
    NO_TRANSITION = "no_transition"


@dataclass(frozen=True)
class StepDecision:
    state_id: str
    action: JourneyAction | None
    stopped: bool
    reason: StopReason | None = None


class JourneySession:
    """Stateful controller; all decisions are reproducible from spec + observations."""

    def __init__(self, spec: JourneySpec):
        self.spec = spec
        self.state_id = spec.initial_state
        self.turns_emitted = 0
        self.stopped = False

    def start(self) -> StepDecision:
        if self.turns_emitted:
            raise RuntimeError("journey has already started")
        return self._emit_current()

    def react(self, observation: Observation) -> StepDecision:
        if self.stopped:
            raise RuntimeError("journey has stopped")
        current = self.spec.states[self.state_id]
        matching = [
            item for item in current.transitions if item.when.matches(observation)
        ]
        if len(matching) > 1:
            raise RuntimeError(f"ambiguous transitions from state {self.state_id}")
        if matching:
            if self.turns_emitted >= self.spec.max_turns:
                self.stopped = True
                return StepDecision(self.state_id, None, True, StopReason.MAX_TURNS)
            self.state_id = matching[0].target
            return self._emit_current()
        if observation.status in self.spec.goal.terminal_statuses:
            self.stopped = True
            return StepDecision(self.state_id, None, True, StopReason.TERMINAL_STATUS)
        if self.turns_emitted >= self.spec.max_turns:
            self.stopped = True
            return StepDecision(self.state_id, None, True, StopReason.MAX_TURNS)
        self.stopped = True
        return StepDecision(self.state_id, None, True, StopReason.NO_TRANSITION)

    def _emit_current(self) -> StepDecision:
        state = self.spec.states[self.state_id]
        if state.action.kind is ActionKind.STOP:
            self.stopped = True
            return StepDecision(self.state_id, None, True, StopReason.POLICY_STOP)
        if self.turns_emitted >= self.spec.max_turns:
            self.stopped = True
            return StepDecision(self.state_id, None, True, StopReason.MAX_TURNS)
        self.turns_emitted += 1
        return StepDecision(self.state_id, state.action, False)
