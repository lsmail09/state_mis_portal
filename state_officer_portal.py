# ============================================================
# NCTO STATE OFFICER PORTAL
# ============================================================

import os
import io
import re
import json
import gzip
import hmac
import time
import base64
import hashlib
import bcrypt
import tempfile
import threading

from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, parse_qs, quote
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import pandas as pd
import streamlit as st

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


# ============================================================
# STREAMLIT CONFIG
# ============================================================

st.set_page_config(
    page_title="NCTO State Officer Portal",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================================
# POSTGRESQL CONFIG
# ============================================================

PG_HOST = "102.164.37.69"
PG_PORT = 5432

PG_DATABASE = "ben_db"
PG_USER = "ben_user"
PG_PASSWORD = "Olajumokepsgr#9#9"


# ============================================================
# DIRECT DOWNLOAD SERVER CONFIG
#
# IMPORTANT:
# Change PUBLIC_DOWNLOAD_BASE_URL to the IP/domain of the
# machine where this Streamlit application is running.
#
# Example:
# PUBLIC_DOWNLOAD_BASE_URL = "http://102.164.37.69:8765"
# ============================================================

DOWNLOAD_HOST = "0.0.0.0"
DOWNLOAD_PORT = 8765

# PUBLIC_DOWNLOAD_BASE_URL = "http://YOUR_SERVER_IP:8765"
PUBLIC_DOWNLOAD_BASE_URL = "http://102.164.37.69:8765"
DOWNLOAD_SECRET = (
    "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET_KEY_FOR_NCTO"
)

# Link valid for 8 hours
DOWNLOAD_TOKEN_LIFETIME = 8 * 60 * 60


# ============================================================
# EXPORT CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

EXPORT_DIR = BASE_DIR / "exports"

EXPORT_DIR.mkdir(
    parents=True,
    exist_ok=True
)

# Limit simultaneous large exports
EXPORT_WORKERS = 2


# ============================================================
# PAYMENT TABLES
# ============================================================

PAYMENT_TABLES = [
    (
        "First Tranche",
        'ben."itblDistinctPaidBeneficiaries"'
    ),
    (
        "Second Tranche",
        'ben."itblDistinctSecondTranche"'
    ),
    (
        "Third Tranche",
        'ben."itblDistinctThirdTranche"'
    ),
]


# ============================================================
# DATABASE ENGINE
# ============================================================

@st.cache_resource
def get_engine():

    database_url = URL.create(
        drivername="postgresql+psycopg2",
        username=PG_USER,
        password=PG_PASSWORD,
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
    )

    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=10,
    )


engine = get_engine()


# ============================================================
# EXPORT THREAD RESOURCES
# ============================================================

@st.cache_resource
def get_export_executor():

    return ThreadPoolExecutor(
        max_workers=EXPORT_WORKERS,
        thread_name_prefix="ncto_export"
    )


@st.cache_resource
def get_export_jobs():

    return {}


@st.cache_resource
def get_export_lock():

    return threading.Lock()


STATUS_FILE_LOCK = threading.Lock()


# ============================================================
# GENERAL HELPERS
# ============================================================

def safe_filename(value):

    value = str(
        value or ""
    ).strip()

    value = re.sub(
        r'[\\/:*?"<>|]+',
        "_",
        value
    )

    value = re.sub(
        r"\s+",
        "_",
        value
    )

    return value or "Export"


def dataframe_to_excel_bytes(
    df,
    sheet_name="Summary"
):

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            sheet_name=sheet_name[:31],
            index=False
        )

    output.seek(0)

    return output.getvalue()


def dataframe_to_csv_bytes(df):

    return (
        df.to_csv(
            index=False
        )
        .encode(
            "utf-8-sig"
        )
    )


# ============================================================
# AUTHENTICATION
# ============================================================

def verify_password(
    plain_password,
    password_hash
):

    try:

        if isinstance(
            password_hash,
            bytes
        ):
            stored_hash = password_hash

        else:
            stored_hash = str(
                password_hash
            ).encode(
                "utf-8"
            )

        return bcrypt.checkpw(
            plain_password.encode(
                "utf-8"
            ),
            stored_hash
        )

    except Exception:
        return False


def authenticate_user(
    username,
    password
):

    query = text("""
        SELECT
            username,
            password_hash,
            assigned_state
        FROM ben.state_officer_users
        WHERE LOWER(BTRIM(username))
              = LOWER(BTRIM(:username))
          AND is_active = TRUE
        LIMIT 1;
    """)

    with engine.connect() as conn:

        user = conn.execute(
            query,
            {
                "username": username
            }
        ).mappings().first()

    if not user:
        return None

    if not verify_password(
        password,
        user["password_hash"]
    ):
        return None

    return {
        "username": user["username"],
        "state": str(
            user["assigned_state"]
        ).strip()
    }


