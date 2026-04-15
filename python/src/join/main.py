import os
import logging
import signal

from common import middleware, message_protocol, fruit_item

MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
OUTPUT_QUEUE = os.environ["OUTPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
SUM_PREFIX = os.environ["SUM_PREFIX"]
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]
TOP_SIZE = int(os.environ["TOP_SIZE"])


class JoinFilter:

    def __init__(self):
        self._shutting_down = False

        self.input_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, INPUT_QUEUE
        )
        self.output_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, OUTPUT_QUEUE
        )

        self.partial_tops = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def close(self):
        try:
            self.input_queue.close()
            self.output_queue.close()
        except Exception as e:
            logging.error(f"Error closing connections: {e}")

    def _merge_tops(self, partials):
        all_items = [
            fruit_item.FruitItem(fruit, amount)
            for partial in partials
            for (fruit, amount) in partial
        ]
        all_items.sort(reverse=True)
        return [(fi.fruit, fi.amount) for fi in all_items[:TOP_SIZE]]

    def _process_partial_top(self, client_id, partial_top):
        tops = self.partial_tops.setdefault(client_id, [])
        tops.append(partial_top)
        count = len(tops)
        logging.info(f"[client={client_id}] Partial top {count}/{AGGREGATION_AMOUNT}")

        if count < AGGREGATION_AMOUNT:
            return

        partials = self.partial_tops.pop(client_id)
        final_top = self._merge_tops(partials)
        logging.info(f"[client={client_id}] Sending final top ({len(final_top)} items)")
        self.output_queue.send(
            message_protocol.internal.serialize(
                {"client_id": client_id, "fruit_top": final_top}
            )
        )

    def process_message(self, message, ack, nack):
        logging.info("Received top")
        try:
            msg = message_protocol.internal.deserialize(message)
            client_id   = msg.get("client_id")
            partial_top = msg.get("fruit_top")

            if client_id is None or partial_top is None:
                logging.warning(f"Malformed message: {msg}")
                ack()
                return

            self._process_partial_top(client_id, partial_top)
            ack()
        except Exception as e:
            logging.error(f"Error processing message: {e}")
            nack()

    def _handle_sigterm(self, signum, frame):
        logging.info("Received SIGTERM, stopping")
        self._shutting_down = True
        self.input_queue.stop_consuming()

    def start(self):
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        self.input_queue.start_consuming(self.process_message)


def main():
    logging.basicConfig(level=logging.INFO)
    with JoinFilter() as join_filter:
        join_filter.start()

    return 0


if __name__ == "__main__":
    main()
