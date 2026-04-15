import os
import signal
import logging
import bisect

from common import middleware, message_protocol, fruit_item

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
OUTPUT_QUEUE = os.environ["OUTPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
SUM_PREFIX = os.environ["SUM_PREFIX"]
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]
TOP_SIZE = int(os.environ["TOP_SIZE"])


class AggregationFilter:

    def __init__(self):
        self.input_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, f"{AGGREGATION_PREFIX}_{ID}"
        )
        self.output_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, OUTPUT_QUEUE
        )
        # lo paso a diccionario para que sea por client_id
        self.fruit_top = {}
        self.eof_count = {}

    def _process_data(self, client_id, fruit, amount):
        logging.info("Processing data message")
        top = self.fruit_top.setdefault(client_id, [])
        for i, fi in enumerate(top):
            if fi.fruit == fruit:
                top[i] = fi + fruit_item.FruitItem(fruit, amount)
                return
        bisect.insort(top, fruit_item.FruitItem(fruit, amount))

    def _process_eof(self, client_id):
        logging.info("Received EOF")
        count = self.eof_count.get(client_id, 0) + 1
        self.eof_count[client_id] = count
        logging.info(f"[client={client_id}] EOF {count}/{SUM_AMOUNT}")

        if count < SUM_AMOUNT:
            return

        # todos los Sum terminaron para este cliente: calculo un top parcial.
        top = self.fruit_top.pop(client_id, [])
        del self.eof_count[client_id]

        partial = [
            (fi.fruit, fi.amount)
            for fi in reversed(top[-TOP_SIZE:])
        ]
        logging.info(f"[client={client_id}] Sending partial top ({len(partial)} items) to Join")
        self.output_queue.send(
            message_protocol.internal.serialize(
                {"client_id": client_id, "fruit_top": partial}
            )
        )

    def process_messsage(self, message, ack, nack):
        logging.info("Process message")
        msg = message_protocol.internal.deserialize(message)

        if message_protocol.internal.is_data(msg):
            self._process_data(msg["client_id"], msg["fruit"], msg["amount"])
        elif message_protocol.internal.is_eof(msg):
            self._process_eof(msg["client_id"])
        else:
            logging.warning(f"Unknown message type: {msg.get('type')}")

        ack()

    def _handle_sigterm(self, signum, frame):
        logging.info("Received SIGTERM, stopping")
        self._shutting_down = True
        self.input_queue.stop_consuming()

    def start(self):
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        self.input_queue.start_consuming(self.process_message)


def main():
    logging.basicConfig(level=logging.INFO)
    with AggregationFilter() as aggregation_filter:
        aggregation_filter.start()
    return 0


if __name__ == "__main__":
    main()
