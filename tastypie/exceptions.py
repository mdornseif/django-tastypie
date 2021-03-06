class TastyPieError(Exception):
    pass


class HydrationError(TastyPieError):
    pass


class NotRegistered(TastyPieError):
    pass


class URLReverseError(TastyPieError):
    pass


class NotFound(TastyPieError):
    pass


class MultipleRepresentationsFound(TastyPieError):
    pass


class ApiFieldError(TastyPieError):
    pass


class UnsupportedFormat(TastyPieError):
    pass


class BadRequest(TastyPieError):
    pass


class BlueberryFillingFound(TastyPieError):
    pass
