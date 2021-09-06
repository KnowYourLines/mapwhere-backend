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


class PlaceType(models.Model):
    id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    PLACE_TYPE_CHOICES = [
        ("accounting", "accounting"),
        ("airport", "airport"),
        ("amusement_park", "amusement_park"),
        ("aquarium", "aquarium"),
        ("art_gallery", "art_gallery"),
        ("atm", "atm"),
        ("bakery", "bakery"),
        ("bank", "bank"),
        ("bar", "bar"),
        ("beauty_salon", "beauty_salon"),
        ("bicycle_store", "bicycle_store"),
        ("book_store", "book_store"),
        ("bowling_alley", "bowling_alley"),
        ("bus_station", "bus_station"),
        ("cafe", "cafe"),
        ("campground", "campground"),
        ("car_dealer", "car_dealer"),
        ("car_rental", "car_rental"),
        ("car_repair", "car_repair"),
        ("car_wash", "car_wash"),
        ("casino", "casino"),
        ("cemetery", "cemetery"),
        ("church", "church"),
        ("city_hall", "city_hall"),
        ("clothing_store", "clothing_store"),
        ("convenience_store", "convenience_store"),
        ("courthouse", "courthouse"),
        ("dentist", "dentist"),
        ("department_store", "department_store"),
        ("doctor", "doctor"),
        ("drugstore", "drugstore"),
        ("electrician", "electrician"),
        ("electronics_store", "electronics_store"),
        ("embassy", "embassy"),
        ("fire_station", "fire_station"),
        ("florist", "florist"),
        ("funeral_home", "funeral_home"),
        ("furniture_store", "furniture_store"),
        ("gas_station", "gas_station"),
        ("gym", "gym"),
        ("hair_care", "hair_care"),
        ("hardware_store", "hardware_store"),
        ("hindu_temple", "hindu_temple"),
        ("home_goods_store", "home_goods_store"),
        ("hospital", "hospital"),
        ("insurance_agency", "insurance_agency"),
        ("jewelry_store", "jewelry_store"),
        ("laundry", "laundry"),
        ("lawyer", "lawyer"),
        ("library", "library"),
        ("light_rail_station", "light_rail_station"),
        ("liquor_store", "liquor_store"),
        ("local_government_office", "local_government_office"),
        ("locksmith", "locksmith"),
        ("lodging", "lodging"),
        ("meal_delivery", "meal_delivery"),
        ("meal_takeaway", "meal_takeaway"),
        ("mosque", "mosque"),
        ("movie_rental", "movie_rental"),
        ("movie_theater", "movie_theater"),
        ("moving_company", "moving_company"),
        ("museum", "museum"),
        ("night_club", "night_club"),
        ("painter", "painter"),
        ("park", "park"),
        ("parking", "parking"),
        ("pet_store", "pet_store"),
        ("pharmacy", "pharmacy"),
        ("physiotherapist", "physiotherapist"),
        ("plumber", "plumber"),
        ("police", "police"),
        ("post_office", "post_office"),
        ("primary_school", "primary_school"),
        ("real_estate_agency", "real_estate_agency"),
        ("restaurant", "restaurant"),
        ("roofing_contractor", "roofing_contractor"),
        ("rv_park", "rv_park"),
        ("school", "school"),
        ("secondary_school", "secondary_school"),
        ("shoe_store", "shoe_store"),
        ("shopping_mall", "shopping_mall"),
        ("spa", "spa"),
        ("stadium", "stadium"),
        ("storage", "storage"),
        ("store", "store"),
        ("subway_station", "subway_station"),
        ("supermarket", "supermarket"),
        ("synagogue", "synagogue"),
        ("taxi_stand", "taxi_stand"),
        ("tourist_attraction", "tourist_attraction"),
        ("train_station", "train_station"),
        ("transit_station", "transit_station"),
        ("travel_agency", "travel_agency"),
        ("university", "university"),
        ("veterinary_care", "veterinary_care"),
        ("zoo", "zoo"),
    ]
    choice = models.CharField(
        max_length=max([len(choice[0]) for choice in PLACE_TYPE_CHOICES]),
        choices=PLACE_TYPE_CHOICES,
    )


class Place(models.Model):
    id = models.AutoField(primary_key=True)
    room = models.ForeignKey(Room, on_delete=models.CASCADE)
    place_id = models.TextField()
    lng = models.FloatField()
    lat = models.FloatField()


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
                )
            )
        ):
            raise ValidationError(
                _(
                    "Notification must be for either a new message, user leaving, user joining, join request, user "
                    "location, user adding place or privacy change. "
                )
            )