# ============================================================
# STATE SUMMARY
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False
)
def load_state_summary(state_name):

    query = text("""
        WITH unified_payments AS
        (
            SELECT
                'First Tranche'::TEXT AS tranche,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh
            FROM ben."itblDistinctPaidBeneficiaries"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))

            UNION ALL

            SELECT
                'Second Tranche'::TEXT,
                ben.normalize_location_name("LGA"),
                ben.normalize_location_name("Ward"),
                ben.normalize_location_name("Community"),
                CAST(nidhh AS TEXT)
            FROM ben."itblDistinctSecondTranche"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))

            UNION ALL

            SELECT
                'Third Tranche'::TEXT,
                ben.normalize_location_name("LGA"),
                ben.normalize_location_name("Ward"),
                ben.normalize_location_name("Community"),
                CAST(nidhh AS TEXT)
            FROM ben."itblDistinctThirdTranche"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))
        )

        SELECT
            tranche,

            COUNT(DISTINCT nidhh)
                AS total_beneficiaries,

            COUNT(DISTINCT nidhh)
                AS total_households,

            COUNT(DISTINCT lga)
                AS total_lgas,

            COUNT(DISTINCT (lga, ward))
                AS total_wards,

            COUNT(
                DISTINCT (
                    lga,
                    ward,
                    community
                )
            ) AS total_communities

        FROM unified_payments

        GROUP BY tranche

        ORDER BY
            CASE tranche
                WHEN 'First Tranche' THEN 1
                WHEN 'Second Tranche' THEN 2
                WHEN 'Third Tranche' THEN 3
                ELSE 4
            END;
    """)

    with engine.connect() as conn:

        return pd.read_sql(
            query,
            conn,
            params={
                "state_name": state_name
            }
        )


# ============================================================
# UNIQUE STATE TOTAL
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False
)
def load_state_unique_total(state_name):

    query = text("""
        SELECT
            COUNT(DISTINCT nidhh)
        FROM
        (
            SELECT
                CAST(nidhh AS TEXT) AS nidhh
            FROM ben."itblDistinctPaidBeneficiaries"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))

            UNION ALL

            SELECT
                CAST(nidhh AS TEXT)
            FROM ben."itblDistinctSecondTranche"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))

            UNION ALL

            SELECT
                CAST(nidhh AS TEXT)
            FROM ben."itblDistinctThirdTranche"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))
        ) x;
    """)

    with engine.connect() as conn:

        value = conn.execute(
            query,
            {
                "state_name": state_name
            }
        ).scalar()

    return int(
        value or 0
    )


# ============================================================
# LGA SUMMARY
# ============================================================

@st.cache_data(
    ttl=600,
    show_spinner=False
)
def load_lga_summary(state_name):

    query = text("""
        WITH unified_payments AS
        (
            SELECT
                'First Tranche'::TEXT AS tranche,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh
            FROM ben."itblDistinctPaidBeneficiaries"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))

            UNION ALL

            SELECT
                'Second Tranche'::TEXT,
                ben.normalize_location_name("LGA"),
                ben.normalize_location_name("Ward"),
                ben.normalize_location_name("Community"),
                CAST(nidhh AS TEXT)
            FROM ben."itblDistinctSecondTranche"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))

            UNION ALL

            SELECT
                'Third Tranche'::TEXT,
                ben.normalize_location_name("LGA"),
                ben.normalize_location_name("Ward"),
                ben.normalize_location_name("Community"),
                CAST(nidhh AS TEXT)
            FROM ben."itblDistinctThirdTranche"
            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))
        )

        SELECT

            COALESCE(
                lga,
                'UNKNOWN LGA'
            ) AS "LGA",

            COUNT(
                DISTINCT CASE
                    WHEN tranche = 'First Tranche'
                    THEN nidhh
                END
            ) AS "First_Tranche_Beneficiaries",

            COUNT(
                DISTINCT CASE
                    WHEN tranche = 'Second Tranche'
                    THEN nidhh
                END
            ) AS "Second_Tranche_Beneficiaries",

            COUNT(
                DISTINCT CASE
                    WHEN tranche = 'Third Tranche'
                    THEN nidhh
                END
            ) AS "Third_Tranche_Beneficiaries",

            COUNT(
                DISTINCT nidhh
            ) AS "Total_Unique_Beneficiaries",

            COUNT(
                DISTINCT ward
            )
            FILTER (
                WHERE ward IS NOT NULL
            ) AS "Total_Wards",

            COUNT(
                DISTINCT (
                    ward,
                    community
                )
            )
            FILTER (
                WHERE ward IS NOT NULL
                  AND community IS NOT NULL
            ) AS "Total_Communities"

        FROM unified_payments

        GROUP BY lga

        ORDER BY
            COALESCE(
                lga,
                'UNKNOWN LGA'
            );
    """)

    with engine.connect() as conn:

        return pd.read_sql(
            query,
            conn,
            params={
                "state_name": state_name
            }
        )


