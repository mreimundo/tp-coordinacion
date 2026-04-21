# Trabajo Práctico - Coordinación

En este trabajo se busca familiarizar a los estudiantes con los desafíos de la coordinación del trabajo y el control de la complejidad en sistemas distribuidos. Para tal fin se provee un esqueleto de un sistema de control de stock de una verdulería y un conjunto de escenarios de creciente grado de complejidad y distribución que demandarán mayor sofisticación en la comunicación de las partes involucradas.

## Ejecución

`make up` : Inicia los contenedores del sistema y comienza a seguir los logs de todos ellos en un solo flujo de salida.

`make down`:   Detiene los contenedores y libera los recursos asociados.

`make logs`: Sigue los logs de todos los contenedores en un solo flujo de salida.

`make test`: Inicia los contenedores del sistema, espera a que los clientes finalicen, compara los resultados con una ejecución serial y detiene los contenederes.

`make switch`: Permite alternar rápidamente entre los archivos de docker compose de los distintos escenarios provistos.

## Elementos del sistema objetivo

![ ](./imgs/diagrama_de_robustez.jpg  "Diagrama de Robustez")
*Fig. 1: Diagrama de Robustez*

### Client

Lee un archivo de entrada y envía por TCP/IP pares (fruta, cantidad) al sistema.
Cuando finaliza el envío de datos, aguarda un top de pares (fruta, cantidad) y vuelca el resultado en un archivo de salida csv.
El criterio y tamaño del top dependen de la configuración del sistema. Por defecto se trata de un top 3 de frutas de acuerdo a la cantidad total almacenada.

### Gateway

Es el punto de entrada y salida del sistema. Intercambia mensajes con los clientes y las colas internas utilizando distintos protocolos.

### Sum
 
Recibe pares  (fruta, cantidad) y aplica la función Suma de la clase `FruitItem`. Por defecto esa suma es la canónica para los números enteros, ej:

`("manzana", 5) + ("manzana", 8) = ("manzana", 13)`

Pero su implementación podría modificarse.
Cuando se detecta el final de la ingesta de datos envía los pares (fruta, cantidad) totales a los Aggregators.

### Aggregator

Consolida los datos de las distintas instancias de Sum.
Cuando se detecta el final de la ingesta, se calcula un top parcial y se envía esa información al Joiner.

### Joiner

Recibe tops parciales de las instancias del Aggregator.
Cuando se detecta el final de la ingesta, se envía el top final hacia el gateway para ser entregado al cliente.

## Limitaciones del esqueleto provisto

La implementación base respeta la división de responsabilidades de los distintos controles y hace uso de la clase `FruitItem` como un elemento opaco, sin asumir la implementación de las funciones de Suma y Comparación.

No obstante, esta implementación no cubre los objetivos buscados tal y como es presentada. Entre sus falencias puede destactarse que:

 - No se implementa la interfaz del middleware. 
 - No se dividen los flujos de datos de los clientes más allá del Gateway, por lo que no se es capaz de resolver múltiples consultas concurrentemente.
 - No se implementan mecanismos de sincronización que permitan escalar los controles Sum y Aggregator. En particular:
   - Las instancias de Sum se dividen el trabajo, pero solo una de ellas recibe la notificación de finalización en la ingesta de datos.
   - Las instancias de Sum realizan _broadcast_ a todas las instancias de Aggregator, en lugar de agrupar los datos por algún criterio y evitar procesamiento redundante.
  - No se maneja la señal SIGTERM, con la salvedad de los clientes y el Gateway.

## Condiciones de Entrega

El código de este repositorio se agrupa en dos carpetas, una para Python y otra para Golang. Los estudiantes deberán elegir **sólo uno** de estos lenguajes y realizar una implementación que funcione correctamente ante cambios en la multiplicidad de los controles (archivo de docker compose), los archivos de entrada y las implementaciones de las funciones de Suma y Comparación del `FruitItem`.

![ ](./imgs/mutabilidad.jpg  "Mutabilidad de Elementos")
*Fig. 2: Elementos mutables e inmutables*

A modo de referencia, en la *Figura 2* se marcan en tonos oscuros los elementos que los estudiantes no deben alterar y en tonos claros aquellos sobre los que tienen libertad de decisión.
Al momento de la evaluación y ejecución de las pruebas se **descartarán** o **reemplazarán** :

