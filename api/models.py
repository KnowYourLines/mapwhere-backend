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
    last_logged_in = models.DateTimeField(auto_now_add=True)


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


class AreaQuery(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    query = models.TextField()


class Place(models.Model):
    id = models.AutoField(primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    place_id = models.TextField()
    lng = models.FloatField()
    lat = models.FloatField()
    last_saved = models.DateTimeField(auto_now_add=True)


class Vote(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    place = models.ForeignKey(Place, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["room", "user"], name="one_user_vote_per_room"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.place.room == self.room:
            raise ValidationError(_("Place is not saved to room."))
        super().save(*args, **kwargs)


class LocationBubble(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    address = models.TextField()
    latitude = models.FloatField()
    longitude = models.FloatField()
    hours = models.PositiveIntegerField()
    minutes = models.PositiveIntegerField()
    place_id = models.TextField()

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

    ASIA = "asia"
    AFRICA = "africa"
    AUSTRALIA = "australia"
    BRITISH_ISLES = "britishisles"
    CENTRAL_AMERICA = "central_america"
    EASTERN_EUROPE = "easterneurope"
    NORTH_AMERICA = "northamerica"
    SOUTH_AMERICA = "south_america"
    WESTERN_EUROPE = "westcentraleurope"
    REGION_CHOICES = [
        (ASIA, "asia"),
        (AFRICA, "africa"),
        (AUSTRALIA, "australia"),
        (BRITISH_ISLES, "britishisles"),
        (CENTRAL_AMERICA, "central_america"),
        (EASTERN_EUROPE, "easterneurope"),
        (NORTH_AMERICA, "northamerica"),
        (SOUTH_AMERICA, "south_america"),
        (WESTERN_EUROPE, "westcentraleurope"),
    ]
    region = models.CharField(
        max_length=max([len(choice[0]) for choice in REGION_CHOICES]),
        choices=REGION_CHOICES,
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def clean(self):
        if self.hours == 0 and self.minutes == 0:
            raise ValidationError(_("Total travel time cannot be zero."))
        if (
            self.region in ["africa", "central_america"]
            and self.transportation == "transit"
        ):
            raise ValidationError(_(f"No transit data for {self.region}."))
        if (self.hours * 3600) + (self.minutes * 60) > 7200:
            raise ValidationError(_("Total travel time cannot exceed 2 hours."))


class Intersection(models.Model):
    id = models.AutoField(primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    coordinates = models.JSONField()
    centroid_lng = models.FloatField()
    centroid_lat = models.FloatField()
    POLYGON = "Polygon"
    MULTIPOLYGON = "MultiPolygon"
    TYPE_CHOICES = [
        (POLYGON, "Polygon"),
        (MULTIPOLYGON, "MultiPolygon"),
    ]
    type = models.CharField(
        max_length=max([len(choice[0]) for choice in TYPE_CHOICES]),
        choices=TYPE_CHOICES,
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Notification(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, null=True, blank=True
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(blank=False, default=False)
    user_joined = models.ForeignKey(
        User,
        related_name="joined_user",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    user_left = models.ForeignKey(
        User, related_name="left_user", on_delete=models.CASCADE, null=True, blank=True
    )
    user_location = models.ForeignKey(
        User,
        related_name="location_user",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    added_place = models.ForeignKey(
        User,
        related_name="added_place_user",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    voted_place = models.ForeignKey(
        User,
        related_name="voted_place_user",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    join_request = models.ForeignKey(
        JoinRequest, on_delete=models.CASCADE, null=True, blank=True
    )
    now_private = models.BooleanField(null=True, blank=True)
    now_public = models.BooleanField(null=True, blank=True)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
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
                    or self.added_place
                    or self.voted_place
                )
            )
            or (
                self.added_place
                and (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.now_private
                    or self.user_location
                    or self.voted_place
                )
            )
            or (
                self.voted_place
                and (
                    self.message
                    or self.user_joined
                    or self.user_left
                    or self.join_request
                    or self.now_public
                    or self.now_private
                    or self.user_location
                    or self.added_place
                )
            )
        ):
            raise ValidationError(
                _(
                    "Notification must be for either a new message, user leaving, user joining, join request, user "
                    "location, user adding place, voting for place, or privacy change. "
                )
            )
