import os
import uuid
from pyspark.sql import SparkSession
from pyspark.sql.functions import from_json, col, to_timestamp, window, avg
from pyspark.sql.types import StructType, StructField, IntegerType, StringType, DoubleType

bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
topic = os.getenv("KAFKA_TOPIC", "events")

output_base = os.getenv("OUTPUT_PATH", "/opt/spark-output/parquet").rstrip("/")
run_id = os.getenv("RUN_ID", str(uuid.uuid4())[:8])
output_path = f"{output_base}/run_{run_id}"
checkpoint_path = f"{output_path}/_chk"

spark = (
    SparkSession.builder.appName(f"KafkaSparkStreamingDemo-{run_id}")
    .getOrCreate()
)
spark.sparkContext.setLogLevel("WARN")

schema = StructType([
    StructField("id", IntegerType(), False),
    StructField("ts", StringType(), False),
    StructField("value", DoubleType(), False),
    StructField("source", StringType(), True),
])

raw_df = (
    spark.readStream
         .format("kafka")
         .option("kafka.bootstrap.servers", bootstrap)
         .option("subscribe", topic)
         .option("startingOffsets", "earliest")
         .load()
)

parsed_df = (
    raw_df.selectExpr("CAST(key AS STRING)", "CAST(value AS STRING) as json_str")
          .select(col("key"), from_json(col("json_str"), schema).alias("data"))
          .select(
              col("key"),
              col("data.id").alias("id"),
              to_timestamp(col("data.ts")).alias("ts"),
              col("data.value").alias("value"),
              col("data.source").alias("source"),
          )
          .na.drop(subset=["ts"])
)

# Quick debug sink
debug_q = (
    parsed_df.writeStream
             .format("console")
             .option("truncate", "false")
             .outputMode("append")
             .start()
)

agg_df = (
    parsed_df
    .withWatermark("ts", "30 seconds")
    .groupBy(window(col("ts"), "10 seconds"), col("id"))
    .agg(avg("value").alias("avg_value"))
    .select(
        col("window.start").alias("window_start"),
        col("window.end").alias("window_end"),
        col("id"),
        col("avg_value")
    )
)

agg_console_q = (
    agg_df.writeStream
          .outputMode("update")
          .format("console")
          .option("truncate", "false")
          .option("numRows", "50")
          .start()
)

parquet_q = (
    parsed_df.writeStream
             .format("parquet")
             .option("path", output_path)
             .option("checkpointLocation", checkpoint_path)
             .option("failOnDataLoss", "false")
             .partitionBy("id")
             .outputMode("append")
             .start()
)

spark.streams.awaitAnyTermination()
