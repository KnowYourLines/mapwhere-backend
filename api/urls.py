# chat/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path("display_name/", views.display_name, name="display_name"),
]
