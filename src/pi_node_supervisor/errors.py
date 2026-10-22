"""Stable application error types."""


class SupervisorError(RuntimeError):
    """Base class for errors that can be presented to an operator."""


class PlanningError(SupervisorError):
    """The requested activities cannot produce a safe plan."""


class PersistenceError(SupervisorError):
    """A draft or revision could not be persisted safely."""


class ApplyError(SupervisorError):
    """A candidate revision could not be activated."""


class ManualOperationUnavailable(SupervisorError):
    """A manual operation cannot safely override the pending power handoff."""
