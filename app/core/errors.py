"""Domain errors shared by module services. HTTP adapters map these to status codes."""


class GuestNotFound(Exception):
    pass


class InvalidCron(Exception):
    pass