# ============================================================
# BENEFICIARY DETAIL QUERY
# ============================================================

def build_detail_union_sql():

    branches = []

    for (
        tranche_name,
        table_name
    ) in PAYMENT_TABLES:

        branches.append(
            f"""
            SELECT

                '{tranche_name}'::TEXT
                    AS tranche,

                CAST(
                    "State" AS TEXT
                ) AS "State",

                ben.normalize_location_name(
                    "LGA"
                ) AS "LGA",

                ben.normalize_location_name(
                    "Ward"
                ) AS "Ward",

                ben.normalize_location_name(
                    "Community"
                ) AS "Community",

                CAST(
                    "HouseholdID" AS TEXT
                ) AS "HouseholdID",

                CAST(
                    nidhh AS TEXT
                ) AS nidhh,

                CAST(
                    nid AS TEXT
                ) AS nid,

                CAST(
                    "Name" AS TEXT
                ) AS "Name",

                CAST(
                    "TelephoneNo" AS TEXT
                ) AS "TelephoneNo",

                CAST(
                    "Gender" AS TEXT
                ) AS "Gender",

                CAST(
                    "Age" AS TEXT
                ) AS "Age",

                CAST(
                    "HAddress" AS TEXT
                ) AS "HAddress",

                CAST(
                    "AccountName" AS TEXT
                ) AS "AccountName",

                CAST(
                    "AccountNumber" AS TEXT
                ) AS "AccountNumber",

                CAST(
                    "BankName" AS TEXT
                ) AS "BankName",

                CAST(
                    "AmountPaid" AS TEXT
                ) AS "AmountPaid",

                CAST(
                    "PaymentStatus" AS TEXT
                ) AS "PaymentStatus",

                CAST(
                    "PaymentDate" AS TEXT
                ) AS "PaymentDate",

                CAST(
                    "TrancheStatus" AS TEXT
                ) AS "TrancheStatus",

                CAST(
                    "TotalAmount" AS TEXT
                ) AS "TotalAmount",

                CAST(
                    "Zone" AS TEXT
                ) AS "Zone",

                CAST(
                    "Ward_Class" AS TEXT
                ) AS "Ward_Class"

            FROM {table_name}

            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(:state_name))
            """
        )

    return "\nUNION ALL\n".join(
        branches
    )


@st.cache_data(
    ttl=300,
    show_spinner=False
)
def load_state_data(
    state_name,
    tranche_filter,
    search_value,
    limit_rows
):

    params = {
        "state_name": state_name,
        "limit_rows": int(
            limit_rows
        )
    }

    tranche_condition = ""

    if tranche_filter != "All":

        tranche_condition = """
            AND tranche = :tranche_filter
        """

        params[
            "tranche_filter"
        ] = tranche_filter


    search_condition = ""

    if search_value:

        search_condition = """
            AND
            (
                   nid ILIKE :search_value
                OR nidhh ILIKE :search_value
                OR "AccountNumber" ILIKE :search_value
                OR "Name" ILIKE :search_value
                OR "HouseholdID" ILIKE :search_value
                OR "TelephoneNo" ILIKE :search_value
            )
        """

        params[
            "search_value"
        ] = f"%{search_value}%"


    union_sql = (
        build_detail_union_sql()
    )


    query = text(
        f"""
        WITH unified_payments AS
        (
            {union_sql}
        )

        SELECT *
        FROM unified_payments
        WHERE 1 = 1

        {tranche_condition}
        {search_condition}

        LIMIT :limit_rows;
        """
    )

    with engine.connect() as conn:

        return pd.read_sql(
            query,
            conn,
            params=params
        )


# ============================================================
# FULL EXPORT SQL
#
# No NIN
# No NINBVN
# No IDType
# No AccountUsed
#
# Includes:
# HAddress
# TelephoneNo
# TrancheStatus
# Zone
# Ward_Class
# ============================================================

