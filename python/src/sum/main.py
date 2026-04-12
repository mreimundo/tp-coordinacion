import os
import logging
import signal
import hashlib

from common import middleware, message_protocol, fruit_item

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
SUM_PREFIX = os.environ["SUM_PREFIX"]
SUM_CONTROL_EXCHANGE = "SUM_CONTROL_EXCHANGE"
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]


class SumFilter:
    def __init__(self):
        self.shutting_down = False

        self.input_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, INPUT_QUEUE
        )
        self.aggregation_exchanges = [
            middleware.MessageMiddlewareExchangeRabbitMQ(
                MOM_HOST, AGGREGATION_PREFIX, [f"{AGGREGATION_PREFIX}_{i}"]
            )
            for i in range(AGGREGATION_AMOUNT)
        ]
        self.amount_by_fruit = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        self.input_queue.close()
        for exchange in self.aggregation_exchanges:
            exchange.close()

    @staticmethod
    def _stable_hash(fruit):
        return int(hashlib.md5(fruit.encode()).hexdigest(), 16)

    def _aggregator_idx(self, fruit):
        return self._stable_hash(fruit) % AGGREGATION_AMOUNT

    def _process_data(self, client_id, fruit, amount):
        logging.info(f"Process data")
        client_state = self.amount_by_fruit.setdefault(client_id, {})
        client_state[fruit] = (
            client_state.get(fruit, fruit_item.FruitItem(fruit, 0))
            + fruit_item.FruitItem(fruit, int(amount))
        )

    def _process_eof(self, client_id, remaining):
        logging.info(f"Broadcasting data messages")
        if remaining > 0:
            logging.info(f"[client={client_id}] Propagating EOF (remaining={remaining - 1})")
            self.input_queue.send(
                message_protocol.internal.serialize_eof(client_id, remaining - 1)
            )

        client_state = self.amount_by_fruit.pop(client_id, {})
        logging.info(f"[client={client_id}] Flushing {len(client_state)} fruits to aggregators")
        for fi in client_state.values():
            idx = self._aggregator_idx(fi.fruit)
            self.aggregation_exchanges[idx].send(
                message_protocol.internal.serialize_data(client_id, fi.fruit, fi.amount)
            )

        logging.info(f"[client={client_id}] Sending EOF to all {AGGREGATION_AMOUNT} aggregators")
        for exchange in self.aggregation_exchanges:
            exchange.send(
                message_protocol.internal.serialize_eof(client_id, remaining=0)
            )

    def process_message(self, message, ack, nack):
        msg = message_protocol.internal.deserialize(message)

        if message_protocol.internal.is_data(msg):
            self._process_data(msg["client_id"], msg["fruit"], msg["amount"])
        elif message_protocol.internal.is_eof(msg):
            self._process_eof(msg["client_id"], msg["remaining"])
        else:
            logging.warning(f"Unknown message type: {msg.get('type')}")

        ack()

    def process_data_messsage(self, message, ack, nack):
        fields = message_protocol.internal.deserialize(message)
        if len(fields) == 2:
            self._process_data(*fields)
        else:
            self._process_eof(*fields)
        ack()

    def _handle_sigterm(self, signum, frame):
        logging.info("Received SIGTERM, stopping consumer")
        self._shutting_down = True
        self.input_queue.stop_consuming()

    def start(self):
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        self.input_queue.start_consuming(self.process_message)


def main():
    logging.basicConfig(level=logging.INFO)
    with SumFilter() as sum_filter:
        sum_filter.start()
    return 0


if __name__ == "__main__":
    main()
