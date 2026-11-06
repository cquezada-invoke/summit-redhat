"""Carga inicial del lake de la fiscalía: el histórico de denuncias en Parquet (S3) y las tablas y la vista en Trino. Idempotente."""
import io
import time

import pyarrow as pa
import pyarrow.parquet as pq

from comun import asegurar_lake, s3_put, trino
from datos import generar

df = generar()
for c in ("evidencias", "carga_mp"):
    df[c] = df[c].astype("int32")
df["dias_resolucion"] = df["dias_resolucion"].astype("Int32")
buf = io.BytesIO()
pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, coerce_timestamps="ms", allow_truncated_timestamps=True)
s3_put("denuncias_historico/denuncias.parquet", buf.getvalue(), "application/octet-stream")
print("histórico en S3:", len(df), "denuncias,", round(len(buf.getvalue()) / 1e6, 1), "MB", flush=True)
for i in range(60):
    try:
        asegurar_lake()
        break
    except Exception as e:  # noqa: BLE001
        print("esperando Trino…", str(e)[:120], flush=True)
        time.sleep(5)
print(trino("SELECT origen, estado, COUNT(*) AS n FROM lake.fiscalia.denuncias GROUP BY 1, 2 ORDER BY 1, 3 DESC").to_string(index=False), flush=True)