- Los archivos de entrada de la carpeta `datasets`.
- El archivo docker compose principal y los de la carpeta `scenarios`.
- Todos los archivos Dockerfile.
- Todo el código del cliente.
- Todo el código del gateway, salvo `message_handler`.
- La implementación del protocolo de comunicación externo y `FruitItem`.

Redactar un breve informe explicando el modo en que se coordinan las instancias de Sum y Aggregation, así como el modo en el que el sistema escala respecto a los clientes y a la cantidad de controles.


## Informe de resolución

### Introducción
**Nota**: La implementación de este proyecto se realizó en Python, por lo que todos los cambios se incluyen sobre la carpeta `/python`

Como vimos en el enunciado, el sistema implementa un pipeline de conteo y ranking de stock de clientes que podrían ser una verdulería. Múltiples clientes envían pares `(fruta, cantidad)` al sistema, este los procesa y devuelve un top-N de las frutas con mayor cantidad total acumulada (o según el criterio que se defina en el archivo del `FruitItem`). Según la figura 1 tenemos entonces un esquema similar a


```
[Cliente 1]─┐                                                    ┌─[Cliente 1]
[Cliente 2]─┤---TCP--->[Gateway]-->[input_queue]-->[Sum×M]-->[Aggregation×K]-->[Join]-->[results_queue]-->[Gateway]-->...
[Cliente N]─┘                                                    └─[Cliente N]
```

Todos los nodos del pipeline son stateless respecto a la configuración, es decir leen sus parámetros del entorno (`SUM_AMOUNT`, `AGGREGATION_AMOUNT`, `AGGREGATION_PREFIX`, `nombre_cliente`), lo que permite escalar cada entidad modificando únicamente el archivo `docker-compose.yaml`. A modo de prueba en este TP, podemos observar este cambio con el `make switch` que contempla los casos de la carpeta `/python/scenarios`.

---

### Coordinación entre Sum y Aggregation

Uno de los puntos más fundamentales del trabajo radica en la lógica de coordinación y distribución entre las entidades Sum y Aggregation. A continuación se describe como se implementó con el fin de cumplir lo solicitado:

#### Sum

Cada instancia de Sum consume de la **working queue** `input_queue` compartida. El middleware (el que ha sido implementado en el TP previo usando RabbitMQ) reparte los mensajes en round-robin entre todas las instancias activas. Cada instancia acumula en memoria un diccionario `{client_id → {fruta → FruitItem}}` con las sumas parciales de los datos que recibió.

#### Aggregation

Cada instancia de Aggregation tiene una **cola dedicada** `{AGGREGATION_PREFIX}_{ID}`. Aquí se acumulan los datos que recibe en un diccionario `{client_id → {fruta → FruitItem}}` y lleva un contador de EOFs recibidos por cliente. Cuando el contador alcanza `SUM_AMOUNT`, significa que todos los Sum terminaron para ese cliente, y se procede a ordenar el diccionario y emitir el top parcial hacia Join.

#### Routing Sum -> Aggregation

Con este enfoque, una misma fruta puede ser procesada por cualquier instancia de Sum. Para que su suma total sea correcta, todas las contribuciones parciales de esa fruta deben confluir en **el mismo Aggregator**, y para que no haya errores hubo que agregar una lógica de ruteo. El criterio de routing es:

```
índice = zlib.crc32(fruta.encode()) % AGGREGATION_AMOUNT
```

Se decidió utilizar `zlib` ya que es determinístico entre procesos (a diferencia del `hash()` built-in de Python, cuya semilla varía por proceso con `PYTHONHASHSEED`). Todas las instancias de Sum calculan el mismo índice para la misma fruta y envían sus datos a la queue `{AGGREGATION_PREFIX}_{índice}`.

Esto garantiza que el diccionario de cada Aggregator contiene, al momento del EOF, **todas** las sumas parciales de las frutas que le corresponden, sin duplicados entre Aggregators. Ejemplo básico de ilustración:

```
sum_0: manzana=100  ------------------------> aggr_1
sum_1: manzana=300  --- crc32("manzana") % 3 = 1 ----> aggr_1   <- suma total correcta
sum_2: manzana=200  ------------------------> aggr_1
```

#### Coordinación de EOF entre instancias de Sum

