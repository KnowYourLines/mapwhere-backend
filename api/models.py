import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    id = models.AutoField(primary_key=True)
    phone_regex = RegexValidator(
        regex=r"^\+?1?\d{9,15}$",
        message="Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed.",
    )
    phone_number = models.CharField(validators=[phone_regex], max_length=17, blank=True)
    display_name = models.CharField(max_length=150, blank=True)


class Room(models.Model):
    display_name = models.CharField(max_length=150, blank=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    private = models.BooleanField(blank=False, default=False)
    members = models.ManyToManyField(User)


class Message(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)


class JoinRequest(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)


class Notification(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(blank=False, default=False)
    user_joined = models.ForeignKey(
        User, related_name="joined_user", on_delete=models.CASCADE, null=True
    )
    user_left = models.ForeignKey(
        User, related_name="left_user", on_delete=models.CASCADE, null=True
    )
    join_request = models.ForeignKey(JoinRequest, on_delete=models.CASCADE, null=True)

    def clean(self):
        if (
            (
                not (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.user_join_request
                )
            )
            or (
                self.message
                and (self.user_joined or self.user_left or self.user_join_request)
            )
            or (
                self.user_joined
                and (self.message or self.user_left or self.user_join_request)
            )
            or (
                self.user_left
                and (self.message or self.user_joined or self.user_join_request)
            )
            or (
                self.user_join_request
                and (self.message or self.user_joined or self.user_left)
            )
        ):
            raise ValidationError(
                _(
                    "Notification must be for either a new message, user leaving, user joining or join request."
                )
            )
