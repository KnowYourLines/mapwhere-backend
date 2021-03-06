import datetime
import os
from operator import itemgetter

import asyncio
import json
import logging

import aiohttp as aiohttp
import requests as requests
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db.models import Count, Case, When, BooleanField
from shapely.geometry import MultiPolygon, Polygon, Point

from api.models import (
    Message,
    Room,
    User,
    JoinRequest,
    Notification,
    LocationBubble,
    Intersection,
    Place,
    AreaQuery,
    Vote,
)

logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    def messages_to_json(self, messages):
        result = []
        for message in messages:
            result.append(self.message_to_json(message))
        return result[::-1]

    def message_to_json(self, message):
        return {
            "display_name": message.user.display_name
            or message.user.get_full_name()
            or message.user.email
            or message.user.phone_number
            or message.user.username,
            "content": message.content,
            "timestamp": str(message.timestamp),
        }

    def update_user_last_logged_in_timestamp(self):
        self.user.last_logged_in = datetime.datetime.utcnow()

    @staticmethod
    def update_place_last_saved_timestamp(place):
        place.last_saved = datetime.datetime.utcnow()
        place.save()

    async def connect(self):
        self.room_group_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room = await database_sync_to_async(self.get_room)(self.room_group_name)
        self.user = self.scope["user"]
        await database_sync_to_async(self.update_user_last_logged_in_timestamp)()

        # Join room group
        await self.channel_layer.group_add(str(self.room.id), self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    def fetch_messages(self):
        try:
            messages = self.room.message_set.order_by("-timestamp")[:10]
            for message in self.messages_to_json(messages):
                async_to_sync(self.channel_layer.send)(
                    self.channel_name,
                    {
                        "type": "fetching_message",
                        "message": f"{message['display_name']}: {message['content']}",
                    },
                )
        except Message.DoesNotExist:
            pass

    def update_location_bubble(
        self,
        address,
        latitude,
        longitude,
        transportation,
        hours,
        minutes,
        region,
        place_id,
    ):
        location_bubble, created = LocationBubble.objects.update_or_create(
            user=self.user,
            room=self.room,
            defaults={
                "address": address,
                "latitude": latitude,
                "longitude": longitude,
                "transportation": transportation,
                "hours": hours,
                "minutes": minutes,
                "region": region,
                "place_id": place_id,
            },
        )
        self.update_user_location_notification()
        return created

    def update_area_query(self, query):
        AreaQuery.objects.update_or_create(
            user=self.user,
            room=self.room,
            defaults={
                "query": query,
            },
        )

    def save_place(self, place_id, lat, lng, *, update_timestamp=True):
        place, created = Place.objects.update_or_create(
            room=self.room,
            lng=lng,
            lat=lat,
            defaults={
                "place_id": place_id,
            },
        )
        self.added_place_notification()
        if update_timestamp:
            self.update_place_last_saved_timestamp(place)

    def fetch_places(self):
        places = list(
            self.room.place_set.values("place_id", "lat", "lng")
            .annotate(total_votes=Count("vote"))
            .annotate(
                user_voted_for=Case(
                    When(vote__user=self.user, then=True),
                    default=False,
                    output_field=BooleanField(),
                )
            )
            .order_by(
                "-user_voted_for",
                "-total_votes",
                "-last_saved",
            )[:10]
        )
        skip_index = None
        if len(places) > 0 and places[0]["user_voted_for"]:
            for index, place in enumerate(places[1:]):
                if place["place_id"] == places[0]["place_id"]:
                    places[0]["total_votes"] += place["total_votes"]
                    skip_index = index + 1
        if skip_index:
            return places[:skip_index] + places[skip_index + 1 :]
        return places

    def update_display_name(self, new_name):
        self.user.display_name = new_name
        self.user.save()

    def update_room_name(self, new_name):
        room = self.room
        room.display_name = new_name
        room.save()
        return room

    def update_privacy(self, private):
        self.room.private = private
        self.room.save()
        if self.room.private:
            self.create_privacy_notification_for_going_private()
        else:
            self.create_privacy_notification_for_going_public()

    async def fetch_display_name(self):
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "display_name",
                "new_display_name": f"{self.user.display_name or self.user.get_full_name() or self.user.email or self.user.phone_number or self.user.username}",
            },
        )

    def get_user_location_bubble_for_room(self):
        location_bubbles = self.user.locationbubble_set.filter(room=self.room)

        if len(location_bubbles) > 1:
            logger.info(
                f"Got multiple location bubbles for user {self.user.uid} in room {self.room.id}"
            )
        if len(location_bubbles) > 0:
            location = location_bubbles.first()
            url = (
                f"https://maps.googleapis.com/maps/api/place/details/json?place_id={location.place_id}&"
                f"fields=place_id&key={os.environ.get('MAPS_API_KEY')}"
            )
            refreshed_place_id = requests.get(url).json()["result"]["place_id"]
            if refreshed_place_id != location.place_id:
                location.place_id = refreshed_place_id
                location.save()
            return {
                "address": location.address,
                "latitude": location.latitude,
                "longitude": location.longitude,
                "hours": location.hours,
                "minutes": location.minutes,
                "transportation": location.transportation,
                "region": location.region,
                "place_id": location.place_id,
            }
        return {}

    async def fetch_location_bubble(self):
        location_bubble = await database_sync_to_async(
            self.get_user_location_bubble_for_room
        )()
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "location_bubble",
                "location_bubble": location_bubble,
            },
        )

    def get_user_area_query_for_room(self):
        area_query = self.user.areaquery_set.filter(room=self.room).values(
            "query",
        )

        if len(area_query) > 1:
            logger.info(
                f"Got multiple area queries for user {self.user.uid} in room {self.room.id}"
            )
        if len(area_query) > 0:
            return area_query[0]["query"]
        return None

    async def fetch_area_query(self):
        area_query = await database_sync_to_async(self.get_user_area_query_for_room)()
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "area_query",
                "area_query": area_query,
            },
        )
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "refresh_area_query",
            },
        )

    def get_intersection_for_room(self):
        intersection = self.room.intersection_set.values(
            "coordinates", "type", "centroid_lat", "centroid_lng"
        )

        if len(intersection) > 1:
            logger.info(f"Got multiple areas for room {self.room.id}")
        if len(intersection) > 0:
            return intersection[0]
        return {}

    def delete_intersection_for_room(self):
        self.room.intersection_set.all().delete()

    async def fetch_area(self):
        intersection = await database_sync_to_async(self.get_intersection_for_room)()
        return intersection

    def get_room_users_missing_location_bubbles(self):
        room_users_with_locations = self.room.locationbubble_set.values(
            "user__username", "user__display_name"
        )
        room_users = self.room.members.all().values("username", "display_name")
        rooms_users_missing_location_bubbles = list(
            room_users.difference(room_users_with_locations)
        )
        return rooms_users_missing_location_bubbles

    async def find_users_missing_location_bubbles(self):
        users_missing_location_bubbles = await database_sync_to_async(
            self.get_room_users_missing_location_bubbles
        )()
        return users_missing_location_bubbles

    def get_room_join_requests(self):
        self.room = self.get_room(self.room_group_name)
        requests = list(
            self.room.joinrequest_set.order_by("-timestamp").values(
                "user", "user__username", "user__display_name"
            )
        )
        return requests

    async def fetch_join_requests(self):
        try:
            requests = await database_sync_to_async(self.get_room_join_requests)()
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "requests",
                    "requests": json.dumps(requests),
                },
            )
        except JoinRequest.DoesNotExist:
            pass

    def get_user_unread_notifications_for_room(self):

        unobserved_notifications = list(
            self.user.notification_set.filter(
                room=self.room, timestamp__lte=self.user.last_logged_in, read=False
            ).values("message", "user_location", "added_place", "voted_place")
        )
        return unobserved_notifications

    def get_user_notifications(self):
        self.user.notification_set.filter(room=self.room).update(read=True)
        notifications = list(
            self.user.notification_set.values(
                "room",
                "room__display_name",
                "message__content",
                "message__user__display_name",
                "timestamp",
                "read",
                "user_joined__display_name",
                "user_left__display_name",
                "user_location__display_name",
                "join_request__user__display_name",
                "now_public",
                "now_private",
                "added_place__display_name",
                "voted_place__display_name",
            )
            .annotate(
                current_room=Case(
                    When(room=self.room, then=True),
                    default=False,
                    output_field=BooleanField(),
                )
            )
            .order_by("room", "-timestamp")
            .distinct("room")
        )
        notifications.sort(key=itemgetter("timestamp"), reverse=True)
        notifications.sort(key=itemgetter("read"))
        for notification in notifications:
            notification["room"] = str(notification["room"])
            notification["timestamp"] = str(notification["timestamp"])
        return notifications

    async def fetch_user_notifications(self):
        try:
            unobserved_notifications = await database_sync_to_async(
                self.get_user_unread_notifications_for_room
            )()
            if unobserved_notifications:
                hightlight_chat = False
                highlight_area = False
                highlight_vote = False
                for notification in unobserved_notifications:
                    if notification.get("message"):
                        hightlight_chat = True
                    if notification.get("user_location"):
                        highlight_area = True
                    if notification.get("added_place") or notification.get(
                        "voted_place"
                    ):
                        highlight_vote = True
                if hightlight_chat:
                    await self.channel_layer.send(
                        self.channel_name,
                        {
                            "type": "highlight_chat",
                        },
                    )
                if highlight_vote:
                    await self.channel_layer.send(
                        self.channel_name,
                        {
                            "type": "highlight_vote",
                        },
                    )
                if highlight_area:
                    await self.channel_layer.send(
                        self.channel_name,
                        {
                            "type": "highlight_area",
                        },
                    )
            notifications = await database_sync_to_async(self.get_user_notifications)()
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "notifications",
                    "notifications": json.dumps(notifications),
                },
            )
        except Notification.DoesNotExist:
            pass

    async def fetch_room_name(self):
        room = await database_sync_to_async(self.get_room)(self.room_group_name)
        if not room.display_name:
            room = await database_sync_to_async(self.update_room_name)(str(room.id))
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "room_name",
                "new_room_name": f"{room.display_name}",
            },
        )

    async def fetch_privacy(self):
        self.room = await database_sync_to_async(self.get_room)(self.room_group_name)
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "privacy",
                "privacy": self.room.private,
            },
        )

    async def fetch_room_members(self):
        members = await database_sync_to_async(self.get_room_members)()
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "members",
                "members": json.dumps(members),
            },
        )

    def get_room_members(self):
        return list(self.room.members.all().values("display_name"))

    def update_room_members(self, room, user):
        added = False
        if user not in room.members.all():
            room.members.add(user)
            self.create_user_joined_notification_for_all_room_members(user_joining=user)
            added = True
        return added

    def leave_room(self, room_id):
        room_to_leave = Room.objects.get(id=room_id)
        room_to_leave.members.remove(self.user)
        room_to_leave.locationbubble_set.filter(user=self.user).delete()
        for user in room_to_leave.members.all():
            Notification.objects.create(
                user=user, room=room_to_leave, user_left=self.user
            )
        self.user.notification_set.filter(room=room_to_leave).delete()

    def get_room(self, room_id):
        room, created = Room.objects.get_or_create(id=room_id)
        return room

    def create_new_message_notification_for_all_room_members(self, new_message):
        for user in self.room.members.all():
            Notification.objects.create(user=user, room=self.room, message=new_message)

    def create_privacy_notification_for_going_public(self):
        for user in self.room.members.all():
            Notification.objects.create(user=user, room=self.room, now_public=True)

    def create_privacy_notification_for_going_private(self):
        for user in self.room.members.all():
            Notification.objects.create(user=user, room=self.room, now_private=True)

    def create_join_request_notification_for_all_room_members(self, join_request):
        for user in self.room.members.all():
            Notification.objects.create(
                user=user, room=self.room, join_request=join_request
            )

    def update_user_location_notification(self):
        for user in self.room.members.all():
            Notification.objects.create(
                user=user, room=self.room, user_location=self.user
            )

    def added_place_notification(self):
        for user in self.room.members.all():
            Notification.objects.create(
                user=user, room=self.room, added_place=self.user
            )

    def voted_place_notification(self):
        for user in self.room.members.all():
            Notification.objects.create(
                user=user, room=self.room, voted_place=self.user
            )

    def create_user_joined_notification_for_all_room_members(self, user_joining):
        if user_joining is self.user:
            if not self.user.display_name:
                self.update_display_name(
                    self.user.get_full_name()
                    or self.user.email
                    or self.user.phone_number
                    or self.user.username
                )
        for user in self.room.members.all():
            Notification.objects.create(
                user=user, room=self.room, user_joined=user_joining
            )

    def get_rooms_of_all_members(self):
        rooms = set()
        for user in self.room.members.all():
            for room in user.room_set.all():
                rooms.add(str(room.id))
        return rooms

    def create_new_message(self, message):
        new_message = Message.objects.create(
            user=self.user, room=self.room, content=message
        )
        self.create_new_message_notification_for_all_room_members(new_message)
        return new_message

    def get_or_create_new_join_request(self):
        join_request, created = JoinRequest.objects.get_or_create(
            user=self.user, room=self.room
        )
        if created:
            self.create_join_request_notification_for_all_room_members(join_request)
        return created

    def approve_room_member(self, username):
        user = User.objects.get(username=username)
        self.room.members.add(user)
        self.room.joinrequest_set.filter(user=user).delete()
        self.create_user_joined_notification_for_all_room_members(user_joining=user)

    def approve_all_room_members(self):
        for request in self.room.joinrequest_set.all():
            self.room.members.add(request.user)
            self.create_user_joined_notification_for_all_room_members(
                user_joining=request.user
            )
        self.room.joinrequest_set.all().delete()

    def reject_room_member(self, username):
        user = User.objects.get(username=username)
        self.room.joinrequest_set.filter(user=user).delete()

    def user_is_not_room_member(self):
        return self.user not in self.room.members.all()

    def user_not_allowed(self):
        return self.user_is_not_room_member() and self.room.private

    def get_room_location_bubbles(self):
        room_location_bubbles = list(self.room.locationbubble_set.all().values())
        return room_location_bubbles

    def update_room_intersection(
        self, intersection_type, coordinates, centroid_lng, centroid_lat
    ):
        Intersection.objects.update_or_create(
            room=self.room,
            defaults={
                "type": intersection_type,
                "coordinates": coordinates,
                "centroid_lng": centroid_lng,
                "centroid_lat": centroid_lat,
            },
        )

    async def get_isochrone(self, session, url, payload):
        async with session.post(url, json=payload) as resp:
            try:
                result = await resp.json()
                return result["data"]["features"][0]
            except aiohttp.ContentTypeError:
                logger.error(
                    f"Targomo API call failed for {payload}. {await resp.text()}"
                )

    async def get_region_isochrone(self, session, url, payload, region):
        async with session.post(url, json=payload) as resp:
            try:
                result = await resp.json()
                result = result["data"]["features"][0]
                return {
                    "isochrone": result,
                    "region": region,
                    "travel_mode": list(payload["sources"][0]["tm"])[0],
                }
            except (aiohttp.ContentTypeError, KeyError):
                logger.error(
                    f"Targomo API call failed for {payload}. {await resp.text()}"
                )

    async def get_isochrone_service_region(self, location_latitude, location_longitude):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            async with aiohttp.ClientSession() as session:
                travel_time_in_seconds = 180
                walk_payload = {
                    "sources": [
                        {
                            "lat": location_latitude,
                            "lng": location_longitude,
                            "id": f"region for {location_latitude}, {location_longitude}",
                            "tm": {"walk": {}},
                        }
                    ],
                    "polygon": {
                        "serializer": "geojson",
                        "srid": 4326,
                        "values": [travel_time_in_seconds],
                    },
                }
                transit_payload = {
                    "sources": [
                        {
                            "lat": location_latitude,
                            "lng": location_longitude,
                            "id": f"region for {location_latitude}, {location_longitude}",
                            "tm": {"transit": {}},
                        }
                    ],
                    "polygon": {
                        "serializer": "geojson",
                        "srid": 4326,
                        "values": [travel_time_in_seconds],
                    },
                }
                service_regions = [
                    "africa",
                    "central_america",
                    "south_america",
                    "australia",
                    "britishisles",
                    "asia",
                    "easterneurope",
                    "northamerica",
                    "westcentraleurope",
                ]
                tasks = []
                for region in service_regions:
                    url = f"https://service.targomo.com/{region}/v1/polygon?key={os.environ.get('TARGOMO_API_KEY')}"
                    tasks.append(
                        asyncio.ensure_future(
                            self.get_region_isochrone(
                                session,
                                url,
                                walk_payload,
                                region,
                            )
                        )
                    )
                    tasks.append(
                        asyncio.ensure_future(
                            self.get_region_isochrone(
                                session,
                                url,
                                transit_payload,
                                region,
                            )
                        )
                    )
                region_isochrones = await asyncio.gather(*tasks)
                await self.channel_layer.send(
                    self.channel_name,
                    {
                        "type": "isochrone_service_regions",
                        "region_isochrones": region_isochrones,
                        "location_lng": location_longitude,
                        "location_lat": location_latitude,
                    },
                )

    async def get_isochrones(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            async with aiohttp.ClientSession() as session:
                location_bubbles = await database_sync_to_async(
                    self.get_room_location_bubbles
                )()
                members = await database_sync_to_async(self.get_room_members)()
                if location_bubbles and (
                    (len(members) > 1 and len(location_bubbles) > 1)
                    or (len(members) == 1 and len(location_bubbles) == 1)
                ):
                    tasks = []
                    for location_bubble in location_bubbles:
                        payload = {
                            "sources": [
                                {
                                    "lat": location_bubble["latitude"],
                                    "lng": location_bubble["longitude"],
                                    "id": f"{location_bubble['id']}",
                                    "tm": {location_bubble["transportation"]: {}},
                                }
                            ],
                            "polygon": {
                                "serializer": "geojson",
                                "srid": 4326,
                                "values": [
                                    (location_bubble["hours"] * 3600)
                                    + (location_bubble["minutes"] * 60)
                                ],
                            },
                        }
                        url = f"https://service.targomo.com/{location_bubble['region']}/v1/polygon?key={os.environ.get('TARGOMO_API_KEY')}"
                        tasks.append(
                            asyncio.ensure_future(
                                self.get_isochrone(session, url, payload)
                            )
                        )
                    room_isochrones = await asyncio.gather(*tasks)
                    await self.channel_layer.send(
                        self.channel_name,
                        {
                            "type": "isochrones",
                            "isochrones": room_isochrones,
                        },
                    )
                else:
                    await database_sync_to_async(self.delete_intersection_for_room)()
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "refresh_area"},
                    )

    # Receive message from WebSocket
    async def receive(self, text_data):
        input_payload = json.loads(text_data)
        if input_payload.get("command") == "fetch_messages":
            asyncio.create_task(self.handle_fetch_messages())
        elif input_payload.get("command") == "fetch_allowed_status":
            asyncio.create_task(self.handle_fetch_allowed_status())
        elif input_payload.get("command") == "fetch_display_name":
            asyncio.create_task(self.fetch_display_name())
        elif input_payload.get("command") == "get_isochrone_service_region":
            asyncio.create_task(
                self.get_isochrone_service_region(
                    input_payload["latitude"],
                    input_payload["longitude"],
                )
            )
        elif input_payload.get("command") == "update_display_name":
            asyncio.create_task(self.handle_update_display_name(input_payload))
        elif input_payload.get("command") == "update_intersection":
            asyncio.create_task(self.handle_update_intersection(input_payload))
        elif input_payload.get("command") == "delete_intersection":
            asyncio.create_task(self.handle_delete_intersection())
        elif input_payload.get("command") == "fetch_users_missing_locations":
            asyncio.create_task(self.handle_fetch_users_missing_locations())
        elif input_payload.get("command") == "fetch_intersection":
            asyncio.create_task(self.handle_fetch_intersection())
        elif input_payload.get("command") == "fetch_location_bubble":
            asyncio.create_task(self.handle_fetch_location_bubble())
        elif input_payload.get("command") == "update_location_bubble":
            asyncio.create_task(self.handle_update_location_bubble(input_payload))
        elif input_payload.get("command") == "fetch_area_query":
            asyncio.create_task(self.handle_fetch_area_query())
        elif input_payload.get("command") == "update_area_query":
            asyncio.create_task(self.handle_update_area_query(input_payload))
            asyncio.create_task(self.handle_get_area_query_results(input_payload))
        elif input_payload.get("command") == "save_place":
            asyncio.create_task(self.handle_save_place(input_payload))
        elif input_payload.get("command") == "fetch_places":
            asyncio.create_task(self.handle_fetch_places())
        elif input_payload.get("command") == "fetch_room_name":
            asyncio.create_task(self.handle_fetch_room_name())
        elif input_payload.get("command") == "fetch_members":
            asyncio.create_task(self.handle_fetch_members())
        elif input_payload.get("command") == "update_room_name":
            asyncio.create_task(self.handle_update_room_name(input_payload))
        elif input_payload.get("command") == "exit_room":
            asyncio.create_task(self.exit_room(input_payload))
        elif input_payload.get("command") == "calculate_intersection":
            asyncio.create_task(self.get_isochrones())
        elif input_payload.get("command") == "approve_user":
            asyncio.create_task(self.handle_approve_user(input_payload))
        elif input_payload.get("command") == "approve_all_users":
            asyncio.create_task(self.handle_approve_all_users())
        elif input_payload.get("command") == "reject_user":
            asyncio.create_task(self.handle_reject_user(input_payload))
        elif input_payload.get("command") == "fetch_join_requests":
            asyncio.create_task(self.fetch_join_requests())
        elif input_payload.get("command") == "fetch_user_notifications":
            asyncio.create_task(self.fetch_user_notifications())
        elif input_payload.get("command") == "fetch_privacy":
            asyncio.create_task(self.handle_fetch_privacy())
        elif input_payload.get("command") == "update_privacy":
            asyncio.create_task(self.handle_privacy_update(input_payload))
        elif input_payload.get("command") == "get_next_page_places":
            asyncio.create_task(self.get_next_page_places(input_payload))
        elif input_payload.get("command") == "join_room":
            asyncio.create_task(self.join_room())
        elif input_payload.get("command") == "vote_place":
            asyncio.create_task(self.vote_place(input_payload))
        else:
            asyncio.create_task(self.handle_message(input_payload))

    def vote_for_place(self, place_id):
        voted_place = Place.objects.get(place_id=place_id, room=self.room)
        Vote.objects.update_or_create(
            room=self.room,
            user=self.user,
            defaults={
                "place": voted_place,
            },
        )
        self.voted_place_notification()

    async def vote_place(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.vote_for_place)(input_payload["place_id"])
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {"type": "refresh_notifications"},
                )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_places"},
            )

    async def handle_fetch_messages(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.fetch_messages)()

    async def handle_fetch_allowed_status(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if user_not_allowed:
            created = await database_sync_to_async(
                self.get_or_create_new_join_request
            )()
            if created:
                rooms_to_notify = await database_sync_to_async(
                    self.get_rooms_of_all_members
                )()
                for room in rooms_to_notify:
                    await self.channel_layer.group_send(
                        room,
                        {
                            "type": "refresh_notifications",
                        },
                    )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_join_requests"},
            )
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "not_allowed",
                },
            )
        else:
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "allowed",
                },
            )

    async def handle_update_display_name(self, input_payload):
        await database_sync_to_async(self.update_display_name)(input_payload["name"])
        rooms_to_notify = await database_sync_to_async(self.get_rooms_of_all_members)()
        for room in rooms_to_notify:
            await self.channel_layer.group_send(
                room,
                {"type": "refresh_notifications"},
            )
            await self.channel_layer.group_send(
                room,
                {"type": "refresh_members"},
            )
            await self.channel_layer.group_send(
                room,
                {"type": "refresh_chat"},
            )
            await self.channel_layer.group_send(
                room,
                {"type": "refresh_users_missing_locations"},
            )

    async def handle_update_intersection(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.update_room_intersection)(
                input_payload["type"],
                input_payload["coordinates"],
                input_payload["centroid_lng"],
                input_payload["centroid_lat"],
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_area"},
            )

    async def handle_delete_intersection(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.delete_intersection_for_room)()
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_area"},
            )

    async def handle_fetch_users_missing_locations(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            users = await self.find_users_missing_location_bubbles()
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "users_missing_locations",
                    "users": users,
                },
            )

    async def handle_fetch_intersection(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            intersection = await self.fetch_area()
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "intersection",
                    "intersection": intersection,
                },
            )
            await self.channel_layer.send(
                self.channel_name,
                {"type": "refresh_users_missing_locations"},
            )
            await self.channel_layer.send(
                self.channel_name,
                {"type": "refresh_area_query"},
            )

    async def handle_fetch_area_query(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_area_query()

    async def handle_fetch_location_bubble(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_location_bubble()

    async def handle_update_area_query(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.update_area_query)(
                input_payload["query"],
            )

    async def text_search_results(self, session, url):
        async with session.get(url) as resp:
            try:
                response = await resp.json()
                return response
            except aiohttp.ContentTypeError:
                logger.error(f"Text search failed. {await resp.text()}")

    async def handle_get_area_query_results(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            query = input_payload["query"]
            lat = input_payload["lat"]
            lng = input_payload["lng"]
            place_results = []
            async with aiohttp.ClientSession() as session:
                url = (
                    f"https://maps.googleapis.com/maps/api/place/textsearch/json?&query={query}&location={lat},{lng}&"
                    f"key={os.environ.get('MAPS_API_KEY')}"
                )
                tasks = [asyncio.ensure_future(self.text_search_results(session, url))]
                response = await asyncio.gather(*tasks)
                response = response[0]
                next_page_token = response.get("next_page_token", "")
                place_results += response["results"]
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "area_query_results",
                    "area_query_results": place_results,
                    "next_page_places_token": next_page_token,
                },
            )

    async def get_next_page_places(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            next_page_token = input_payload["token"]
            place_results = []
            async with aiohttp.ClientSession() as session:
                while next_page_token:
                    next_url = (
                        f"https://maps.googleapis.com/maps/api/place/textsearch/json?pagetoken"
                        f"={next_page_token}&key={os.environ.get('MAPS_API_KEY')}"
                    )
                    tasks = [
                        asyncio.ensure_future(
                            self.text_search_results(session, next_url)
                        )
                    ]
                    response = await asyncio.gather(*tasks)
                    response = response[0]
                    if response["status"] == "INVALID_REQUEST":
                        logger.info(f"next page token currently invalid")
                    else:
                        place_results += response["results"]
                        new_next_page_token = response.get("next_page_token", "")
                        break

            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "next_page_place_results",
                    "next_page_place_results": place_results,
                    "token_used": next_page_token,
                    "next_page_places_token": new_next_page_token,
                },
            )

    async def get_place(self, session, url, old_place_id):
        async with session.get(url) as resp:
            try:
                result = await resp.json()
                result = result["result"]
                result_place_id = result["place_id"]
                result_location = result["geometry"]["location"]
                if old_place_id != result_place_id:
                    await database_sync_to_async(self.save_place)(
                        result_place_id,
                        result_location["lat"],
                        result_location["lng"],
                        update_timestamp=False,
                    )
                return result
            except aiohttp.ContentTypeError:
                logger.error(f"Place id refresh failed. {await resp.text()}")

    async def get_distance_matrix(self, session, url):
        async with session.get(url) as resp:
            try:
                result = await resp.json()
                result = result["rows"][0]["elements"]
                return result
            except aiohttp.ContentTypeError:
                logger.error(f"Distance matrix failed. {await resp.text()}")

    async def handle_fetch_places(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            places = await database_sync_to_async(self.fetch_places)()
            location_bubble = await database_sync_to_async(
                self.get_user_location_bubble_for_room
            )()

            if location_bubble:
                mode = "transit"
                if location_bubble["transportation"] == "bike":
                    mode = "bicycling"
                elif location_bubble["transportation"] == "car":
                    mode = "driving"
                elif location_bubble["transportation"] == "walk":
                    mode = "walking"
                distance_matrix_url = (
                    f"https://maps.googleapis.com/maps/api/distancematrix/json?key="
                    f"{os.environ.get('MAPS_API_KEY')}&origins=place_id:{location_bubble['place_id']}"
                    f"&mode={mode}&destinations="
                )

            async with aiohttp.ClientSession() as session:
                tasks = []

                for place in places:
                    if location_bubble:
                        distance_matrix_url += f"place_id:{place['place_id']}|"
                    url = (
                        f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place['place_id']}&"
                        f"fields=formatted_phone_number,geometry,icon,name,opening_hours,url,place_id,website,"
                        f"rating,price_level,vicinity&key={os.environ.get('MAPS_API_KEY')}"
                    )
                    tasks.append(
                        asyncio.ensure_future(
                            self.get_place(session, url, place["place_id"])
                        )
                    )
                if location_bubble and places:
                    distance_matrix_url = distance_matrix_url[:-1]
                    tasks.append(
                        asyncio.ensure_future(
                            self.get_distance_matrix(session, distance_matrix_url)
                        )
                    )
                results = await asyncio.gather(*tasks)
                for index, place in enumerate(places):
                    results[index]["total_votes"] = place["total_votes"]
                    results[index]["user_voted_for"] = place["user_voted_for"]
                if results and location_bubble:
                    distance_matrix = results[-1]
                    for index, place in enumerate(results[:-1]):
                        place["travel_time"] = distance_matrix[index]["duration"]
                        place["distance"] = distance_matrix[index]["distance"]
                    results = results[:-1]
                await self.channel_layer.send(
                    self.channel_name,
                    {"type": "places", "places": results},
                )

    async def handle_save_place(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.save_place)(
                input_payload["id"], input_payload["lat"], input_payload["lng"]
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_places"},
            )
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {"type": "refresh_notifications"},
                )

    async def handle_update_location_bubble(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            async with aiohttp.ClientSession() as session:
                travel_time_in_seconds = 180
                walk_payload = {
                    "sources": [
                        {
                            "lat": input_payload["latitude"],
                            "lng": input_payload["longitude"],
                            "id": f"region for {input_payload['latitude']}, {input_payload['longitude']}",
                            "tm": {"walk": {}},
                        }
                    ],
                    "polygon": {
                        "serializer": "geojson",
                        "srid": 4326,
                        "values": [travel_time_in_seconds],
                    },
                }
                transit_payload = {
                    "sources": [
                        {
                            "lat": input_payload["latitude"],
                            "lng": input_payload["longitude"],
                            "id": f"region for {input_payload['latitude']}, {input_payload['longitude']}",
                            "tm": {"transit": {}},
                        }
                    ],
                    "polygon": {
                        "serializer": "geojson",
                        "srid": 4326,
                        "values": [travel_time_in_seconds],
                    },
                }
                service_regions = [
                    "africa",
                    "central_america",
                    "south_america",
                    "australia",
                    "britishisles",
                    "asia",
                    "easterneurope",
                    "northamerica",
                    "westcentraleurope",
                ]
                tasks = []
                for region in service_regions:
                    url = f"https://service.targomo.com/{region}/v1/polygon?key={os.environ.get('TARGOMO_API_KEY')}"
                    tasks.append(
                        asyncio.ensure_future(
                            self.get_region_isochrone(
                                session,
                                url,
                                walk_payload,
                                region,
                            )
                        )
                    )
                    tasks.append(
                        asyncio.ensure_future(
                            self.get_region_isochrone(
                                session,
                                url,
                                transit_payload,
                                region,
                            )
                        )
                    )
                region_isochrones = await asyncio.gather(*tasks)
                processed_region_isochrones = []
                for region_isochrone in region_isochrones:
                    polygons = []
                    for polygon in region_isochrone["isochrone"]["geometry"][
                        "coordinates"
                    ]:
                        subpolygons = []
                        for subpolygon in polygon:
                            coordinates = []
                            for lng, lat in subpolygon:
                                coordinates.append((lng, lat))
                            subpolygons.append(Polygon(coordinates))
                        polygons.append(
                            Polygon(
                                subpolygons[0].exterior.coords,
                                [hole.exterior.coords for hole in subpolygons[1:]],
                            )
                        )
                    processed_isochrone = MultiPolygon(polygons)
                    processed_region_isochrones.append(
                        {
                            "isochrone": processed_isochrone,
                            "region": region_isochrone["region"],
                            "travel_mode": region_isochrone["travel_mode"],
                        }
                    )
                possible_regions = []
                for region_isochrone in processed_region_isochrones:
                    if (
                        region_isochrone["isochrone"].covers(
                            Point(
                                input_payload["longitude"],
                                input_payload["latitude"],
                            )
                        )
                        or region_isochrone["isochrone"].distance(
                            Point(
                                input_payload["longitude"],
                                input_payload["latitude"],
                            )
                        )
                        < 1e-3
                    ):
                        possible_regions.append(
                            {
                                "name": region_isochrone["region"],
                                "travel_mode": region_isochrone["travel_mode"],
                                "area": region_isochrone["isochrone"].area,
                            }
                        )
                if possible_regions:
                    user_region = possible_regions[0]
                    for region in possible_regions[1:]:
                        if (
                            user_region["area"] < region["area"]
                            and user_region["travel_mode"] == "walk"
                            and region["travel_mode"] == "transit"
                            and region["name"] != "central_america"
                        ):
                            user_region = region
                    isochrone_service_region = user_region["name"]
                    await database_sync_to_async(self.update_location_bubble)(
                        input_payload["address"],
                        input_payload["latitude"],
                        input_payload["longitude"],
                        input_payload["transportation"],
                        input_payload["hours"],
                        input_payload["minutes"],
                        isochrone_service_region,
                        input_payload["place_id"],
                    )
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "recalculate_intersection"},
                    )
                    rooms_to_notify = await database_sync_to_async(
                        self.get_rooms_of_all_members
                    )()
                    for room in rooms_to_notify:
                        await self.channel_layer.group_send(
                            room,
                            {"type": "refresh_notifications"},
                        )
                        await self.channel_layer.group_send(
                            room,
                            {"type": "refresh_users_missing_locations"},
                        )
                else:
                    logger.error(
                        f"Could not find isochrone service region for location bubble: {input_payload}"
                    )
                    await self.channel_layer.send(
                        self.channel_name,
                        {
                            "type": "region_not_found",
                        },
                    )

    async def handle_fetch_room_name(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_room_name()

    async def handle_fetch_members(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_room_members()

    async def handle_update_room_name(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.update_room_name)(input_payload["name"])
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {"type": "refresh_notifications"},
                )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_room_name"},
            )

    async def exit_room(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            await database_sync_to_async(self.leave_room)(input_payload["room_id"])

            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {
                        "type": "refresh_notifications",
                    },
                )
            await self.channel_layer.group_send(
                input_payload["room_id"],
                {"type": "refresh_members"},
            )
            await self.channel_layer.group_send(
                input_payload["room_id"],
                {"type": "refresh_users_missing_locations"},
            )
            await self.channel_layer.group_send(
                input_payload["room_id"],
                {"type": "recalculate_intersection"},
            )
            await self.channel_layer.group_send(
                input_payload["room_id"],
                {"type": "refresh_allowed_status"},
            )

    async def handle_approve_user(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.approve_room_member)(
                input_payload["username"]
            )
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {"type": "refresh_notifications"},
                )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_join_requests"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_members"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_allowed_status"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_chat"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_room_name"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_privacy"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_users_missing_locations"},
            )

    async def handle_approve_all_users(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.approve_all_room_members)()
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {
                        "type": "refresh_notifications",
                    },
                )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_join_requests"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_members"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_allowed_status"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_chat"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_room_name"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_privacy"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_users_missing_locations"},
            )

    async def handle_reject_user(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.reject_room_member)(
                input_payload["username"]
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_join_requests"},
            )

    async def handle_fetch_join_requests(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_join_requests()

    async def handle_fetch_user_notifications(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_user_notifications()

    async def handle_fetch_privacy(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await self.fetch_privacy()

    async def handle_privacy_update(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            await database_sync_to_async(self.update_privacy)(input_payload["privacy"])
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_privacy"},
            )
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {
                        "type": "refresh_notifications",
                    },
                )

    async def join_room(self):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if user_not_allowed:
            created = await database_sync_to_async(
                self.get_or_create_new_join_request
            )()
            if created:
                rooms_to_notify = await database_sync_to_async(
                    self.get_rooms_of_all_members
                )()
                for room in rooms_to_notify:
                    await self.channel_layer.group_send(
                        room,
                        {
                            "type": "refresh_notifications",
                        },
                    )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_join_requests"},
            )
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "not_allowed",
                },
            )
        else:
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "allowed",
                },
            )
            previous_members = await database_sync_to_async(self.get_room_members)()
            user_was_added = await database_sync_to_async(self.update_room_members)(
                self.room, self.user
            )
            if user_was_added and len(previous_members) == 1:
                await database_sync_to_async(self.delete_intersection_for_room)()
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {"type": "refresh_area"},
                )

            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {
                        "type": "refresh_notifications",
                    },
                )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_members"},
            )
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "refresh_users_missing_locations"},
            )

    async def handle_message(self, input_payload):
        user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
        if not user_not_allowed:
            message = input_payload["message"]
            display_name = input_payload["user"]
            await database_sync_to_async(self.create_new_message)(message)
            rooms_to_notify = await database_sync_to_async(
                self.get_rooms_of_all_members
            )()
            for room in rooms_to_notify:
                await self.channel_layer.group_send(
                    room,
                    {
                        "type": "refresh_notifications",
                    },
                )
            # Send message to room group
            await self.channel_layer.group_send(
                self.room_group_name,
                {"type": "chat_message", "message": f"{display_name}: {message}"},
            )

    # Receive message from room group
    async def chat_message(self, event):
        message = event["message"]

        # Send message to WebSocket
        await self.send(text_data=json.dumps({"message": message}))

    async def fetching_message(self, event):
        message = event["message"]

        # Send message to WebSocket
        await self.send(text_data=json.dumps({"fetching_message": message}))

    async def display_name(self, event):
        name = event["new_display_name"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"new_display_name": name}))

    async def location_bubble(self, event):
        location_bubble = event["location_bubble"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"location_bubble": location_bubble}))

    async def intersection(self, event):
        area = event["intersection"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"area": area}))

    async def isochrones(self, event):
        isochrones = event["isochrones"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"isochrones": isochrones}))

    async def users_missing_locations(self, event):
        users = event["users"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"users_missing_locations": users}))

    async def room_name(self, event):
        name = event["new_room_name"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"new_room_name": name}))

    async def area_query(self, event):
        area_query = event["area_query"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"area_query": area_query}))

    async def members(self, event):
        members = event["members"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"members": members}))

    async def requests(self, event):
        requests = event["requests"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"requests": requests}))

    async def notifications(self, event):
        notifications = event["notifications"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"notifications": notifications}))

    async def isochrone_service_regions(self, event):
        region_isochrones = event["region_isochrones"]
        # Send message to WebSocket
        await self.send(
            text_data=json.dumps(
                {
                    "region_isochrones": region_isochrones,
                    "location_lng": event["location_lng"],
                    "location_lat": event["location_lat"],
                }
            )
        )

    async def privacy(self, event):
        privacy = event["privacy"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"privacy": privacy}))

    async def not_allowed(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"not_allowed": True}))

    async def allowed(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"allowed": True}))

    async def refresh_privacy(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_privacy": True}))

    async def refresh_members(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_members": True}))

    async def refresh_area(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_area": True}))

    async def refresh_notifications(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_notifications": True}))

    async def refresh_join_requests(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_join_requests": True}))

    async def refresh_chat(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_chat": True}))

    async def refresh_users_missing_locations(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_users_missing_locations": True}))

    async def refresh_room_name(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_room_name": True}))

    async def recalculate_intersection(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"recalculate_intersection": True}))

    async def region_not_found(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"region_not_found": True}))

    async def highlight_chat(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"highlight_chat": True}))

    async def highlight_area(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"highlight_area": True}))

    async def highlight_vote(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"highlight_vote": True}))

    async def refresh_allowed_status(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_allowed_status": True}))

    async def refresh_area_query(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_area_query": True}))

    async def refresh_places(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_places": True}))

    async def places(self, event):
        places = event["places"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"places": places}))

    async def area_query_results(self, event):
        area_query_results = event["area_query_results"]
        token = event["next_page_places_token"]
        # Send message to WebSocket
        await self.send(
            text_data=json.dumps(
                {
                    "area_query_results": area_query_results,
                    "next_page_places_token": token,
                }
            )
        )

    async def next_page_place_results(self, event):
        next_page_place_results = event["next_page_place_results"]
        token_used = event["token_used"]
        token = event["next_page_places_token"]
        # Send message to WebSocket
        await self.send(
            text_data=json.dumps(
                {
                    "next_page_place_results": next_page_place_results,
                    "token_used": token_used,
                    "next_page_places_token": token,
                }
            )
        )
