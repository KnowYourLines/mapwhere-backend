"""
ASGI config for server project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/3.1/howto/deployment/asgi/
"""

import os

from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")
django_asgi_app = get_asgi_application()

from api.authentication import TokenAuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

import api.routing

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # Just HTTP for now. (We can add other protocols later.)
        "websocket": AllowedHostsOriginValidator(
            TokenAuthMiddlewareStack(URLRouter(api.routing.websocket_urlpatterns))
        ),
    }
)
