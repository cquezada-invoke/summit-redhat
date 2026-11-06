"""Carga el data lake de la demo: los mismos datos de la demo de analítica, en tres formatos distintos, en MinIO (S3),
y los registra como tablas en Trino (catálogo «lake», esquema «telco»). Idempotente."""
import os
import time

import boto3
import requests
from datos import base

con, _ = base()
os.makedirs("/tmp/lake", exist_ok=True)
con.execute("COPY clientes TO '/tmp/lake/clientes.parquet' (FORMAT PARQUET)")                 # tabla analítica: Parquet
con.execute("COPY ventas TO '/tmp/lake/ventas.csv' (HEADER false, DELIMITER ',')")             # export de un sistema: CSV
con.execute("COPY recargas TO '/tmp/lake/recargas.json' (FORMAT JSON)")                       # eventos: JSON (una línea por evento)

s3 = boto3.client("s3", endpoint_url="http://minio:9000", aws_access_key_id=os.environ["MINIO_ROOT_USER"],
                  aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"], region_name="us-east-1")
if "lake" not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
    s3.create_bucket(Bucket="lake")
for f, k in (("clientes.parquet", "telco/clientes/clientes.parquet"), ("ventas.csv", "telco/ventas/ventas.csv"),
             ("recargas.json", "telco/recargas/recargas.json")):
    s3.upload_file(f"/tmp/lake/{f}", "lake", k)
    print("subido", k, flush=True)


def sql(q):
    r = requests.post("http://trino:8080/v1/statement", data=q.encode(), headers={"X-Trino-User": "carga"}).json()
    while "nextUri" in r:
        time.sleep(0.3)
        r = requests.get(r["nextUri"], headers={"X-Trino-User": "carga"}).json()
    if r.get("error"):
        raise SystemExit(f"{q[:60]}…: {r['error']['message']}")
    return r.get("data")


for i in range(60):   # Trino tarda en arrancar
    try:
        sql("SELECT 1"); break
    except Exception:  # noqa: BLE001
        time.sleep(5)
sql("CREATE SCHEMA IF NOT EXISTS lake.telco WITH (location = 's3://lake/telco/')")
for t in ("clientes", "ventas", "recargas"):
    sql(f"DROP TABLE IF EXISTS lake.telco.{t}")
sql("""CREATE TABLE lake.telco.clientes (cliente_id integer, region varchar, comuna varchar, segmento varchar, plan varchar,
  cargo_mensual double, antiguedad_meses integer, edad integer, datos_gb_mes double, minutos_mes integer, reclamos_90d integer,
  caidas_red_30d integer, consultas_portabilidad integer, gama_equipo varchar, equipo_en_cuotas boolean, canal_alta varchar,
  fecha_alta timestamp(3), fuga boolean, fecha_baja timestamp(3))
  WITH (format = 'PARQUET', external_location = 's3://lake/telco/clientes/')""")
sql("""CREATE TABLE lake.telco.ventas (venta_id varchar, fecha varchar, region varchar, canal varchar, tipo varchar, monto varchar, cliente_id varchar)
  WITH (format = 'CSV', external_location = 's3://lake/telco/ventas/')""")
sql("""CREATE TABLE lake.telco.recargas (recarga_id integer, fecha varchar, cliente_id integer, region varchar, canal varchar, monto integer)
  WITH (format = 'JSON', external_location = 's3://lake/telco/recargas/')""")
print("tablas", sql("SHOW TABLES FROM lake.telco"), flush=True)
print("prueba", sql("SELECT region, ROUND(100.0 * AVG(CASE WHEN fuga THEN 1 ELSE 0 END), 1) FROM lake.telco.clientes GROUP BY 1 ORDER BY 2 DESC LIMIT 3"), flush=True)
print("prueba", sql("SELECT canal, SUM(CAST(monto AS bigint)) FROM lake.telco.ventas GROUP BY 1 ORDER BY 2 DESC LIMIT 3"), flush=True)
print("prueba", sql("SELECT canal, COUNT(*) FROM lake.telco.recargas GROUP BY 1 ORDER BY 2 DESC LIMIT 3"), flush=True)
