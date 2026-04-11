import pika
import pika.exceptions
from .middleware import (
    MessageMiddlewareQueue,
    MessageMiddlewareExchange,
    MessageMiddlewareDisconnectedError,
    MessageMiddlewareMessageError,
    MessageMiddlewareCloseError,
)

_PIKA_CONNECTION_ERRORS = (
    pika.exceptions.AMQPConnectionError,
    pika.exceptions.StreamLostError,
    pika.exceptions.ChannelWrongStateError,
    pika.exceptions.ConnectionClosedByBroker,
)


class MessageMiddlewareQueueRabbitMQ(MessageMiddlewareQueue):

    def __init__(self, host, queue_name):
        self._queue_name = queue_name
        self._connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=host)
        )
        self._channel = self._connection.channel()
        # durable para que la cola sobreviva reinicios del broker
        self._channel.queue_declare(queue=queue_name, durable=True)

    def send(self, message):
        # publico directo a la cola (default exchange)
        # delivery_mode=2 indica mensaje persistente en disco
        try:
            self._channel.basic_publish(
                exchange="",
                routing_key=self._queue_name,
                body=message,
                properties=pika.BasicProperties(delivery_mode=2),
            )
        except _PIKA_CONNECTION_ERRORS as e:
            raise MessageMiddlewareDisconnectedError(e)
        except Exception as e:
            raise MessageMiddlewareMessageError(e)

    def start_consuming(self, on_message_callback):
        def _on_message(ch, method, properties, body):
            ack = lambda: ch.basic_ack(delivery_tag=method.delivery_tag)
            nack = lambda: ch.basic_nack(delivery_tag=method.delivery_tag)
            on_message_callback(body, ack, nack)

        try:
            self._channel.basic_consume(
                queue=self._queue_name,
                on_message_callback=_on_message,
            )
            # bloquea hasta que se llame a stop_consuming() desde el callback
            self._channel.start_consuming()
        except _PIKA_CONNECTION_ERRORS as e:
            raise MessageMiddlewareDisconnectedError(e)
        except Exception as e:
            raise MessageMiddlewareMessageError(e)

    def stop_consuming(self):
        try:
            self._channel.stop_consuming()
        except _PIKA_CONNECTION_ERRORS as e:
            raise MessageMiddlewareDisconnectedError(e)

    def close(self):
        try:
            self._connection.close()
        except Exception as e:
            raise MessageMiddlewareCloseError(e)


# edit: uso direct para simplificar, pero se podría usar topic en caso de que se necesiten patrones de routing keys más complejos
class MessageMiddlewareExchangeRabbitMQ(MessageMiddlewareExchange):

    def __init__(self, host, exchange_name, routing_keys):
        self._exchange_name = exchange_name
        self._routing_keys = routing_keys
        self._connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=host)
        )
        self._channel = self._connection.channel()
        self._channel.exchange_declare(
            exchange=exchange_name,
            exchange_type="direct",
            durable=True,
        )
        # queue exclusiva para el consumer
        # cada instancia obtiene un nombre único generado por RabbitMQ.
        result = self._channel.queue_declare(queue="", exclusive=True)
        self._queue_name = result.method.queue
        # bindeamos la cola al exchange por cada routing key de interés
        for key in routing_keys:
            self._channel.queue_bind(
                exchange=exchange_name,
                queue=self._queue_name,
                routing_key=key,
            )

    def send(self, message):
        # publico al exchange con la primera routing key de la lista
        try:
            self._channel.basic_publish(
                exchange=self._exchange_name,
                routing_key=self._routing_keys[0],
                body=message,
            )
        except _PIKA_CONNECTION_ERRORS as e:
            raise MessageMiddlewareDisconnectedError(e)
        except Exception as e:
            raise MessageMiddlewareMessageError(e)

    def start_consuming(self, on_message_callback):
        def _on_message(ch, method, properties, body):
            ack = lambda: ch.basic_ack(delivery_tag=method.delivery_tag)
            nack = lambda: ch.basic_nack(delivery_tag=method.delivery_tag)
            on_message_callback(body, ack, nack)

        try:
            self._channel.basic_consume(
                queue=self._queue_name,
                on_message_callback=_on_message,
            )
            self._channel.start_consuming()
        except _PIKA_CONNECTION_ERRORS as e:
            raise MessageMiddlewareDisconnectedError(e)
        except Exception as e:
            raise MessageMiddlewareMessageError(e)

    def stop_consuming(self):
        try:
            self._channel.stop_consuming()
        except _PIKA_CONNECTION_ERRORS as e:
            raise MessageMiddlewareDisconnectedError(e)

    def close(self):
        try:
            self._connection.close()
        except Exception as e:
            raise MessageMiddlewareCloseError(e)
