import logging
import os

import firebase_admin
from firebase_admin import auth
from firebase_admin import credentials
from rest_framework import authentication

from api.models import User
from .exceptions import FirebaseError
from .exceptions import InvalidAuthToken
from .exceptions import NoAuthToken

logger = logging.getLogger(__name__)


class FirebaseAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION")
        if not auth_header:
            raise NoAuthToken("No auth token provided")

        id_token = auth_header.split(" ").pop()
        try:
            decoded_token = auth.verify_id_token(id_token)
        except Exception:
            raise InvalidAuthToken("Invalid auth token")
            pass

        if not id_token or not decoded_token:
            return None

        try:
            uid = decoded_token.get("uid")
            logger.debug(f"decoded_token: {decoded_token}")
        except Exception:
            raise FirebaseError()

        name = decoded_token.get("name")
        last_name = ""
        first_name = ""
        if name:
            split_name = name.split(" ")
            first_name = split_name[0]
            if len(split_name) > 1:
                last_name = split_name[1]

        user, created = User.objects.update_or_create(
            username=uid,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "email": decoded_token.get("email") or "",
                "phone_number": decoded_token.get("phone_number") or "",
            },
        )

        return (user, None)
