import os
import tempfile

import pandas as pd
import psycopg2
import requests
import socom_ca_fix
from psycopg2 import sql

try:
    from cred import Credentials
except ImportError:
    Credentials = None

REQUEST_TIMEOUT = (10, 120)


def _identifier(name):
    """Safely support either a table name or a schema-qualified table name."""
    return sql.Identifier(*name.split("."))


def extract_postgres_data(pg_creds, source_table) -> pd.DataFrame:
    """
    Connects to PostgreSQL, extracts the target table, and safely closes all
    database connections and cursors before returning the DataFrame.
    """
    print("\n--- Connecting to PostgreSQL ---")

    with psycopg2.connect(
        host=pg_creds.host,
        port=pg_creds.pg_port,
        database=pg_creds.pg_database,
        user=pg_creds.user,
        password=pg_creds.password,
    ) as pg_conn:
        print(f"Successfully connected. Fetching data from table '{source_table}'...")
        query = sql.SQL("SELECT * FROM {}").format(_identifier(source_table))
        df = pd.read_sql_query(query, pg_conn)

    print("PostgreSQL connection and cursors closed cleanly.")
    return df


def upload_to_mss(df: pd.DataFrame, mss_creds, dest_rid, dest_table_name):
    """
    Writes the DataFrame to a local Parquet file and streams it to MSS.
    Guarantees cleanup of files and network streams post-completion.
    """
    print("\n--- Starting MSS Upload ---")

    # Initialize NIPR CA fix for the HTTPS requests
    socom_ca_fix.add_nipr_ca()

    file_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=f"_{dest_table_name}.snappy.parquet", delete=False
        ) as tmp_file:
            file_path = tmp_file.name

        print(f"Writing data to temporary Parquet file: {file_path}")
        df.to_parquet(file_path, compression="snappy")

        headers = {
            "content-type": "application/octet-stream",
            "authorization": f"Bearer {mss_creds.mss_token}",
        }

        print(f"Uploading file '{file_path}' to MSS dataset RID '{dest_rid}'...")
        upload_url = f"https://{mss_creds.mss_url}/api/v2/datasets/{dest_rid}/files/{dest_table_name}.snappy.parquet/upload?preview=true"

        with open(file_path, "rb") as file_data:
            response = requests.post(
                upload_url,
                headers=headers,
                data=file_data,
                timeout=REQUEST_TIMEOUT,
            )

            print(f"MSS Response Code: {response.status_code}")
            if response.text:
                print(f"MSS Response Body: {response.text}")

            response.raise_for_status()
            print("\nUpload to MSS completed successfully.")

    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print("Cleanup: Temporary Parquet file deleted from disk.")
            except Exception as cleanup_err:
                print(f"[WARNING] Could not delete temp file: {cleanup_err}")

        print("--- MSS Upload Finished ---")


def postgres_to_mss_pipeline():
    """
    Main orchestrator for the PostgreSQL to MSS data pipeline.
    """
    print("--- PostgreSQL to MSS Pipeline Started ---")
    if Credentials is None:
        raise RuntimeError("Could not find cred.py; copy creds.py to cred.py and configure it")

    # 1. Setup Credentials
    try:
        creds = Credentials()
        pg_creds = creds.postgres
        mss_creds = creds.mss
    except AttributeError as e:
        raise RuntimeError("A required credential is missing from cred.py") from e

    postgres_source_table = "loc_table"
    mss_dest_table_name = "pg_to_mss"
    mss_dest_rid = "ri.foundry.main.dataset.4f164cd5-a100-483b-9b3d-05cbe7002444"

    # 2. Extract Data
    try:
        df = extract_postgres_data(pg_creds, postgres_source_table)
        if df.empty:
            print(
                f"[WARNING] The source table '{postgres_source_table}' is empty. Halting pipeline."
            )
            return

        print(f"Data fetched successfully. Found {len(df)} rows.")
        print("\nDataFrame Head:")
        print(df.head())

    except Exception as e:
        raise RuntimeError("Database extraction failed") from e

    # 3. Upload Data
    upload_to_mss(df, mss_creds, mss_dest_rid, mss_dest_table_name)


if __name__ == "__main__":
    postgres_to_mss_pipeline()