def build_full_export_select_sql():

    branches = []

    for (
        tranche_name,
        table_name
    ) in PAYMENT_TABLES:

        branches.append(
            f"""
            SELECT

                '{tranche_name}'::TEXT
                    AS tranche,

                CAST(
                    "State" AS TEXT
                ) AS "State",

                ben.normalize_location_name(
                    "LGA"
                ) AS "LGA",

                ben.normalize_location_name(
                    "Ward"
                ) AS "Ward",

                ben.normalize_location_name(
                    "Community"
                ) AS "Community",

                CAST(
                    "HouseholdID" AS TEXT
                ) AS "HouseholdID",

                CAST(
                    nidhh AS TEXT
                ) AS nidhh,

                CAST(
                    nid AS TEXT
                ) AS nid,

                CAST(
                    "Name" AS TEXT
                ) AS "Name",

                CAST(
                    "TelephoneNo" AS TEXT
                ) AS "TelephoneNo",

                CAST(
                    "Gender" AS TEXT
                ) AS "Gender",

                CAST(
                    "Age" AS TEXT
                ) AS "Age",

                CAST(
                    "HAddress" AS TEXT
                ) AS "HAddress",

                CAST(
                    "AccountName" AS TEXT
                ) AS "AccountName",

                CAST(
                    "AccountNumber" AS TEXT
                ) AS "AccountNumber",

                CAST(
                    "BankName" AS TEXT
                ) AS "BankName",

                CAST(
                    "AmountPaid" AS TEXT
                ) AS "AmountPaid",

                CAST(
                    "PaymentStatus" AS TEXT
                ) AS "PaymentStatus",

                CAST(
                    "PaymentDate" AS TEXT
                ) AS "PaymentDate",

                CAST(
                    "TrancheStatus" AS TEXT
                ) AS "TrancheStatus",

                CAST(
                    "TotalAmount" AS TEXT
                ) AS "TotalAmount",

                CAST(
                    "Zone" AS TEXT
                ) AS "Zone",

                CAST(
                    "Ward_Class" AS TEXT
                ) AS "Ward_Class"

            FROM {table_name}

            WHERE UPPER(BTRIM("State"))
                  = UPPER(BTRIM(%s))
            """
        )

    return "\nUNION ALL\n".join(
        branches
    )


# ============================================================
# EXPORT STATUS LOCK
# ============================================================

STATUS_FILE_LOCK = threading.RLock()


# ============================================================
# EXPORT STATUS FILE
# ============================================================

def get_export_status_file(state_name):

    return (
        EXPORT_DIR
        / f"{safe_filename(state_name)}_Full_Export.status.json"
    )


# ============================================================
# WRITE EXPORT STATUS
#
# Windows-safe:
# - no .tmp file
# - no os.replace()
# - no rename
# - protected by thread lock
# - retries if antivirus/Windows temporarily locks the file
# ============================================================

def write_export_status(
    state_name,
    status,
    **kwargs
):

    status_file = get_export_status_file(
        state_name
    )

    data = {
        "state": state_name,
        "status": status,
        "updated_at": datetime.now().isoformat(
            timespec="seconds"
        )
    }

    data.update(kwargs)

    EXPORT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    last_error = None

    with STATUS_FILE_LOCK:

        for attempt in range(10):

            try:

                with open(
                    status_file,
                    "w",
                    encoding="utf-8"
                ) as file:

                    json.dump(
                        data,
                        file,
                        indent=2
                    )

                    file.flush()

                return True

            except PermissionError as exc:

                last_error = exc

                time.sleep(
                    0.25 * (attempt + 1)
                )

            except OSError as exc:

                last_error = exc

                time.sleep(
                    0.25 * (attempt + 1)
                )

    raise RuntimeError(
        f"Unable to write export status after retries: "
        f"{last_error}"
    )


# ============================================================
# READ EXPORT STATUS
# ============================================================

def read_export_status(state_name):

    status_file = get_export_status_file(
        state_name
    )

    if not status_file.exists():
        return None

    with STATUS_FILE_LOCK:

        for attempt in range(5):

            try:

                with open(
                    status_file,
                    "r",
                    encoding="utf-8"
                ) as file:

                    return json.load(file)

            except json.JSONDecodeError:

                # Extremely small chance that another thread
                # is between truncate/write operations.
                time.sleep(0.1)

            except PermissionError:

                time.sleep(
                    0.15 * (attempt + 1)
                )

            except OSError:

                time.sleep(
                    0.15 * (attempt + 1)
                )

    return None


# ============================================================
# GZIP VALIDATION
# ============================================================

def validate_gzip_file(
    file_path
):

    file_path = Path(
        file_path
    )

    if not file_path.exists():
        return False

    if file_path.stat().st_size <= 0:
        return False

    try:

        with gzip.open(
            file_path,
            "rb"
        ) as file:

            while True:

                chunk = file.read(
                    8 * 1024 * 1024
                )

                if not chunk:
                    break

        return True

    except Exception:

        return False


# ============================================================
# CLEAN OLD EXPORTS
# ============================================================

def cleanup_old_exports(
    state_name,
    keep_file=None
):

    state_prefix = (
        safe_filename(
            state_name
        )
        + "_Full_Beneficiaries_"
    )

    for file in EXPORT_DIR.glob(
        f"{state_prefix}*.csv.gz"
    ):

        if (
            keep_file
            and file.resolve()
            == Path(
                keep_file
            ).resolve()
        ):
            continue

        try:
            file.unlink()
        except Exception:
            pass


# ============================================================
# GENERATE FULL EXPORT
# ============================================================

