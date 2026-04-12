from common import message_protocol


class MessageHandler:

    def __init__(self):
        pass

    def serialize_data_message(self, message, client_id):
        [fruit, amount] = message
        return message_protocol.internal.serialize_data(client_id, fruit, amount)

    def serialize_eof_message(self, message, client_id, sum_amount):
        return message_protocol.internal.serialize_eof(
            client_id, remaining=sum_amount - 1
        )

    def deserialize_result_message(self, message):
        msg = message_protocol.internal.deserialize(message)
        client_id = msg.get("client_id")
        fruit_top = msg.get("fruit_top")
        if client_id is None or fruit_top is None:
            return None
        return (client_id, fruit_top)
