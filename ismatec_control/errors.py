"""Errors shared by the serial controller, local API, and clients."""


class PumpError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **self.details}
