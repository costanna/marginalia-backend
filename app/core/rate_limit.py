from fastapi import Request
from slowapi import Limiter

from app.core.config import get_settings


def client_ip(request: Request) -> str:
    """The visitor's IP address, used as the rate-limit key.

    Behind a reverse proxy the socket peer is the proxy, not the visitor, so every visitor would
    share one bucket. The proxy appends the address it saw to X-Forwarded-For, but a client can
    also send its own X-Forwarded-For, so only the entries added by OUR proxies are trusted: with
    N trusted proxies the visitor is the Nth entry counting from the END. With none configured
    the header is ignored entirely, which stops trivial spoofing.
    """
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        forwarded = [
            part.strip()
            for part in request.headers.get("x-forwarded-for", "").split(",")
            if part.strip()
        ]
        if len(forwarded) >= hops:
            return forwarded[-hops]
        # Fewer entries than proxies: the header did not come through our proxy chain.
    return request.client.host if request.client else "unknown"


# In-memory storage: counters live in this process and reset on restart. That is enough for a
# single-instance deployment; several instances would need a shared store such as Redis.
limiter = Limiter(key_func=client_ip, storage_uri="memory://")
