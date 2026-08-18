import csv
import io
from urllib.parse import quote

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

# =====================================================================
# ============================= Variables =============================
# =====================================================================

# 1. 'dataset_rid' is found in the properties of the MSS dataset on
#     the left side of the page
# 2. 'postgres_table_name' is what the table will be named in pgadmin.
#     If the table does not exist, this code will create it.
# 3. 'postgres_unique_column' Below is the PK of the table. It MUST be
#     unique, as this sets the primary key in postgres.

dataset_rid = "ENTER RID HERE (ex. ri.foundry.main.dataset...)"
postgres_table_name = "POSTGRES TABLE NAME"
postgres_unique_column = "ENTER TABLE PRIMARY KEY HERE"

# =====================================================================


def _identifier(name):
    """Safely support either a table name or a schema-qualified table name."""
    return sql.Identifier(*name.split("."))


def run_mss_to_postgres():
    if Credentials is None:
        raise RuntimeError("Could not find cred.py; copy creds.py to cred.py and configure it")
    creds = Credentials()

    # --- NIPR CA FIX ---
    socom_ca_fix.add_nipr_ca()

    # --- URL Sanitization ---
    raw_url = creds.mss.mss_url.replace("https://", "").strip("/")
    base_url = f"https://{raw_url}"
    headers = {"authorization": f"Bearer {creds.mss.mss_token}"}

    # =================================================================
    # 1: Palantir Section
    # =================================================================

    print("--- Fetching raw files from Palantir Dataset ---")
    branches_to_try = ["master", "main"]
    file_list = []
    active_branch = None

    for branch in branches_to_try:
        print(f"DEBUG: Checking for files on branch: '{branch}'...")
        list_files_url = f"{base_url}/api/v1/datasets/{dataset_rid}/files"
        resp = requests.get(
            list_files_url,
            headers=headers,
            params={"branchName": branch},
            timeout=REQUEST_TIMEOUT,
        )

        if resp.status_code == 200:
            file_list = resp.json().get("data", [])
            active_branch = branch
            print(f"Success! Found {len(file_list)} files on branch: '{branch}'")
            break
        else:
            print(f"   -> Failed on '{branch}' (Status: {resp.status_code})")

    if not file_list:
        print("\n[ERROR] Could not list any files. The dataset might be completely empty.")
        return

    valid_files = [f for f in file_list if not f.get("path", "").startswith("_")]
    if not valid_files:
        print("\n[ERROR] Dataset contains no valid data files (only hidden system files).")
        return

    dfs = []
    for file_info in valid_files:
        file_path = file_info.get("path")
        print(f"Downloading file: {file_path} ...")

        encoded_path = quote(file_path, safe="")
        content_url = f"{base_url}/api/v1/datasets/{dataset_rid}/files/{encoded_path}/content"
        file_resp = requests.get(
            content_url,
            headers=headers,
            params={"branchName": active_branch},
            timeout=REQUEST_TIMEOUT,
        )

        if file_resp.status_code == 200:
            if file_path.endswith(".parquet"):
                df_part = pd.read_parquet(io.BytesIO(file_resp.content))
                dfs.append(df_part)
            elif file_path.endswith(".csv"):
                df_part = pd.read_csv(io.StringIO(file_resp.text))
                dfs.append(df_part)
            else:
                print(f"   -> Skipping unsupported file format: {file_path}")
        else:
            print(f"   -> Failed to download {file_path} (Status {file_resp.status_code})")

    if not dfs:
        print("\n[ERROR] Could not parse any data files into Pandas.")
        return

    df = pd.concat(dfs, ignore_index=True)
    print(f"\nSuccessfully loaded a total of {len(df)} rows into memory.")
    if df.empty:
        print("[WARNING] Dataset has no rows. Nothing to transfer.")
        return

    # =================================================================
    # 2: Postgres Section
    # =================================================================

    print("\n--- Starting PostgreSQL Upsert ---")
    pg_conn = None
    cursor = None
    try:
        # 1. Psycopg2 connection
        print("Creating direct psycopg2 connection...")
        pg_conn = psycopg2.connect(
            host=creds.postgres.host,
            port=creds.postgres.pg_port,
            database=creds.postgres.pg_database,
            user=creds.postgres.user,
            password=creds.postgres.password,
        )
        cursor = pg_conn.cursor()
        print("Successfully created PostgreSQL connection.")

        main_table_name = postgres_table_name
        unique_column = postgres_unique_column

        # 2. Create table IF IT DOES NOT EXIST.
        print(f"Ensuring table '{main_table_name}' exists...")
        dtype_mapping = {
            "object": "TEXT",
            "int64": "BIGINT",
            "float64": "DOUBLE PRECISION",
            "datetime64[ns]": "TIMESTAMP",
            "bool": "BOOLEAN",
        }
        column_definitions = [
            sql.SQL("{} {}").format(
                sql.Identifier(str(column)),
                sql.SQL(dtype_mapping.get(str(df[column].dtype), "TEXT")),
            )
            for column in df.columns
        ]
        create_main_table_query = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
            _identifier(main_table_name), sql.SQL(", ").join(column_definitions)
        )

        cursor.execute(create_main_table_query)
        pg_conn.commit()  # Commit the table creation

        # 3. Add the PK constraint if it doesn't exist.
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_index "
            "WHERE indrelid = to_regclass(%s) AND indisprimary)",
            (main_table_name,),
        )
        has_primary_key = cursor.fetchone()[0]
        if not has_primary_key:
            print(f"Attempting to add PRIMARY KEY on '{unique_column}'...")
            cursor.execute(
                sql.SQL("ALTER TABLE {} ADD PRIMARY KEY ({})").format(
                    _identifier(main_table_name), sql.Identifier(unique_column)
                )
            )
            pg_conn.commit()
            print("Primary Key added successfully.")
        else:
            print("Primary Key already exists. Continuing.")

        # 4. Create a temp table for staging new data.
        temp_table_name = "temp_upload_table"
        cursor.execute(
            sql.SQL("CREATE TEMP TABLE {} (LIKE {})").format(
                sql.Identifier(temp_table_name), _identifier(main_table_name)
            )
        )
        print(f"Temporary table '{temp_table_name}' created.")

        # 5. Load the DataFrame into the temp table.
        buffer = io.StringIO()
        df.to_csv(buffer, index=False, header=False, quoting=csv.QUOTE_ALL)
        buffer.seek(0)
        print("Loading data into temporary table...")
        identifiers = [sql.Identifier(str(column)) for column in df.columns]
        copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT CSV)").format(
            sql.Identifier(temp_table_name), sql.SQL(", ").join(identifiers)
        )
        cursor.copy_expert(sql=copy_sql, file=buffer)

        # 6. Insert from temp table into main table, skipping duplicates.
        print("Merging new data into main table...")
        merge_sql = sql.SQL(
            "INSERT INTO {} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO NOTHING"
        ).format(
            _identifier(main_table_name),
            sql.SQL(", ").join(identifiers),
            sql.SQL(", ").join(identifiers),
            sql.Identifier(temp_table_name),
            sql.Identifier(unique_column),
        )
        cursor.execute(merge_sql)
        new_rows_count = cursor.rowcount
        print(f"Merge complete. Added {new_rows_count} new rows.")
        pg_conn.commit()

    except Exception as e:
        if pg_conn:
            pg_conn.rollback()
        raise RuntimeError("PostgreSQL transfer failed") from e

    finally:
        if cursor is not None:
            cursor.close()
        if pg_conn is not None:
            pg_conn.close()
            print("PostgreSQL connection closed.")

    print("--- PostgreSQL Upsert Finished ---\n")


if __name__ == "__main__":
    run_mss_to_postgres()
