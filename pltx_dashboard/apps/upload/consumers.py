import json
from channels.generic.websocket import AsyncWebsocketConsumer


class UploadProgressConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user_id = self.scope["session"].get("user_id")
        if not self.user_id:
            await self.close()
            return

        self.group_name = f"user_{self.user_id}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)

        await self.accept()

    async def disconnect(self, _close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def upload_progress(self, event):
        await self.send(
            text_data=json.dumps(
                {"message": event["message"], "status": event["status"]}
            )
        )
