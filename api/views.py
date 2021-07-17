# chat/views.py
import logging

from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET", "POST"])
def display_name(request):
    if request.method == "POST":

        payload = request.data
        display_name = payload.get("display_name")
        request.user.display_name = display_name
        request.user.save()
        return Response(
            {
                "display_name": request.user.display_name
                or request.user.first_name + request.user.last_name
                or request.user.first_name
                or request.user.email
                or request.user.phone_number
                or request.user.username
            }
        )

    return Response(
        {
            "display_name": request.user.display_name
            or request.user.first_name + request.user.last_name
            or request.user.first_name
            or request.user.email
            or request.user.phone_number
            or request.user.username
        }
    )
