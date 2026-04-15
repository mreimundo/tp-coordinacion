import uuid
from common import message_protocol


class MessageHandler:

    def __init__(self):
        # generamos id cliente con optimismo de que no van a haber colisiones
        self._client_id = str(uuid.uuid4())

    def serialize_data_message(self, message):
        [fruit, amount] = message
        return message_protocol.internal.serialize_data(self._client_id, fruit, amount)

    def serialize_eof_message(self, message):
        return message_protocol.internal.serialize_eof(
            self._client_id
        )

    def deserialize_result_message(self, message):
        msg = message_protocol.internal.deserialize(message)
        client_id = msg.get("client_id")
        fruit_top = msg.get("fruit_top")

        if client_id != self._client_id:
            return None

        return fruit_top