Una vez resuelto el routing de datos, surge el problema de **cuándo y cómo cada instancia de Sum sabe que terminó la ingesta de un cliente (EOF)**. El gateway inyecta un único EOF por cliente a la working queue, y el middleware lo entrega a una sola instancia. Las demás tienen datos acumulados para ese cliente que nunca enviarían si no se las notifica.

Una primera aproximación fue propagar el EOF por la misma working queue, de forma decremental, o sea la instancia que lo recibe lo republica con un contador `remaining` decrementado, de manera que cada Sum eventualmente consuma un EOF. El problema de este approach era que la misma instancia puede consumir todos los EOFs republicados que la misma publicó, especialmente si la queue está casi vacía o si una instancia es más rápida que las otras. En la práctica ocurría que el sistema pasaba los escenarios 1 y 2 (un solo Sum) pero fallaba en el 3 en adelante, ya que una sola instancia acaparaba todos los EOFs y las demás nunca hacían flush. Los resultados eran aproximadamente 1/3 de lo esperado.

La solución adoptada fue un **broadcast peer-to-peer via colas dedicadas de EOF**:

1. La instancia que recibe el EOF original publica una copia en `{SUM_PREFIX}_{i}_eof` para cada `i ∈ [0, SUM_AMOUNT)`.
2. Cada instancia consume únicamente su propia cola `{SUM_PREFIX}_{ID}_eof`, garantizando la entrega uno a uno sin competencia.
3. Al recibir la notificación, cada instancia hace flush de sus acumulados hacia los Aggregators (por consistent hash) y envía un EOF a cada uno. Esta idea se puede evidenciar en el esquema

```
Gateway --> [input_queue] --> Sum_k (recibe EOF original)
                                    |
                    .---------------+---------------.
                    v               v               v
              [sum_0_eof]     [sum_1_eof]     [sum_2_eof]
                    |               |               |
                  Sum_0           Sum_1           Sum_2
                (flush)         (flush)         (flush)
                    |               |               |
                    '---------------+---------------'
                                    |
                        cada Sum envía EOF a cada Aggregator
```

Las colas de EOF se consumen en el **mismo channel** que `input_queue` mediante `add_queue_consumer` (ver Apéndice B), por lo que `pika` las multiplexa en un único `start_consuming()` sin necesitar threads adicionales dentro del proceso. Esto preserva el procesamiento secuencial de mensajes y race conditions entre los nodos. El flush de un cliente solo ocurre después de que todos sus datos ya fueron procesados, ya que el EOF de la cola dedicada fue publicado luego de que el EOF de la working queue fue consumido y recibido 'ack'.

#### Coordinación de EOF en Aggregation -> Join

Cada Aggregator lleva la cuenta de cuántos EOFs recibió por cliente. Cuando ese contador llega a `SUM_AMOUNT`, sabe que todos los Sum terminaron de flushear sus datos para ese cliente. En ese momento ordena su diccionario acumulado (usando la comparación de `FruitItem`, que es opaca) y emite su top parcial hacia Join.

Join, a su vez, acumula los tops parciales en `{client_id -> [parciales]}` y emite el resultado final cuando recibe `AGGREGATION_AMOUNT` tops para ese cliente. El merge concatena todos los parciales, construye `FruitItem` para reutilizar la función de comparación, y toma los primeros `TOP_SIZE`.

Como el consistent hash garantiza que cada fruta es manejada por un único Aggregator, los tops parciales son **disjuntos** entre sí, es decir no hay duplicados en el merge. Esto simplifica enormemente la lógica de Join, que no necesita detectar ni resolver colisiones.

---

### Escalabilidad

#### Respecto a clientes

Cada cliente genera un `client_id` único en el `MessageHandler` del Gateway (UUID), que se inyecta en todos los mensajes del protocolo interno. Todos los nodos del pipeline mantienen estado **separado por `client_id`**: Sum con `amount_by_fruit[client_id]`, Aggregation con `fruit_items[client_id]` y `eof_count[client_id]`, Join con `partial_tops[client_id]`. Múltiples clientes pueden estar en vuelo simultáneamente sin interferencia entre sus datos.

