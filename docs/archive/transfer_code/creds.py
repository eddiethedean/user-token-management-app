# Copy this file to cred.py. transfer_code/cred.py is ignored by Git.
# This file stores application secrets.
# Do NOT commit this file to version control (e.g., Git).


class AdvanaCredentials:
    """Credentials for Databricks/Advana"""

    dbricks_url = "advana-data.cloud.databricks.mil"
    dbricks_token = "[INSERT TOKEN HERE]"
    dbricks_protocol = "['HTTP Path' from JDBC/ODBC section of Databricks]"
    dbricks_database = "[INSERT DB NAME (ex. exec_ccmd_ussocom_analytics)]"
    dbricks_exec_database = "YOUR_EXEC_DATABASE_HERE"
    dbricks_Exec_protocol = "YOUR_EXEC_PROTOCOL_HERE"


class MCSCOPCredentials:
    """Credentials for MCS"""

    mcscop_token = "[INSERT MCSCOP TOKEN HERE]"
    mcscop_url = "cloud.mcs-cop.socom.mil"


class MSSCredentials:
    """Credentials for MSS"""

    mss_token = "[MSSTOKEN]"
    mss_url = "mss.data.mil"


class VANTAGECredentials:
    """Credentials for Vantage"""

    vantage_token = "VANTAGE_TOKEN"
    vantage_url = "vantage.army.mil"


class PostgresCredentials:
    """Credentials for Postgres"""

    host = "postgresql.socom.mil"
    pg_port = "5432"
    pg_database = "[DB NAME FROM PGADMIN]"
    user = "[INSERT USERNAME]"
    password = "[INSERT PASSWORD(database PW not log-in PW)]"


class Credentials:
    """A central class to hold all credential objects."""

    def __init__(self):
        self.advana = AdvanaCredentials()
        self.mcscop = MCSCOPCredentials()
        self.mss = MSSCredentials()
        self.vantage = VANTAGECredentials()
        self.postgres = PostgresCredentials()
