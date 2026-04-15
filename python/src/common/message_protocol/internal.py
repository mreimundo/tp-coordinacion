import json

MSG_DATA = "data"
MSG_EOF = "eof"


def serialize(message):
    return json.dumps(message).encode("utf-8")


def deserialize(message):
    return json.loads(message.decode("utf-8"))


def make_data(client_id, fruit, amount):
    return {"type": MSG_DATA, "client_id": client_id,
            "fruit": fruit, "amount": amount}


def make_eof(client_id, remaining=None):
    return {"type": MSG_EOF, "client_id": client_id, "remaining": remaining}


def is_data(msg):
    return msg.get("type") == MSG_DATA


def is_eof(msg):
    return msg.get("type") == MSG_EOF


def serialize_data(client_id, fruit, amount):
    return serialize(make_data(client_id, fruit, amount))


def serialize_eof(client_id, remaining=None):
    return serialize(make_eof(client_id, remaining))