Se consideró también un enfoque -mencionado de manera similar en el foro por un compañero- alternativo de **sticky routing por cliente** (hash del `client_id` para determinar el Aggregator destino), de manera que toda la ejecución de un cliente vaya siempre al mismo Aggregator. La ventaja es que requiere menos coordinación entre instancias (el Aggregator ya tiene el top global, no parcial). Sin embargo se pensó que este enfoque no aprovecha bien el paralelismo: si hay un único cliente activo, solo uno de los K Aggregators trabaja, independientemente de cuántas réplicas existan. Para el perfil del sistema (pocos clientes, gran volumen de datos por cliente) el routing por fruta es más adecuado, ya que distribuye la carga de procesamiento entre todos los Aggregators disponibles.

#### Respecto a la cantidad de nodos Sum

Aumentar `SUM_AMOUNT` en el docker-compose agrega réplicas que compiten por la working queue. El sistema adapta su coordinación automáticamente, ya que se implementó de la siguiente manera:
- El broadcast de EOF crea exactamente `SUM_AMOUNT` colas de notificación.
- Cada Aggregator espera `SUM_AMOUNT` EOFs antes de emitir su top parcial.
- El consistent hash garantiza que el routing fruta→Aggregator es correcto independientemente de cuántos Sum existan.

#### Respecto a la cantidad de nodos Aggregation

Aumentar `AGGREGATION_AMOUNT` redistribuye el espacio de frutas entre más Aggregators. El consistent hash asegura que cada fruta siempre va al mismo Aggregator, incluso con más nodos: Sum crea tantas queues de salida como Aggregators haya (leído de `AGGREGATION_AMOUNT`), y Join espera `AGGREGATION_AMOUNT` tops parciales antes de emitir el resultado final.

Durante el desarrollo se intentó usar el `hash()` built-in de Python para el consistent hash. Esto funcionaba correctamente en escenarios con un solo Aggregator (`hash(f) % 1 = 0` siempre), pero fallaba con múltiples Aggregators porque Python asigna una semilla aleatoria distinta por proceso (`PYTHONHASHSEED`): la misma fruta daba índices distintos en `sum_0`, `sum_1` y `sum_2`, rompiendo el invariante del consistent hash. Los síntomas en el make test eran totales incorrectos y resultados mezclados entre frutas. Se reemplazó por `zlib.crc32`, que es determinístico, no criptográfico, y forma parte de la biblioteca estándar.

El sistema soporta cualquier combinación de `SUM_AMOUNT × AGGREGATION_AMOUNT` sin cambios de código, solo ajustando el docker-compose.

#### Almacenamiento en memoria

Todos los datos intermedios se mantienen en memoria RAM (diccionarios Python indexados por `client_id`). El footprint crece linealmente con la cantidad de frutas únicas × clientes activos simultáneamente. Como indicó el equipo docente, persistir los acumulados a disco sería una mejora natural para volúmenes grandes, pero está fuera del alcance de este TP.

---
### Otros cambios registrados (apéndices)
#### Apéndice A — Protocolo interno

El protocolo interno (`common/message_protocol/internal.py`) reemplaza el formato de lista del skeleton (`[fruta, cantidad]` / `[]`) por mensajes JSON con tipo `MsgType` explícito y `client_id`:

```json
// data
{"type": "data", "client_id": "uuid", "fruit": "manzana", "amount": 5}

// EOF generado por el gateway (remaining null: Sum lo inicializa)
{"type": "eof",  "client_id": "uuid", "remaining": null}

// EOF del broadcast inter-Sum
{"type": "eof",  "client_id": "uuid", "remaining": 0}

// resultado: Aggregation -> Join -> Gateway
{"client_id": "uuid", "fruit_top": [["manzana", 42], ["pera", 38]]}
```

El campo `remaining` fue la primera aproximación para coordinar el EOF entre Sums (propagación decremental por la working queue). Fue reemplazado por el mecanismo de broadcast via colas dedicadas descrito en la sección anterior, que resuelve el race condition donde una sola instancia podía acaparar todos los EOFs.

#### Apéndice B — Middleware

Se agregó el método `add_queue_consumer(queue_name, callback)` a `MessageMiddlewareQueueRabbitMQ`. Registra un consumer adicional en el **mismo channel** de la instancia, declarando la cola como durable antes de suscribirse. El objetivo es permitir que Sum consuma de `input_queue` (datos) y de `{SUM_PREFIX}_{ID}_eof` (notificaciones de fin) en un único `start_consuming()`, sin threads adicionales.

Todas las interfaces abstractas del skeleton (`MessageMiddleware`, `MessageMiddlewareQueue`, `MessageMiddlewareExchange`) se preservaron sin modificación.