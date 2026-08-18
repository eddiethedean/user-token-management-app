import os
import tempfile

import pandas as pd
import psycopg2
import requests
import socom_ca_fix
from psycopg2 import sql

# Import the credentials from your cred.py file
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

    # Establish connection using context managers to guarantee closure of all sockets
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

    # Both cursor and pg_conn are guaranteed closed here
    print("PostgreSQL connection and cursors closed cleanly.")
    return df


def upload_to_mcscop(df: pd.DataFrame, mcscop_creds, dest_rid, dest_table_name):
    """
    Writes the DataFrame to a local Parquet file and streams it to MCS-COP.
    Guarantees cleanup of files and network streams post-completion.
    """
    print("\n--- Starting MCS-COP Upload ---")

    # Initialize NIPR CA fix for the HTTPS requests
    socom_ca_fix.add_nipr_ca()

    file_path = None

    try:
        # Step 1: Safely write DataFrame to a temp Parquet file.
        # We close this context block immediately after writing to release any OS write locks.
        with tempfile.NamedTemporaryFile(
            suffix=f"_{dest_table_name}.snappy.parquet", delete=False
        ) as tmp_file:
            file_path = tmp_file.name

        print(f"Writing data to temporary Parquet file: {file_path}")
        df.to_parquet(file_path, compression="snappy")

        headers = {
            "content-type": "application/octet-stream",
            "authorization": f"Bearer {mcscop_creds.mcscop_token}",
        }

        print(f"Uploading file '{file_path}' to MCS-COP dataset RID '{dest_rid}'...")
        upload_url = f"https://{mcscop_creds.mcscop_url}/api/v2/datasets/{dest_rid}/files/{dest_table_name}.snappy.parquet/upload?preview=true"

        # Step 2: Stream file payload using nested context managers.
        # The 'with open' block guarantees the file descriptor closes the moment the upload finishes.
        with open(file_path, "rb") as file_data:
            response = requests.post(
                upload_url,
                headers=headers,
                data=file_data,
                timeout=REQUEST_TIMEOUT,
            )

            # Print response parameters while connection context is active
            print(f"MCS-COP Response Code: {response.status_code}")
            if response.text:
                print(f"MCS-COP Response Body: {response.text}")

            response.raise_for_status()
            print("\nUpload to MCS-COP completed successfully.")

    finally:
        # Step 3: Clean up the physical file on disk (guaranteed execution)
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print("Cleanup: Temporary Parquet file deleted from disk.")
            except Exception as cleanup_err:
                print(f"[WARNING] Could not delete temp file: {cleanup_err}")

        print("--- MCS-COP Upload Finished ---")


def postgres_to_mcscop_pipeline():
    """
    Main orchestrator for the PostgreSQL to MCS-COP data pipeline.
    """
    print("--- PostgreSQL to MCS-COP Pipeline Started ---")
    if Credentials is None:
        raise RuntimeError("Could not find cred.py; copy creds.py to cred.py and configure it")

    # 1. Setup Credentials
    try:
        creds = Credentials()
        pg_creds = creds.postgres
        mcscop_creds = creds.mcscop
    except AttributeError as e:
        raise RuntimeError("A required credential is missing from cred.py") from e

    postgres_source_table = "loc_table"
    mcscop_dest_table_name = "refactor_test"
    mcscop_dest_rid = "ri.foundry.main.dataset.c0849c85-b5aa-4a14-a116-053385afe8bf"

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
    upload_to_mcscop(df, mcscop_creds, mcscop_dest_rid, mcscop_dest_table_name)


if __name__ == "__main__":
    postgres_to_mcscop_pipeline()
