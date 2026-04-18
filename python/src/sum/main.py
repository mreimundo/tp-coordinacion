import os
import logging
import signal
import zlib

from common import middleware, message_protocol, fruit_item

ID = int(os.environ["ID"])
MOM_HOST = os.environ["MOM_HOST"]
INPUT_QUEUE = os.environ["INPUT_QUEUE"]
SUM_AMOUNT = int(os.environ["SUM_AMOUNT"])
SUM_PREFIX = os.environ["SUM_PREFIX"]
AGGREGATION_AMOUNT = int(os.environ["AGGREGATION_AMOUNT"])
AGGREGATION_PREFIX = os.environ["AGGREGATION_PREFIX"]


class SumFilter:
    def __init__(self):
        self.shutting_down = False

        self.input_queue = middleware.MessageMiddlewareQueueRabbitMQ(
            MOM_HOST, INPUT_QUEUE
        )
        # cola dedicada para recibir los EOFs de broadcast de otros Sum
        # registro en el mismo channel que input para que start_consuming() los multiplexe sin threads. 
        self._eof_queue_name = f"{SUM_PREFIX}_{ID}_eof"
        self.input_queue.add_queue_consumer(
            self._eof_queue_name, self.process_eof_notification
        )

        # colas para publicar el EOF a CADA instancia de Sum (incluyendo self)
        self.peer_eof_queues = [
            middleware.MessageMiddlewareQueueRabbitMQ(
                MOM_HOST, f"{SUM_PREFIX}_{i}_eof"
            )
            for i in range(SUM_AMOUNT)
        ]

        # una queue de salida por aggregator (index determinado por hash).
        self.aggregation_queues = [
            middleware.MessageMiddlewareQueueRabbitMQ(
                MOM_HOST, f"{AGGREGATION_PREFIX}_{i}"
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
        try:
            self.input_queue.close()
            for q in self.peer_eof_queues:
                q.close()
            for q in self.aggregation_queues:
                q.close()
        except Exception as e:
            logging.error(f"Error closing connections: {e}")

    def _aggregator_idx(self, fruit):
        return hash(fruit) % AGGREGATION_AMOUNT

    def _process_data(self, client_id, fruit, amount):
        logging.info(f"Process data")
        client_state = self.amount_by_fruit.setdefault(client_id, {})
        client_state[fruit] = (
            client_state.get(fruit, fruit_item.FruitItem(fruit, 0))
            + fruit_item.FruitItem(fruit, int(amount))
        )

    def _broadcast_eof(self, client_id):
        logging.info(f"Broadcasting data messages")
        logging.info(f"[client={client_id}] Broadcasting EOF to {SUM_AMOUNT} peer queues")
        for q in self.peer_eof_queues:
            q.send(message_protocol.internal.serialize_eof(client_id, remaining=0))

    def _flush_client(self, client_id):
        client_state = self.amount_by_fruit.pop(client_id, {})
        logging.info(f"[client={client_id}] Flushing {len(client_state)} fruits")
        for fi in client_state.values():
            idx = self._aggregator_idx(fi.fruit)
            self.aggregation_queues[idx].send(
                message_protocol.internal.serialize_data(client_id, fi.fruit, fi.amount)
            )
        logging.info(f"[client={client_id}] Sending EOF to {AGGREGATION_AMOUNT} aggregators")
        for q in self.aggregation_queues:
            q.send(message_protocol.internal.serialize_eof(client_id, remaining=0))

    def process_eof_notification(self, message, ack, nack):
        try:
            msg = message_protocol.internal.deserialize(message)
            if message_protocol.internal.is_eof(msg):
                self._flush_client(msg["client_id"])
            ack()
        except Exception as e:
            logging.error(f"Error processing EOF notification: {e}")
            nack()

    def process_message(self, message, ack, nack):
        try:
            msg = message_protocol.internal.deserialize(message)
            if message_protocol.internal.is_data(msg):
                self._process_data(msg["client_id"], msg["fruit"], msg["amount"])
            elif message_protocol.internal.is_eof(msg):
                self._broadcast_eof(msg["client_id"])
            else:
                logging.warning(f"Unknown message type: {msg.get('type')}")

            ack()
        except Exception as e:
            logging.error(f"Error processing message: {e}")
            nack()

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
