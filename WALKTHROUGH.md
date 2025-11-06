# Spark ↔ Kafka pipeline works (end-to-end)

Below is a clear, complete walkthrough of **what runs first**, **how containers talk**, **what each component does**, and **how the Spark job processes and writes data**. I’ll also include **exact run/stop steps** and **where to look for outputs**.

---

## 1) What’s in the project

```
spark-kafka-pipeline/
├─ docker-compose.yml
├─ .env
├─ producer/
│  ├─ Dockerfile
│  ├─ requirements.txt
│  └─ producer.py
└─ spark/
   └─ app.py
```

### The services (in `docker-compose.yml`)

* **zookeeper**: required by your single Kafka broker (classic ZooKeeper mode).
* **kafka**: the Kafka broker (exposes 9092 for containers, 9094 for your Mac host).
* **kafka-init**: one-shot helper that waits for Kafka and ensures the topic exists.
* **spark-master**: Spark master (cluster manager).
* **spark-worker**: Spark worker (executes tasks from the master).
* **spark-streaming**: submits your Spark Structured Streaming app (`spark/app.py`).
* **producer**: Python container that continuously publishes JSON messages to Kafka.

### Shared volumes

* `./data/output:/opt/spark-output` is mounted into **spark-master**, **spark-worker**, and **spark-streaming** so **executors** can write Parquet where the **driver** expects.
* `./cache/ivy:/tmp/.ivy2` caches Spark’s downloaded connectors (Kafka package) so it isn’t re-downloaded each run.

---

## 2) How startup sequencing works (who starts first)

When you run `docker compose up -d --build`, Compose uses `depends_on` and healthchecks to order things:

1. **zookeeper** starts → becomes “healthy”.
2. **kafka** starts (depends on zookeeper healthy).
3. **kafka-init** starts (depends on kafka healthy), then:

   * waits until `kafka-topics --list` responds,
   * creates the topic `${KAFKA_TOPIC:-events}` if it doesn’t exist,
   * exits **0** (successful).
4. **spark-master** starts.
5. **spark-worker** starts (points to `spark://spark-master:7077`).
6. **spark-streaming** starts (depends on kafka-init done + spark-master started).

   * Its **entrypoint** runs a short shell to:

     * set a `RUN_ID`,
     * ensure output and Ivy cache directories exist and are writable,
     * then `exec spark-submit ... /opt/spark-app/app.py`.
7. **producer** starts (depends on kafka-init done), connects to `kafka:9092`, and begins publishing JSON events to the topic.

**Net effect:** by the time your Spark app starts, the topic exists and the producer is ready to send data.

---

## 3) Networking & listeners (why `kafka:9092` vs `localhost:9094`)

* **Inside containers**, clients use `kafka:9092` (the Docker DNS name).
* **On your Mac**, any CLI like `kcat` should use `localhost:9094`.
* That’s configured via:

  * `KAFKA_LISTENERS=PLAINTEXT://:9092, PLAINTEXT_HOST://:9094`
  * `KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092, PLAINTEXT_HOST://localhost:9094`

This dual-listener setup prevents the classic “connection refused/metadata says localhost” problem when mixing container and host clients.

---

## 4) The Producer (where events come from)

**File:** `producer/producer.py`

* Reads env:

  * `KAFKA_BOOTSTRAP_SERVERS=kafka:9092`
  * `KAFKA_TOPIC=events`
  * `MSGS_PER_SEC=5`
* Builds a `KafkaProducer` with JSON value serialization.
* Infinite loop:

  * Generates a message:

    ```json
    {
      "id": <0..9>,            // cycles every 10 messages
      "ts": "<UTC ISO-8601>",  // event time
      "value": <0..100 float>,
      "source": "sim-producer"
    }
    ```
  * Sends to topic as `value` (JSON string) and `key` = `id` as a string.
  * Sleeps `1 / MSGS_PER_SEC` seconds.

**Why keys?** Using a key distributes messages deterministically across partitions and enables key-based aggregations later.

---

## 5) The Spark app (Structured Streaming)

**File:** `spark/app.py`

### 5.1 Session setup & output routing

```python
bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
topic     = os.getenv("KAFKA_TOPIC", "events")

output_base   = os.getenv("OUTPUT_PATH", "/opt/spark-output/parquet").rstrip("/")
run_id        = os.getenv("RUN_ID", str(uuid.uuid4())[:8])  # unique per run if not provided
output_path   = f"{output_base}/run_{run_id}"
checkpoint    = f"{output_path}/_chk"

spark = SparkSession.builder.appName(f"KafkaSparkStreamingDemo-{run_id}").getOrCreate()
```

* Uses `RUN_ID` to write each run into a **fresh folder** (`.../run_<id>`).
  This avoids **checkpoint/schema conflicts** across runs — a common cause of failures.

### 5.2 Define the schema of your incoming JSON

```python
schema = StructType([
  StructField("id",    IntegerType(), False),
  StructField("ts",    StringType(),  False),  # parsed to timestamp later
  StructField("value", DoubleType(),  False),
  StructField("source",StringType(),  True),
])
```

### 5.3 Read from Kafka (as a stream)

```python
raw_df = (spark.readStream
  .format("kafka")
  .option("kafka.bootstrap.servers", bootstrap)
  .option("subscribe", topic)
  .option("startingOffsets", "earliest")  # so you see data even if producer started earlier
  .load())
```

* The `raw_df` has columns like `key` (binary), `value` (binary), `topic`, `partition`, `offset`, `timestamp` (Kafka ingestion time), etc.

### 5.4 Parse JSON and cast fields

