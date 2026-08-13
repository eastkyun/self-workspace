class JobPlatformError(RuntimeError):
    """Base class for stable Job Platform failures."""


class JobNotFound(JobPlatformError):
    pass


class JobConflict(JobPlatformError):
    pass


class JobValidationError(JobPlatformError):
    pass


class LeaseLost(JobConflict):
    pass
