# 🧩 Spark + Kafka Streaming Data Pipeline (Dockerized)

A complete **end-to-end data engineering mini-project** built with **Apache Spark Structured Streaming** and **Apache Kafka**, all running locally inside Docker containers.

The pipeline demonstrates:
- Real-time data ingestion using Kafka.
- Stream processing and aggregation using Spark.
- Data persistence to Parquet.
- Easy validation using Pandas or PySpark.

## 🚀 Architecture Overview
```
        ┌────────────┐
        │  Producer  │──► sends JSON messages
        └─────┬──────┘
              │
    Kafka Topic: `events`
              │
    ┌─────────▼─────────┐
    │     Kafka Broker  │
    └─────────┬─────────┘
              │
      consumed by
              │
    ┌─────────▼─────────┐
    │  Spark Streaming  │──► Console (debug)
    │  (Structured API) │──► Parquet (partitioned by id)
    └─────────┬─────────┘
              │
      written to volume
              │
   ./data/output/parquet/run_<id>/
```
## ⚙️ Components
````
| Service | Description |
|----------|-------------|
| **zookeeper** | Required by Kafka broker (coordination service). |
| **kafka** | Kafka broker for message streaming (`localhost:9094`). |
| **kafka-init** | One-shot helper that ensures the Kafka topic exists before Spark starts. |
| **spark-master** | Spark master node managing the cluster. |
| **spark-worker** | Spark worker executing tasks. |
| **spark-streaming** | Spark Structured Streaming job that reads from Kafka, processes data, and writes Parquet. |
| **producer** | Python script producing simulated JSON events to Kafka at a controlled rate. |
````

## 🧰 Prerequisites
````
- Docker ≥ **28.5.1**
- Docker Compose v2 (already included in Docker Desktop)
- Mac M1/M2/M3 or Linux system
````

## 🏗️ Setup and Run

### 1️⃣ Clone this repository
```bash
git clone https://github.com/your-username/spark-kafka-pipeline.git
cd spark-kafka-pipeline
````

### 🧠 How to use locally

After cloning or pulling the repo, you can quickly enable your validation environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2️⃣ Create required folders

```bash
mkdir -p cache/ivy data/output
```

### 3️⃣ Start the full stack

```bash
docker compose up -d --build
```

> 🟢 This builds all containers and starts them in the correct order:
> Zookeeper → Kafka → kafka-init → Spark master/worker → Spark streaming → Producer

### 4️⃣ Check running services

```bash
docker ps
```

### 5️⃣ View logs

* **Spark Streaming output (parsed + aggregated events):**

  ```bash
  docker logs -f spark-streaming
  ```

* **Producer logs (data generation rate):**

  ```bash
  docker logs -f producer
  ```

---

## 📊 What Happens Internally

1. **`producer`** sends JSON messages every 0.2s:

   ```json
   {
     "id": 5,
     "ts": "2025-11-06T07:01:12.122Z",
     "value": 53.122,
     "source": "sim-producer"
   }
   ```

2. **Kafka broker** receives the stream on topic `events` (3 partitions).

3. **`spark-streaming`** job:

   * Reads from Kafka using `spark-sql-kafka` connector.
   * Parses JSON values into structured columns.
   * Outputs to console for debugging.
   * Aggregates by 10-second time windows with a 30-second watermark.
   * Writes raw parsed data to Parquet in `./data/output/parquet/run_<id>/`.

4. **`spark-worker`** executes actual write tasks (because Spark runs in cluster mode).

5. Parquet files are partitioned by `id` for efficient reads.

---

## 🧾 Output Example

```
data/output/parquet/run_1730874341/
├── _chk/                   # checkpoint metadata
├── id=0/
│   ├── part-00000-....snappy.parquet
├── id=1/
│   ├── part-00000-....snappy.parquet
...
```

Each run has a unique timestamped folder (`run_<id>`) to avoid checkpoint conflicts.

---

## 🧠 Validation Options
### 🐼 **Using Pandas**
```python
import pandas as pd

df = pd.read_parquet("data/output/parquet/run_<your_id>/")
print(df.head())
df.to_csv("data/output/validation_output.csv", index=False)
```

### 🧪 **Using PySpark locally**

```python
from pyspark.sql import SparkSession

spark = SparkSession.builder.appName("ValidateParquet").getOrCreate()
df = spark.read.parquet("data/output/parquet/run_<your_id>/")
df.show(5)
```

---

## 🧹 Stopping and Cleaning Up

### Stop everything (recommended)

```bash
docker compose down
```

### Stop only Spark Streaming

```bash
docker stop spark-streaming
```

### Clean data and rebuild from scratch

```bash
docker compose down -v
rm -rf data/output cache/ivy
docker compose up -d --build
```

---

## 📈 Spark UI Access

* Spark Master UI → [http://localhost:8080](http://localhost:8080)
* Spark Worker UI → [http://localhost:8081](http://localhost:8081)

---

## 🧩 Key Features

✅ Real-time data ingestion via Kafka
✅ Structured Streaming with event-time windowing
✅ Automatic topic creation before Spark runs
✅ Partitioned Parquet output with checkpoints
✅ Compatible with Apple Silicon (arm64 images)
✅ Easy data validation using Pandas or Spark

---

## 🧪 Example Aggregation Output

Spark console log example (10-second windows):

```
-------------------------------------------
Batch: 4
-------------------------------------------
+-------------------+-------------------+---+---------+
|window_start       |window_end         |id |avg_value|
+-------------------+-------------------+---+---------+
|2025-11-06 07:00:00|2025-11-06 07:00:10|3  |45.282   |
|2025-11-06 07:00:10|2025-11-06 07:00:20|1  |58.019   |
+-------------------+-------------------+---+---------+
```

---

## 🧩 Folder Structure

```
spark-kafka-pipeline/
├── docker-compose.yml
├── .env
├── cache/
│   └── ivy/                 # cached Spark Kafka connector jars
├── data/
│   └── output/
│       └── parquet/
├── producer/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── producer.py
└── spark/
    └── app.py
```

---

## 💡 Useful Commands

| Action              | Command                                                                                                      |
| ------------------- | ------------------------------------------------------------------------------------------------------------ |
| Start containers    | `docker compose up -d --build`                                                                               |
| Stop containers     | `docker compose down`                                                                                        |
| View Spark logs     | `docker logs -f spark-streaming`                                                                             |
| View Kafka topics   | `docker exec -it kafka kafka-topics --bootstrap-server kafka:9092 --list`                                    |
| View topic messages | `docker exec -it kafka kafka-console-consumer --bootstrap-server kafka:9092 --topic events --from-beginning` |

---

## 👨‍💻 Author

**Sandeep Mohanty**
*Data Engineering Sandbox – Spark × Kafka × Docker*

---
