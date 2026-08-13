class DocumentExportError(RuntimeError):
    pass


class ExportUnavailable(DocumentExportError):
    pass


class ExportTimeout(DocumentExportError):
    pass


class ExportConversionFailed(DocumentExportError):
    pass


class ExportResourceLimitExceeded(DocumentExportError, ValueError):
    pass