def generate_full_export_job(
    state_name
):

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    safe_state = safe_filename(
        state_name
    )

    filename = (
        f"{safe_state}"
        f"_Full_Beneficiaries_"
        f"{timestamp}.csv.gz"
    )

    final_path = (
        EXPORT_DIR
        / filename
    )

    partial_path = Path(
        str(
            final_path
        )
        + ".part"
    )

    raw_connection = None
    cursor = None

    try:

        # ----------------------------------------------------
        # STATUS RUNNING
        # ----------------------------------------------------

        write_export_status(
            state_name,
            "running",
            started_at=datetime.now().isoformat(
                timespec="seconds"
            ),
            message=(
                "Full state export is currently being prepared."
            )
        )


        # ----------------------------------------------------
        # REMOVE STALE PART FILE
        # ----------------------------------------------------

        if partial_path.exists():

            try:
                partial_path.unlink()
            except Exception:
                pass


        # ----------------------------------------------------
        # BUILD COPY QUERY
        # ----------------------------------------------------

        select_sql = (
            build_full_export_select_sql()
        )

        copy_sql = f"""
            COPY
            (
                {select_sql}
            )
            TO STDOUT
            WITH
            (
                FORMAT CSV,
                HEADER TRUE,
                ENCODING 'UTF8'
            )
        """


        # ----------------------------------------------------
        # RAW POSTGRES CONNECTION
        # ----------------------------------------------------

        raw_connection = (
            engine.raw_connection()
        )

        cursor = (
            raw_connection.cursor()
        )


        parameters = tuple(
            state_name
            for _ in PAYMENT_TABLES
        )


        final_copy_sql = (
            cursor.mogrify(
                copy_sql,
                parameters
            )
            .decode(
                "utf-8"
            )
        )


        # ----------------------------------------------------
        # COPY DIRECTLY TO GZIP
        # ----------------------------------------------------

        with gzip.open(
            partial_path,
            mode="wb",
            compresslevel=5
        ) as gzip_file:

            # UTF-8 BOM
            gzip_file.write(
                b"\xef\xbb\xbf"
            )

            cursor.copy_expert(
                final_copy_sql,
                gzip_file,
                size=8 * 1024 * 1024
            )


        cursor.close()
        cursor = None

        raw_connection.close()
        raw_connection = None


        # ----------------------------------------------------
        # VALIDATE BEFORE READY
        # ----------------------------------------------------

        if not validate_gzip_file(
            partial_path
        ):

            raise RuntimeError(
                "Generated export failed GZIP integrity validation."
            )


        # ----------------------------------------------------
        # ATOMIC COMPLETION
        # ----------------------------------------------------

        os.replace(
            partial_path,
            final_path
        )


        # ----------------------------------------------------
        # REMOVE PREVIOUS COMPLETED EXPORTS
        # ----------------------------------------------------

        cleanup_old_exports(
            state_name,
            keep_file=final_path
        )


        size_bytes = (
            final_path.stat().st_size
        )

        size_mb = (
            size_bytes
            / 1024
            / 1024
        )


        # ----------------------------------------------------
        # READY
        # ----------------------------------------------------

        write_export_status(
            state_name,
            "ready",
            filename=final_path.name,
            filepath=str(
                final_path
            ),
            file_size_bytes=size_bytes,
            file_size_mb=round(
                size_mb,
                2
            ),
            completed_at=datetime.now().isoformat(
                timespec="seconds"
            )
        )

        return True


    except Exception as exc:

        try:

            if cursor is not None:
                cursor.close()

        except Exception:
            pass


        try:

            if raw_connection is not None:
                raw_connection.close()

        except Exception:
            pass


        try:

            if partial_path.exists():
                partial_path.unlink()

        except Exception:
            pass


        write_export_status(
            state_name,
            "failed",
            error=str(
                exc
            ),
            failed_at=datetime.now().isoformat(
                timespec="seconds"
            )
        )

        return False


# ============================================================
# START BACKGROUND EXPORT
# ============================================================

def start_full_export(
    state_name
):

    jobs = (
        get_export_jobs()
    )

    lock = (
        get_export_lock()
    )

    state_key = (
        str(
            state_name
        )
        .strip()
        .upper()
    )


    with lock:

        existing = (
            jobs.get(
                state_key
            )
        )

        if (
            existing is not None
            and not existing.done()
        ):

            return (
                False,
                "An export is already running for this state."
            )


        executor = (
            get_export_executor()
        )

        future = executor.submit(
            generate_full_export_job,
            state_name
        )

        jobs[
            state_key
        ] = future


    return (
        True,
        "Full state export started."
    )


# ============================================================
# SIGNED DOWNLOAD TOKEN
# ============================================================

def create_download_token(
    filename
):

    expires = int(
        time.time()
        + DOWNLOAD_TOKEN_LIFETIME
    )

    payload = (
        f"{filename}|{expires}"
    )

    signature = hmac.new(
        DOWNLOAD_SECRET.encode(
            "utf-8"
        ),
        payload.encode(
            "utf-8"
        ),
        hashlib.sha256
    ).hexdigest()

    raw_token = (
        f"{payload}|{signature}"
    )

    return (
        base64.urlsafe_b64encode(
            raw_token.encode(
                "utf-8"
            )
        )
        .decode(
            "utf-8"
        )
    )