```python
parsed_df = (raw_df
  .selectExpr("CAST(key AS STRING)", "CAST(value AS STRING) as json_str")
  .select(col("key"), from_json(col("json_str"), schema).alias("data"))
  .select(
    col("key"),
    col("data.id").alias("id"),
    to_timestamp(col("data.ts")).alias("ts"),  # cast to proper timestamp
    col("data.value").alias("value"),
    col("data.source").alias("source"),
  )
  .na.drop(subset=["ts"]))
```

* Converts Kafka `value` from bytes → string, then parses JSON to columns.
* Converts `ts` to Spark `timestamp`.
* Drops rows with null `ts` for safety.

### 5.5 Debug sink (immediate feedback)

```python
debug_q = (parsed_df.writeStream
  .format("console")
  .option("truncate", "false")
  .outputMode("append")
  .start())
```

* You’ll see **every parsed record** in `docker logs -f spark-streaming` as it arrives.

### 5.6 A small windowed aggregation

```python
from pyspark.sql.functions import window, avg

agg_df = (parsed_df
  .withWatermark("ts", "30 seconds")                 # allow late data for 30s
  .groupBy(window(col("ts"), "10 seconds"), col("id"))
  .agg(avg("value").alias("avg_value"))
  .select(
    col("window.start").alias("window_start"),
    col("window.end").alias("window_end"),
    col("id"),
    col("avg_value")))
```

* **Window:** 10-second tumbling windows on event time `ts`.
* **Watermark:** late events beyond 30s are ignored for aggregation updates.

Console sink for aggregates:

```python
agg_console_q = (agg_df.writeStream
  .outputMode("update")
  .format("console")
  .option("truncate", "false")
  .option("numRows", "50")
  .start())
```

### 5.7 Write raw parsed events to Parquet

```python
parquet_q = (parsed_df.writeStream
  .format("parquet")
  .option("path", output_path)                 # /opt/spark-output/parquet/run_<id>
  .option("checkpointLocation", checkpoint)    # /opt/spark-output/parquet/run_<id>/_chk
  .option("failOnDataLoss", "false")
  .partitionBy("id")                           # organizes data by id=0..9
  .outputMode("append")
  .start())
```

* **Important:** On a cluster, **executors** (running in `spark-worker`) perform the writes. That’s why the **same volume** is mounted on the worker.

### 5.8 Keep the streams alive

```python
spark.streams.awaitAnyTermination()
```

* The driver blocks here until you stop the container or an error occurs.

---

## 6) Why the Ivy cache & volumes matter

* `--packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1` tells Spark to **download** the Kafka connector jar from Maven the first time.
* Docker images don’t ship with that jar, so we:

  * Set `spark.jars.ivy=/tmp/.ivy2`
  * Map it to `./cache/ivy` on host → jars persist between runs.
* We also set `HOME=/tmp` to avoid permission quirks in some base images.

---

## 7) Running the project (exact commands)

From the project root:

```bash
# 0) One-time (make folders)
mkdir -p cache/ivy data/output

# 1) Build and start everything
docker compose up -d --build

# 2) Watch the streaming logs (parsed rows + aggregates)
docker logs -f spark-streaming

# 3) (Optional) Watch producer logs (just a single start line)
docker logs -f producer
```

Check the Spark UIs:

* Master: [http://localhost:8080](http://localhost:8080)
* Worker: [http://localhost:8081](http://localhost:8081)

Parquet shows up here:

```
data/output/parquet/run_<some_id>/id=<n>/part-*.snappy.parquet
```

---

## 8) Stopping, restarting, and validating

**Stop everything:**

```bash
docker compose down
```

**Stop just streaming:**

```bash
docker stop spark-streaming
```

**Restart streaming:**

```bash
docker start -ai spark-streaming
```

## 🧠 How to use locally

After cloning or pulling the repo, you can quickly enable your validation environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Read Parquet (Pandas):**

```bash
pip install pandas pyarrow
python - <<'PY'
import pandas as pd
df = pd.read_parquet("data/output/parquet/run_<your_id>/")
print(df.head())
df.to_csv("data/output/validation_output.csv", index=False)
PY
```

**Convert Parquet → CSV (Spark locally):**

```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.getOrCreate()
df = spark.read.parquet("data/output/parquet/run_<your_id>/")
df.coalesce(1).write.mode("overwrite").option("header","true").csv("data/output/csv_output")
```

---

## 9) Common issues already solved (and why this code prevents them)

* **Kafka topic doesn’t exist** → `kafka-init` creates it before anything else.
* **Ivy cache permission errors** → use `/tmp/.ivy2` mapped to `./cache/ivy`.
* **Parquet writes fail with “RpcEnv stopped”** → executors couldn’t write to driver’s path. Fixed by mounting `./data/output` into **spark-worker** (and optionally master).
* **Stale checkpoint conflicts** → each run writes to `parquet/run_<RUN_ID>`; we also remove the old default `_chk` once at start.

---

## 10) TL;DR flow chart

1. `docker compose up -d --build`
2. `zookeeper` → `kafka` → `kafka-init` (creates topic)
3. `spark-master` + `spark-worker` start
4. `spark-streaming` runs **entrypoint** → `spark-submit` → runs `spark/app.py`
5. `producer` sends JSON to Kafka topic
6. Spark:

   * reads Kafka stream
   * parses JSON → `parsed_df`
   * prints rows to console
   * computes 10s window averages → prints to console
   * writes raw parsed events to **Parquet**, partitioned by `id`
7. You validate Parquet (Pandas, CSV, etc.)
8. Stop with `docker compose down` (data remains in `./data/output/`)

---
