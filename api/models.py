import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField


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


class LocationBubble(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    address = models.TextField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    hours = models.PositiveIntegerField()
    minutes = models.PositiveIntegerField()

    BIKE = "bike"
    TRANSIT = "transit"
    WALK = "walk"
    CAR = "car"
    TRANSPORTATION_CHOICES = [
        (BIKE, "bike"),
        (TRANSIT, "transit"),
        (WALK, "walk"),
        (CAR, "car"),
    ]
    transportation = models.CharField(
        max_length=max([len(choice[0]) for choice in TRANSPORTATION_CHOICES]),
        choices=TRANSPORTATION_CHOICES,
    )

    def clean(self):
        if self.hours == 0 and self.minutes == 0:
            raise ValidationError(_("Total travel time cannot be zero."))


class Intersection(models.Model):
    id = models.AutoField(primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    coordinates = ArrayField(ArrayField(ArrayField(models.FloatField(), size=2)))
    TYPE = "Polygon"
    TYPE_CHOICES = [
        (TYPE, "Polygon"),
    ]
    type = models.CharField(
        max_length=max([len(choice[0]) for choice in TYPE_CHOICES]),
        choices=TYPE_CHOICES,
    )


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
    user_location = models.ForeignKey(
        User, related_name="location_user", on_delete=models.CASCADE, null=True
    )
    join_request = models.ForeignKey(JoinRequest, on_delete=models.CASCADE, null=True)
    now_private = models.BooleanField(null=True)
    now_public = models.BooleanField(null=True)

    def clean(self):
        if (
            (
                not (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.now_private
                    or self.user_location
                )
            )
            or (
                self.message
                and (
                    self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.now_private
                    or self.user_location
                )
            )
            or (
                self.user_joined
                and (
                    self.message
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.now_private
                    or self.user_location
                )
            )
            or (
                self.user_left
                and (
                    self.message
                    or self.user_joined
                    or self.join_request
                    or self.now_public
                    or self.now_private
                    or self.user_location
                )
            )
            or (
                self.join_request
                and (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.now_public
                    or self.now_private
                    or self.user_location
                )
            )
            or (
                self.now_public
                and (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_private
                    or self.user_location
                )
            )
            or (
                self.now_private
                and (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.user_location
                )
            )
            or (
                self.user_location
                and (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.now_private
                )
            )
        ):
            raise ValidationError(
                _(
                    "Notification must be for either a new message, user leaving, user joining, join request, user "
                    "location or privacy change. "
                )
            )
