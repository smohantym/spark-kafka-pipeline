import json
import os
import random
import time
from datetime import datetime, timezone
from kafka import KafkaProducer

bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
topic = os.getenv("KAFKA_TOPIC", "events")
msgs_per_sec = float(os.getenv("MSGS_PER_SEC", "5"))

producer = KafkaProducer(
    bootstrap_servers=bootstrap,
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    key_serializer=lambda v: str(v).encode("utf-8"),
    linger_ms=50,
    retries=5,
)

print(f"Producing to {bootstrap} topic={topic} at ~{msgs_per_sec} msg/s")

i = 0
try:
    while True:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": i % 10,                # 10 keys to allow grouping
            "ts": now,
            "value": round(random.uniform(0, 100), 3),
            "source": "sim-producer"
        }
        producer.send(topic, key=str(payload["id"]), value=payload)
        i += 1
        # pacing
        time.sleep(1.0 / msgs_per_sec)
except KeyboardInterrupt:
    pass
finally:
    producer.flush()
    print("Producer stopped.")
