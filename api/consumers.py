import os
from operator import itemgetter

import asyncio
import json
import logging

import aiohttp as aiohttp
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from shapely.geometry import Polygon, Point

from api.models import (
    Message,
    Room,
    User,
    JoinRequest,
    Notification,
    LocationBubble,
    Intersection,
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

    async def connect(self):
        self.room_group_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room = await database_sync_to_async(self.get_room)(self.room_group_name)
        self.user = self.scope["user"]

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
        self, address, latitude, longitude, transportation, hours, minutes, region
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
            },
        )
        self.update_user_location_notification()
        return created

    def update_display_name(self, new_name):
        self.user.display_name = new_name
        self.user.save()

    def update_room_name(self, new_name):
        self.room.display_name = new_name
        self.room.save()

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
        location_bubbles = self.user.locationbubble_set.filter(room=self.room).values(
            "address",
            "latitude",
            "longitude",
            "hours",
            "minutes",
            "transportation",
            "region",
        )

        if len(location_bubbles) > 1:
            logger.info(
                f"Got multiple location bubbles for user {self.user.uid} in room {self.room.id}"
            )
        if len(location_bubbles) > 0:
            return location_bubbles[0]
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
        self.room = await database_sync_to_async(self.get_room)(self.room_group_name)
        if not self.room.display_name:
            await database_sync_to_async(self.update_room_name)(str(self.room.id))
        await self.channel_layer.send(
            self.channel_name,
            {
                "type": "room_name",
                "new_room_name": f"{self.room.display_name}",
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
        if user not in room.members.all():
            room.members.add(user)
            self.create_user_joined_notification_for_all_room_members(user_joining=user)

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

    def user_not_allowed(self):
        return self.user not in self.room.members.all() and self.room.private

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
            logger.debug(f"{payload}")
            logger.debug(f"{await resp.text()}")
            result = await resp.json()
            return result["data"]["features"][0]

    async def get_region_isochrone(self, session, url, payload, region):
        async with session.post(url, json=payload) as resp:
            result = await resp.json()
            result = result["data"]["features"][0]["geometry"]["coordinates"]
            return {"coordinates": result, "region": region}

    async def get_isochrone_service_region(self, location_latitude, location_longitude):
        async with aiohttp.ClientSession() as session:
            payload = {
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
                    "values": [1],
                },
            }
            service_regions = [
                "asia",
                "africa",
                "australia",
                "britishisles",
                "central_america",
                "easterneurope",
                "northamerica",
                "south_america",
                "westcentraleurope",
            ]
            tasks = []
            for region in service_regions:
                url = f"https://service.targomo.com/{region}/v1/polygon?key={os.environ.get('TARGOMO_API_KEY')}"
                tasks.append(
                    asyncio.ensure_future(
                        self.get_region_isochrone(session, url, payload, region)
                    )
                )
            raw_region_isochrones = await asyncio.gather(*tasks)
            processed_region_isochrones = []
            for isochrone in raw_region_isochrones:
                polygons = []
                for polygon in isochrone["coordinates"]:
                    coordinates = []
                    for lng, lat in polygon[0]:
                        coordinates.append((lng, lat))
                    polygons.append(Polygon(coordinates))
                processed_isochrone = Polygon(
                    polygons[0].exterior.coords,
                    [hole.exterior.coords for hole in polygons[1:]],
                )
                processed_region_isochrones.append(
                    {"isochrone": processed_isochrone, "region": isochrone["region"]}
                )
            for region_isochrone in processed_region_isochrones:
                if (
                    region_isochrone["isochrone"].distance(
                        Point(location_longitude, location_latitude)
                    )
                    < 1e-3
                ):
                    return region_isochrone["region"]

    async def get_isochrones(self, location_bubbles):

        async with aiohttp.ClientSession() as session:

            if location_bubbles:
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
                        asyncio.ensure_future(self.get_isochrone(session, url, payload))
                    )
                room_isochrones = await asyncio.gather(*tasks)
                return room_isochrones

    # Receive message from WebSocket
    async def receive(self, text_data):
        input_payload = json.loads(text_data)
        if input_payload.get("command") == "fetch_messages":
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
                await database_sync_to_async(self.fetch_messages)()
        elif input_payload.get("command") == "fetch_allowed_status":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if user_not_allowed:
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
        elif input_payload.get("command") == "fetch_display_name":
            await self.fetch_display_name()
        elif input_payload.get("command") == "get_isochrone_service_region":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                region = await self.get_isochrone_service_region(
                    input_payload["latitude"],
                    input_payload["longitude"],
                )
                logger.debug(f"isochrone service region: {region}")
                await self.channel_layer.send(
                    self.channel_name,
                    {"type": "isochrone_service_region", "region": region},
                )
        elif input_payload.get("command") == "update_display_name":
            await database_sync_to_async(self.update_display_name)(
                input_payload["name"]
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
        elif input_payload.get("command") == "update_intersection":
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
        elif input_payload.get("command") == "delete_intersection":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await database_sync_to_async(self.delete_intersection_for_room)()
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {"type": "refresh_area"},
                )
        elif input_payload.get("command") == "fetch_users_missing_locations":
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
        elif input_payload.get("command") == "fetch_intersection":
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
        elif input_payload.get("command") == "fetch_location_bubble":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await self.fetch_location_bubble()
        elif input_payload.get("command") == "update_location_bubble":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await database_sync_to_async(self.update_location_bubble)(
                    input_payload["address"],
                    input_payload["latitude"],
                    input_payload["longitude"],
                    input_payload["transportation"],
                    input_payload["hours"],
                    input_payload["minutes"],
                    input_payload["region"],
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
        elif input_payload.get("command") == "fetch_room_name":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await self.fetch_room_name()
        elif input_payload.get("command") == "fetch_members":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await self.fetch_room_members()
        elif input_payload.get("command") == "update_room_name":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await database_sync_to_async(self.update_room_name)(
                    input_payload["name"]
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
                    {"type": "refresh_room_name"},
                )
        elif input_payload.get("command") == "exit_room":
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
        elif input_payload.get("command") == "calculate_intersection":
            room_location_bubbles = await database_sync_to_async(
                self.get_room_location_bubbles
            )()
            isochrones = await self.get_isochrones(
                room_location_bubbles,
            )
            await self.channel_layer.send(
                self.channel_name,
                {
                    "type": "isochrones",
                    "isochrones": isochrones,
                },
            )
        elif input_payload.get("command") == "approve_user":
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
        elif input_payload.get("command") == "approve_all_users":
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
        elif input_payload.get("command") == "reject_user":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await database_sync_to_async(self.reject_room_member)(
                    input_payload["username"]
                )
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {"type": "refresh_join_requests"},
                )
        elif input_payload.get("command") == "fetch_join_requests":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await self.fetch_join_requests()
        elif input_payload.get("command") == "fetch_user_notifications":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await self.fetch_user_notifications()
        elif input_payload.get("command") == "fetch_privacy":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await self.fetch_privacy()
        elif input_payload.get("command") == "update_privacy":
            user_not_allowed = await database_sync_to_async(self.user_not_allowed)()
            if not user_not_allowed:
                await database_sync_to_async(self.update_privacy)(
                    input_payload["privacy"]
                )
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
        elif input_payload.get("command") == "join_room":
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
                await database_sync_to_async(self.update_room_members)(
                    self.room, self.user
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
        else:
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

    async def isochrone_service_region(self, event):
        region = event["region"]
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"region": region}))

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

    async def refresh_allowed_status(self, event):
        # Send message to WebSocket
        await self.send(text_data=json.dumps({"refresh_allowed_status": True}))