def verify_download_token(
    token
):

    try:

        decoded = (
            base64.urlsafe_b64decode(
                token.encode(
                    "utf-8"
                )
            )
            .decode(
                "utf-8"
            )
        )

        filename, expiry, signature = (
            decoded.rsplit(
                "|",
                2
            )
        )

        expiry = int(
            expiry
        )

        if time.time() > expiry:
            return None


        payload = (
            f"{filename}|{expiry}"
        )

        expected_signature = hmac.new(
            DOWNLOAD_SECRET.encode(
                "utf-8"
            ),
            payload.encode(
                "utf-8"
            ),
            hashlib.sha256
        ).hexdigest()


        if not hmac.compare_digest(
            signature,
            expected_signature
        ):
            return None


        # Directory traversal protection
        if Path(
            filename
        ).name != filename:

            return None


        return filename


    except Exception:

        return None


# ============================================================
# DIRECT DOWNLOAD HTTP SERVER
# ============================================================

class ExportDownloadHandler(
    BaseHTTPRequestHandler
):

    def log_message(
        self,
        format,
        *args
    ):
        return


    def do_GET(self):

        parsed = urlparse(
            self.path
        )


        if parsed.path != "/download":

            self.send_error(
                404,
                "Not Found"
            )

            return


        query = parse_qs(
            parsed.query
        )


        tokens = query.get(
            "token"
        )


        if not tokens:

            self.send_error(
                403,
                "Missing download token."
            )

            return


        filename = verify_download_token(
            tokens[0]
        )


        if not filename:

            self.send_error(
                403,
                "Invalid or expired download link."
            )

            return


        file_path = (
            EXPORT_DIR
            / filename
        )


        if (
            not file_path.exists()
            or not file_path.is_file()
        ):

            self.send_error(
                404,
                "Export file not found."
            )

            return


        file_size = (
            file_path.stat().st_size
        )


        # ====================================================
        # RANGE DOWNLOAD SUPPORT
        # ====================================================

        range_header = (
            self.headers.get(
                "Range"
            )
        )

        start = 0
        end = (
            file_size - 1
        )


        if range_header:

            match = re.match(
                r"bytes=(\d*)-(\d*)",
                range_header
            )

            if match:

                if match.group(1):

                    start = int(
                        match.group(1)
                    )


                if match.group(2):

                    end = int(
                        match.group(2)
                    )


                if end >= file_size:

                    end = (
                        file_size - 1
                    )


                if start > end:

                    self.send_error(
                        416,
                        "Requested Range Not Satisfiable"
                    )

                    return


                self.send_response(
                    206
                )


                self.send_header(
                    "Content-Range",
                    f"bytes {start}-{end}/{file_size}"
                )

            else:

                self.send_response(
                    200
                )

        else:

            self.send_response(
                200
            )


        content_length = (
            end - start + 1
        )


        self.send_header(
            "Content-Type",
            "application/gzip"
        )

        self.send_header(
            "Content-Length",
            str(
                content_length
            )
        )

        self.send_header(
            "Accept-Ranges",
            "bytes"
        )

        self.send_header(
            "Cache-Control",
            "private, no-store"
        )

        self.send_header(
            "Content-Disposition",
            (
                f'attachment; '
                f'filename="{filename}"'
            )
        )

        self.end_headers()


        try:

            with open(
                file_path,
                "rb"
            ) as file:

                file.seek(
                    start
                )

                remaining = (
                    content_length
                )

                while remaining > 0:

                    chunk = file.read(
                        min(
                            2 * 1024 * 1024,
                            remaining
                        )
                    )

                    if not chunk:
                        break

                    self.wfile.write(
                        chunk
                    )

                    remaining -= len(
                        chunk
                    )


        except (
            BrokenPipeError,
            ConnectionResetError
        ):

            pass


# ============================================================
# START DIRECT FILE SERVER
# ============================================================

@st.cache_resource
def start_download_server():

    try:

        server = ThreadingHTTPServer(
            (
                DOWNLOAD_HOST,
                DOWNLOAD_PORT
            ),
            ExportDownloadHandler
        )

        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
            name="NCTO_Direct_Download_Server"
        )

        thread.start()

        return server

    except OSError:

        # Port probably already active
        return None


start_download_server()


def build_download_url(
    filename
):

    token = create_download_token(
        filename
    )

    return (
        f"{PUBLIC_DOWNLOAD_BASE_URL}"
        f"/download?"
        f"token={quote(token)}"
    )


# ============================================================
# SESSION INITIALIZATION
# ============================================================

if "logged_in" not in st.session_state:

    st.session_state.logged_in = False


# ============================================================
# LOGIN PAGE
# ============================================================

