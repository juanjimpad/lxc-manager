"""Domain errors shared by module services. HTTP adapters map these to status codes."""


class GuestNotFound(Exception):
    pass


class InvalidCron(Exception):
    pass


class SelfUpdateDisabled(Exception):
    pass


class SelfUpdateBusy(Exception):
    pass


class InvalidRelease(Exception):
    pass


class NotNewer(Exception):
    pass