if not st.session_state.logged_in:

    st.title(
        "🔐 NCTO State Officer Portal"
    )

    st.subheader(
        "State Officer Login"
    )

    username = st.text_input(
        "Username",
        key="login_username"
    )

    password = st.text_input(
        "Password",
        type="password",
        key="login_password"
    )


    if st.button(
        "Login",
        type="primary"
    ):

        try:

            user = authenticate_user(
                username.strip(),
                password
            )

            if user:

                st.session_state.logged_in = True

                st.session_state.username = (
                    user["username"]
                )

                st.session_state.state = (
                    user["state"]
                )

                # Clear any previous user's detail result
                st.session_state.pop(
                    "beneficiary_details_df",
                    None
                )

                st.rerun()

            else:

                st.error(
                    "Invalid username or password."
                )

        except Exception as exc:

            st.error(
                f"Unable to login: {exc}"
            )


    st.stop()


# ============================================================
# LOGGED IN USER
# ============================================================

assigned_state = str(
    st.session_state.state
).strip()

logged_username = (
    st.session_state.username
)


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "NCTO State Portal"
)

st.sidebar.success(
    f"User: {logged_username}"
)

st.sidebar.info(
    f"State: {assigned_state}"
)


menu = st.sidebar.radio(
    "Navigation",
    [
        "State Summary",
        "LGA Summary",
        "Beneficiary Details",
        "Full State Export"
    ],
    key="main_navigation"
)


st.sidebar.divider()


if st.sidebar.button(
    "Logout"
):

    st.session_state.clear()

    st.rerun()


# ============================================================
# MAIN HEADER
# ============================================================

st.title(
    "NCTO State Officer Payment Data Portal"
)

st.caption(
    f"Assigned State: {assigned_state}"
)


# ============================================================
# PAGE 1 — STATE SUMMARY
# ============================================================

if menu == "State Summary":

    st.header(
        "State Summary"
    )


    try:

        with st.spinner(
            "Loading state summary..."
        ):

            summary_df = load_state_summary(
                assigned_state
            )

            unique_total = load_state_unique_total(
                assigned_state
            )


        c1, c2, c3 = st.columns(
            3
        )


        c1.metric(
            "Total Beneficiaries",
            f"{unique_total:,}"
        )


        c2.metric(
            "Total Households",
            f"{unique_total:,}"
        )


        tranche_count = (
            summary_df[
                "tranche"
            ].nunique()
            if not summary_df.empty
            else 0
        )


        c3.metric(
            "Tranches Available",
            f"{tranche_count:,}"
        )


        if summary_df.empty:

            st.warning(
                "No state summary found."
            )

        else:

            st.dataframe(
                summary_df,
                use_container_width=True,
                hide_index=True
            )


            state_summary_excel = (
                dataframe_to_excel_bytes(
                    summary_df,
                    "State Summary"
                )
            )


            st.download_button(
                label="Download State Summary",
                data=state_summary_excel,
                file_name=(
                    f"{safe_filename(assigned_state)}"
                    "_State_Summary.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
                key="state_summary_download"
            )


    except Exception as exc:

        st.error(
            f"Unable to load state summary: {exc}"
        )


# ============================================================
# PAGE 2 — LGA SUMMARY
# ============================================================

elif menu == "LGA Summary":

    st.header(
        "LGA Summary"
    )


    try:

        with st.spinner(
            "Loading LGA summary..."
        ):

            lga_df = load_lga_summary(
                assigned_state
            )


        if lga_df.empty:

            st.warning(
                "No LGA summary found."
            )

        else:

            st.success(
                f"{len(lga_df):,} LGAs found."
            )


            st.dataframe(
                lga_df,
                use_container_width=True,
                hide_index=True,
                height=600
            )


            lga_summary_excel = (
                dataframe_to_excel_bytes(
                    lga_df,
                    "LGA Summary"
                )
            )


            st.download_button(
                label="Download LGA Summary",
                data=lga_summary_excel,
                file_name=(
                    f"{safe_filename(assigned_state)}"
                    "_LGA_Summary.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-"
                    "officedocument.spreadsheetml.sheet"
                ),
                key="lga_summary_download"
            )


    except Exception as exc:

        st.error(
            f"Unable to load LGA summary: {exc}"
        )


# ============================================================
# PAGE 3 — BENEFICIARY DETAILS
# ============================================================

elif menu == "Beneficiary Details":

    st.header(
        "Beneficiary Details"
    )

    st.caption(
        "Search beneficiary payment records for your assigned state."
    )


    col1, col2, col3 = st.columns(
        [1, 2, 1]
    )


    with col1:

        tranche_filter = st.selectbox(
            "Tranche",
            [
                "All",
                "First Tranche",
                "Second Tranche",
                "Third Tranche"
            ],
            key="detail_tranche"
        )


    with col2:

        search_value = st.text_input(
            "Search",
            placeholder=(
                "NID, NIDHH, Name, Telephone, "
                "Account Number or Household ID"
            ),
            key="detail_search"
        )


    with col3:

        limit_rows = st.number_input(
            "Maximum Rows",
            min_value=1000,
            max_value=200000,
            value=50000,
            step=10000,
            key="detail_limit"
        )


    if st.button(
        "Load Beneficiary Details",
        type="primary",
        key="load_beneficiary_details"
    ):

        try:

            with st.spinner(
                "Loading beneficiary records..."
            ):

                details_df = load_state_data(
                    assigned_state,
                    tranche_filter,
                    search_value.strip(),
                    limit_rows
                )


            st.session_state[
                "beneficiary_details_df"
            ] = details_df


        except Exception as exc:

            st.error(
                f"Unable to load beneficiary details: {exc}"
            )


    if (
        "beneficiary_details_df"
        in st.session_state
    ):

        details_df = st.session_state[
            "beneficiary_details_df"
        ]


        if details_df.empty:

            st.warning(
                "No matching records found."
            )

        else:

            st.success(
                f"{len(details_df):,} records loaded."
            )


            st.dataframe(
                details_df,
                use_container_width=True,
                hide_index=True,
                height=600
            )


            csv_data = dataframe_to_csv_bytes(
                details_df
            )


            st.download_button(
                label="Download Loaded Details as CSV",
                data=csv_data,
                file_name=(
                    f"{safe_filename(assigned_state)}"
                    "_Beneficiary_Details.csv"
                ),
                mime="text/csv",
                key="filtered_csv_download"
            )


# ============================================================
# PAGE 4 — FULL STATE EXPORT
# ============================================================

elif menu == "Full State Export":

    st.header(
        "Full State Beneficiary Export"
    )


    st.info(
        "This option is designed for very large datasets. "
        "The export runs in the background using PostgreSQL COPY, "
        "is compressed as CSV.GZ, validated, and then downloaded "
        "directly from the file server."
    )


    col1, col2 = st.columns(
        2
    )


    with col1:

        if st.button(
            "Prepare Full State Export",
            type="primary",
            use_container_width=True,
            key="prepare_full_export"
        ):

            try:

                started, message = (
                    start_full_export(
                        assigned_state
                    )
                )


                if started:

                    st.success(
                        "Export started successfully."
                    )

                    st.info(
                        "You may leave this section and continue "
                        "using the portal. Return later and click "
                        "'Check Export Status'."
                    )

                else:

                    st.info(
                        message
                    )


            except Exception as exc:

                st.error(
                    f"Unable to start export: {exc}"
                )


    with col2:

        if st.button(
            "Check Export Status",
            use_container_width=True,
            key="check_export_status"
        ):

            st.rerun()


    st.divider()


    export_status = read_export_status(
        assigned_state
    )


    if not export_status:

        st.info(
            "No full-state export has been prepared yet."
        )


    else:

        current_status = export_status.get(
            "status"
        )


        # ----------------------------------------------------
        # RUNNING
        # ----------------------------------------------------

        if current_status == "running":

            st.warning(
                "The full state export is currently being prepared."
            )


            started_at = export_status.get(
                "started_at"
            )


            if started_at:

                st.write(
                    f"Started: {started_at}"
                )


            st.caption(
                "You do not need to keep this page open."
            )


        # ----------------------------------------------------
        # FAILED
        # ----------------------------------------------------

        elif current_status == "failed":

            st.error(
                "The full state export failed."
            )


            st.code(
                export_status.get(
                    "error",
                    "Unknown export error."
                )
            )


        # ----------------------------------------------------
        # READY
        # ----------------------------------------------------

        elif current_status == "ready":

            filepath = export_status.get(
                "filepath"
            )


            export_file = (
                Path(
                    filepath
                )
                if filepath
                else None
            )


            if (
                export_file is not None
                and export_file.exists()
                and export_file.stat().st_size > 0
            ):

                size_mb = (
                    export_file.stat().st_size
                    / 1024
                    / 1024
                )


                info1, info2 = st.columns(
                    2
                )


                info1.metric(
                    "Status",
                    "READY"
                )


                info2.metric(
                    "Compressed File Size",
                    f"{size_mb:,.1f} MB"
                )


                completed_at = export_status.get(
                    "completed_at"
                )


                if completed_at:

                    st.caption(
                        f"Completed: {completed_at}"
                    )


                direct_url = build_download_url(
                    export_file.name
                )


                st.link_button(
                    "⬇ Download Full State Export",
                    direct_url,
                    type="primary",
                    use_container_width=True
                )


                st.caption(
                    "The file downloads directly through the browser "
                    "and does not pass through Streamlit memory."
                )


                st.caption(
                    "The downloaded file is a compressed CSV (.csv.gz). "
                    "Extract it using 7-Zip, WinRAR or another GZIP-compatible tool."
                )


            else:

                st.error(
                    "The export status says READY, but the completed "
                    "file cannot be found. Generate a new export."
                )
