import gzip
import io
import os
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO, Optional

import bcrypt
import extra_streamlit_components as stx
import pandas as pd
import streamlit as st
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine


# ============================================================
# 1. STREAMLIT PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="NCTO State Officer Portal",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# 2. APPLICATION CONSTANTS
# ============================================================

APP_TITLE = "NCTO State Officer Portal"
CACHE_TTL_SECONDS = 900
SEARCH_DEFAULT_LIMIT = 1_000
SEARCH_MAX_LIMIT = 10_000
EXPORT_RETENTION_HOURS = 12

PAYMENT_TABLES = (
    ("First Tranche", 'ben."itblDistinctPaidBeneficiaries"'),
    ("Second Tranche", 'ben."itblDistinctSecondTranche"'),
    ("Third Tranche", 'ben."itblDistinctThirdTranche"'),
)

DETAIL_COLUMNS = [
    "tranche",
    "State",
    "LGA",
    "Ward",
    "Community",
    "HouseholdID",
    "nidhh",
    "nid",
    "Name",
    "TelephoneNo",
    "Gender",
    "Age",
    "HAddress",
    "AccountName",
    "AccountNumber",
    "BankName",
    "AmountPaid",
    "PaymentStatus",
    "PaymentDate",
    "TrancheStatus",
    "TotalAmount",
    "Zone",
    "ward_class",
]


# ============================================================
# 3. DATABASE CONFIGURATION
# ============================================================

def get_setting(name: str, default: Optional[str] = None) -> Optional[str]:
    """
    Read a setting from Streamlit secrets first and then environment variables.

    Example .streamlit/secrets.toml:

        PG_HOST = "102.164.37.69"
        PG_PORT = "5432"
        PG_DATABASE = "ben_db"
        PG_USER = "ben_user"
        PG_PASSWORD = "your-password"
    """
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass

    return os.getenv(name, default)


PG_HOST = get_setting("PG_HOST", "102.164.37.69")
PG_PORT = int(get_setting("PG_PORT", "5432") or 5432)
PG_DATABASE = get_setting("PG_DATABASE", "ben_db")
PG_USER = get_setting("PG_USER", "ben_user")
PG_PASSWORD = get_setting("PG_PASSWORD")

if not PG_PASSWORD:
    st.error(
        "Database password is not configured. Add PG_PASSWORD to "
        ".streamlit/secrets.toml or to the server environment."
    )
    st.stop()

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg2",
    username=PG_USER,
    password=PG_PASSWORD,
    host=PG_HOST,
    port=PG_PORT,
    database=PG_DATABASE,
)


@st.cache_resource
def get_engine() -> Engine:
    """
    Create one reusable SQLAlchemy connection pool.

    The original code created a global engine without recycling stale
    connections. The settings below are safer for a long-running Streamlit app.
    """
    return create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=1_800,
        pool_size=5,
        max_overflow=5,
        connect_args={
            "connect_timeout": 20,
            "application_name": "ncto_state_officer_portal",
        },
    )


# ============================================================
# 4. GENERAL HELPERS
# ============================================================

def normalize_filename(value: str) -> str:
    """Return a safe component for generated filenames."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value).strip())
    return cleaned.strip("_") or "state"


def dataframe_to_excel_bytes(
    dataframe: pd.DataFrame,
    sheet_name: str,
) -> bytes:
    """Create an Excel download in memory without writing into the app folder."""
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        dataframe.to_excel(
            writer,
            index=False,
            sheet_name=sheet_name[:31],
        )

    output.seek(0)
    return output.getvalue()


def dataframe_to_csv_bytes(dataframe: pd.DataFrame) -> bytes:
    """Create an Excel-compatible UTF-8 CSV from a DataFrame."""
    return dataframe.to_csv(
        index=False,
        lineterminator="\n",
    ).encode("utf-8-sig")


def format_file_size(size_bytes: int) -> str:
    """Convert a byte count into a readable file size."""
    size = float(size_bytes)

    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:,.2f} {unit}"
        size /= 1024

    return f"{size_bytes:,} B"


def get_export_directory() -> Path:
    """
    Return a server-side temporary export directory.

    This is intentionally not the user's local Downloads folder. A Streamlit
    server cannot directly write into a remote user's browser Downloads folder.
    The generated file stays on the server until the user clicks Download.
    """
    export_dir = Path(tempfile.gettempdir()) / "ncto_state_portal_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


def cleanup_old_export_files() -> None:
    """Delete abandoned export files older than the configured retention period."""
    cutoff = datetime.now() - timedelta(hours=EXPORT_RETENTION_HOURS)

    for file_path in get_export_directory().glob("ncto_export_*"):
        try:
            modified_at = datetime.fromtimestamp(file_path.stat().st_mtime)
            if modified_at < cutoff:
                file_path.unlink(missing_ok=True)
        except OSError:
            continue


# ============================================================
# 5. AUTHENTICATION
# ============================================================

def verify_password(plain_password: str, password_hash: str) -> bool:
    """Validate a plaintext password against a bcrypt hash."""
    if not plain_password or not password_hash:
        return False

    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            password_hash.encode("utf-8"),
        )
    except (TypeError, ValueError):
        return False


def authenticate_user(
    username: str,
    password: str,
) -> Optional[dict[str, str]]:
    """Authenticate an active state officer."""
    if not username or not password:
        return None

    query = text(
        """
        SELECT
            username,
            password_hash,
            assigned_state
        FROM ben.state_officer_users
        WHERE LOWER(username) = LOWER(:username)
          AND is_active = TRUE
        LIMIT 1;
        """
    )

    try:
        with get_engine().connect() as connection:
            user = connection.execute(
                query,
                {"username": username.strip()},
            ).mappings().first()

        if not user:
            return None

        if not verify_password(password, user["password_hash"]):
            return None

        return {
            "username": str(user["username"]),
            "state": str(user["assigned_state"]),
        }

    except Exception as exc:
        st.error(f"Database error during login: {exc}")
        return None


# ============================================================
# 6. SESSION AND COOKIE MANAGEMENT
# ============================================================

def initialize_session_state() -> stx.CookieManager:
    """Initialize all persistent session keys."""
    defaults: dict[str, Any] = {
        "logged_in": False,
        "logged_in_user": None,
        "assigned_state": None,
        "search_results": None,
        "search_parameters": None,
        "full_export_path": None,
        "full_export_rows": None,
        "full_export_state": None,
    }

    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value

    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager()

    return st.session_state.cookie_manager


def restore_session_from_cookies(
    cookie_manager: stx.CookieManager,
) -> None:
    """Restore a saved browser session only when no session is active."""
    if st.session_state.logged_in:
        return

    saved_username = cookie_manager.get(cookie="ncto_logged_in_user")
    saved_state = cookie_manager.get(cookie="ncto_user_state")

    if saved_username and saved_state:
        st.session_state.logged_in = True
        st.session_state.logged_in_user = saved_username
        st.session_state.assigned_state = saved_state


def save_session_cookies(
    cookie_manager: stx.CookieManager,
    username: str,
    assigned_state: str,
) -> None:
    """Save login cookies for seven days."""
    max_age = 7 * 24 * 60 * 60

    cookie_manager.set(
        "ncto_logged_in_user",
        username,
        max_age=max_age,
        key="set_cookie_username",
    )
    cookie_manager.set(
        "ncto_user_state",
        assigned_state,
        max_age=max_age,
        key="set_cookie_state",
    )


def logout(cookie_manager: stx.CookieManager) -> None:
    """Remove cookies and clear user-specific session data."""
    cookie_manager.delete(
        "ncto_logged_in_user",
        key="delete_cookie_username",
    )
    cookie_manager.delete(
        "ncto_user_state",
        key="delete_cookie_state",
    )

    export_path = st.session_state.get("full_export_path")
    if export_path:
        try:
            Path(export_path).unlink(missing_ok=True)
        except OSError:
            pass

    for key in list(st.session_state.keys()):
        if key != "cookie_manager":
            del st.session_state[key]

    st.session_state.logged_in = False


def render_login_page(cookie_manager: stx.CookieManager) -> None:
    """Render a compact login form."""
    left, middle, right = st.columns([1, 1.3, 1])

    with middle:
        st.title("🔐 NCTO State MIS Login")
        st.caption("Sign in with your assigned State Officer account.")

        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button(
                "Login",
                use_container_width=True,
            )

        if submitted:
            user = authenticate_user(username.strip(), password)

            if not user:
                st.error("Invalid username or password.")
                st.stop()

            st.session_state.logged_in = True
            st.session_state.logged_in_user = user["username"]
            st.session_state.assigned_state = user["state"]

            save_session_cookies(
                cookie_manager,
                user["username"],
                user["state"],
            )

            time.sleep(0.2)
            st.rerun()

    st.stop()


# ============================================================
# 7. SUMMARY QUERIES
# ============================================================

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_state_summary(state_name: str) -> pd.DataFrame:
    """
    Load only state/tranche aggregates.

    Each table is filtered before UNION ALL. This avoids building a national
    three-table CTE before applying the state condition.
    """
    query = text(
        """
        WITH state_payments AS (
            SELECT
                'First Tranche'::TEXT AS tranche,
                ben.normalize_location_name("State") AS state,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh,
                COALESCE(CAST("AmountPaid" AS NUMERIC), 0) AS amount_paid
            FROM ben."itblDistinctPaidBeneficiaries"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)

            UNION ALL

            SELECT
                'Second Tranche'::TEXT AS tranche,
                ben.normalize_location_name("State") AS state,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh,
                COALESCE(CAST("AmountPaid" AS NUMERIC), 0) AS amount_paid
            FROM ben."itblDistinctSecondTranche"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)

            UNION ALL

            SELECT
                'Third Tranche'::TEXT AS tranche,
                ben.normalize_location_name("State") AS state,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh,
                COALESCE(CAST("AmountPaid" AS NUMERIC), 0) AS amount_paid
            FROM ben."itblDistinctThirdTranche"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)
        )
        SELECT
            tranche,
            MIN(state) AS state,
            COUNT(DISTINCT nidhh) AS total_households,
            COUNT(DISTINCT nidhh) AS total_beneficiaries,
            COALESCE(SUM(amount_paid), 0) AS total_amount_paid,
            COUNT(DISTINCT lga) FILTER (WHERE lga IS NOT NULL) AS total_lgas,
            COUNT(DISTINCT (lga, ward))
                FILTER (WHERE lga IS NOT NULL AND ward IS NOT NULL)
                AS total_wards,
            COUNT(DISTINCT (lga, ward, community))
                FILTER (
                    WHERE lga IS NOT NULL
                      AND ward IS NOT NULL
                      AND community IS NOT NULL
                )
                AS total_communities
        FROM state_payments
        GROUP BY tranche
        ORDER BY
            CASE tranche
                WHEN 'First Tranche' THEN 1
                WHEN 'Second Tranche' THEN 2
                WHEN 'Third Tranche' THEN 3
                ELSE 4
            END;
        """
    )

    with get_engine().connect() as connection:
        return pd.read_sql_query(
            query,
            connection,
            params={"state_name": state_name},
        )


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_state_unique_total(state_name: str) -> int:
    """Load the number of unique households/beneficiaries across all tranches."""
    query = text(
        """
        SELECT COUNT(DISTINCT nidhh)
        FROM (
            SELECT CAST(nidhh AS TEXT) AS nidhh
            FROM ben."itblDistinctPaidBeneficiaries"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)

            UNION ALL

            SELECT CAST(nidhh AS TEXT) AS nidhh
            FROM ben."itblDistinctSecondTranche"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)

            UNION ALL

            SELECT CAST(nidhh AS TEXT) AS nidhh
            FROM ben."itblDistinctThirdTranche"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)
        ) AS state_households;
        """
    )

    with get_engine().connect() as connection:
        result = connection.execute(
            query,
            {"state_name": state_name},
        ).scalar()

    return int(result or 0)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_lga_summary(state_name: str) -> pd.DataFrame:
    """Load LGA aggregates only when the LGA Summary page is selected."""
    query = text(
        """
        WITH state_payments AS (
            SELECT
                'First Tranche'::TEXT AS tranche,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh,
                COALESCE(CAST("AmountPaid" AS NUMERIC), 0) AS amount_paid
            FROM ben."itblDistinctPaidBeneficiaries"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)

            UNION ALL

            SELECT
                'Second Tranche'::TEXT AS tranche,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh,
                COALESCE(CAST("AmountPaid" AS NUMERIC), 0) AS amount_paid
            FROM ben."itblDistinctSecondTranche"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)

            UNION ALL

            SELECT
                'Third Tranche'::TEXT AS tranche,
                ben.normalize_location_name("LGA") AS lga,
                ben.normalize_location_name("Ward") AS ward,
                ben.normalize_location_name("Community") AS community,
                CAST(nidhh AS TEXT) AS nidhh,
                COALESCE(CAST("AmountPaid" AS NUMERIC), 0) AS amount_paid
            FROM ben."itblDistinctThirdTranche"
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)
        )
        SELECT
            COALESCE(lga, 'UNKNOWN LGA') AS "LGA",
            COUNT(DISTINCT nidhh)
                FILTER (WHERE tranche = 'First Tranche')
                AS "First_Tranche_Beneficiaries",
            COUNT(DISTINCT nidhh)
                FILTER (WHERE tranche = 'Second Tranche')
                AS "Second_Tranche_Beneficiaries",
            COUNT(DISTINCT nidhh)
                FILTER (WHERE tranche = 'Third Tranche')
                AS "Third_Tranche_Beneficiaries",
            COUNT(DISTINCT nidhh) AS "Total_Unique_Beneficiaries",

            COALESCE(
                SUM(amount_paid) FILTER (WHERE tranche = 'First Tranche'),
                0
            ) AS "First_Tranche_Amount",

            COALESCE(
                SUM(amount_paid) FILTER (WHERE tranche = 'Second Tranche'),
                0
            ) AS "Second_Tranche_Amount",

            COALESCE(
                SUM(amount_paid) FILTER (WHERE tranche = 'Third Tranche'),
                0
            ) AS "Third_Tranche_Amount",

            COALESCE(SUM(amount_paid), 0) AS "Cumulative_Amount",

            COUNT(DISTINCT ward)
                FILTER (WHERE ward IS NOT NULL)
                AS "Total_Wards",
            COUNT(DISTINCT (ward, community))
                FILTER (WHERE ward IS NOT NULL AND community IS NOT NULL)
                AS "Total_Communities"
        FROM state_payments
        GROUP BY lga
        ORDER BY COALESCE(lga, 'UNKNOWN LGA');
        """
    )

    with get_engine().connect() as connection:
        return pd.read_sql_query(
            query,
            connection,
            params={"state_name": state_name},
        )


# ============================================================
# 8. SEARCH QUERY
# ============================================================

def build_search_union_sql(
    tranche_filter: str,
    include_search: bool,
) -> str:
    """Build a controlled UNION query for search preview."""
    selected_tables = PAYMENT_TABLES

    if tranche_filter != "All":
        selected_tables = tuple(
            item for item in PAYMENT_TABLES if item[0] == tranche_filter
        )

    branches = []

    for tranche_name, table_name in selected_tables:
        branch = f"""
            SELECT
                '{tranche_name}'::TEXT AS tranche,
                CAST("State" AS TEXT) AS "State",
                CAST("LGA" AS TEXT) AS "LGA",
                CAST("Ward" AS TEXT) AS "Ward",
                CAST("Community" AS TEXT) AS "Community",
                CAST("HouseholdID" AS TEXT) AS "HouseholdID",
                CAST(nidhh AS TEXT) AS nidhh,
                CAST(nid AS TEXT) AS nid,
                CAST("Name" AS TEXT) AS "Name",
                CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
                CAST("Gender" AS TEXT) AS "Gender",
                CAST("Age" AS TEXT) AS "Age",
                CAST("HAddress" AS TEXT) AS "HAddress",
                CAST("AccountName" AS TEXT) AS "AccountName",
                CAST("AccountNumber" AS TEXT) AS "AccountNumber",
                CAST("BankName" AS TEXT) AS "BankName",
                CAST("AmountPaid" AS TEXT) AS "AmountPaid",
                CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
                CAST("PaymentDate" AS TEXT) AS "PaymentDate",
                CAST("NIN" AS TEXT) AS "NIN",
                CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
                CAST("TotalAmount" AS TEXT) AS "TotalAmount",
                CAST("Zone" AS TEXT) AS "Zone",
                CAST("ward_class" AS TEXT) AS "ward_class"
            FROM {table_name}
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(:state_name)
        """

        if include_search:
            branch += """
                AND (
                       CAST(nid AS TEXT) ILIKE :search_value
                    OR CAST(nidhh AS TEXT) ILIKE :search_value
                    OR CAST("NIN" AS TEXT) ILIKE :search_value
                    OR CAST("AccountNumber" AS TEXT) ILIKE :search_value
                    OR CAST("Name" AS TEXT) ILIKE :search_value
                    OR CAST("HouseholdID" AS TEXT) ILIKE :search_value
                )
            """

        branches.append(branch)

    return "\nUNION ALL\n".join(branches)


def search_state_data(
    state_name: str,
    tranche_filter: str,
    search_value: str,
    limit_rows: int,
) -> pd.DataFrame:
    """
    Load search results into a dedicated result table.

    This function is deliberately not cached because each search is explicitly
    submitted through a form and saved in session state.
    """
    union_sql = build_search_union_sql(
        tranche_filter=tranche_filter,
        include_search=bool(search_value),
    )

    query = text(
        f"""
        SELECT *
        FROM (
            {union_sql}
        ) AS matches
        ORDER BY
            tranche,
            "LGA",
            "Ward",
            "Community",
            "Name"
        LIMIT :limit_rows;
        """
    )

    params: dict[str, Any] = {
        "state_name": state_name,
        "limit_rows": int(limit_rows),
    }

    if search_value:
        params["search_value"] = f"%{search_value.strip()}%"

    with get_engine().connect() as connection:
        return pd.read_sql_query(query, connection, params=params)


# ============================================================
# 9. HIGH-PERFORMANCE FULL EXPORT
# ============================================================

def build_full_export_select_sql() -> str:
    """
    Build the full export SELECT.

    Important optimizations:
    - State filtering happens inside each table branch.
    - There is no ORDER BY. Sorting millions of rows before export is expensive
      and does not add value to a raw full-detail download.
    - PostgreSQL COPY writes rows directly to disk without pandas or StringIO.
    """
    branches = []

    for tranche_name, table_name in PAYMENT_TABLES:
        branches.append(
            f"""
            SELECT
                '{tranche_name}'::TEXT AS tranche,
                CAST("State" AS TEXT) AS "State",
                CAST("LGA" AS TEXT) AS "LGA",
                CAST("Ward" AS TEXT) AS "Ward",
                CAST("Community" AS TEXT) AS "Community",
                CAST("HouseholdID" AS TEXT) AS "HouseholdID",
                CAST(nidhh AS TEXT) AS nidhh,
                CAST(nid AS TEXT) AS nid,
                CAST("Name" AS TEXT) AS "Name",
                CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
                CAST("Gender" AS TEXT) AS "Gender",
                CAST("Age" AS TEXT) AS "Age",
                CAST("HAddress" AS TEXT) AS "HAddress",
                CAST("AccountName" AS TEXT) AS "AccountName",
                CAST("AccountNumber" AS TEXT) AS "AccountNumber",
                CAST("BankName" AS TEXT) AS "BankName",
                CAST("AmountPaid" AS TEXT) AS "AmountPaid",
                CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
                CAST("PaymentDate" AS TEXT) AS "PaymentDate",
                CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
                CAST("TotalAmount" AS TEXT) AS "TotalAmount",
                CAST("Zone" AS TEXT) AS "Zone",
                CAST("ward_class" AS TEXT) AS "ward_class"
            FROM {table_name}
            WHERE ben.normalize_location_name("State")
                  = ben.normalize_location_name(%s)
            """
        )

    return "\nUNION ALL\n".join(branches)


def prepare_full_state_export(
    state_name: str,
    compress_file: bool = True,
) -> tuple[str, int]:
    """
    Stream a complete state export from PostgreSQL directly into a server file.

    PostgreSQL COPY is significantly faster and more memory-efficient than:
        pandas.read_sql(..., chunksize=...)
        -> DataFrame conversion
        -> StringIO concatenation
        -> st.download_button

    Returns:
        (generated_file_path, approximate_row_count)
    """
    cleanup_old_export_files()

    safe_state = normalize_filename(state_name).lower()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ".csv.gz" if compress_file else ".csv"

    output_path = (
        get_export_directory()
        / f"ncto_export_{safe_state}_{timestamp}{suffix}"
    )

    select_sql = build_full_export_select_sql()
    engine = get_engine()
    raw_connection = engine.raw_connection()

    try:
        cursor = raw_connection.cursor()

        # Safely insert the same state parameter into all three UNION branches.
        rendered_select = cursor.mogrify(
            select_sql,
            (state_name, state_name, state_name),
        ).decode("utf-8")

        copy_sql = (
            f"COPY ({rendered_select}) "
            "TO STDOUT WITH (FORMAT CSV, HEADER TRUE, ENCODING 'UTF8')"
        )

        if compress_file:
            with gzip.open(
                output_path,
                mode="wt",
                encoding="utf-8-sig",
                newline="",
                compresslevel=5,
            ) as output_file:
                cursor.copy_expert(copy_sql, output_file)
        else:
            with open(
                output_path,
                mode="w",
                encoding="utf-8-sig",
                newline="",
            ) as output_file:
                cursor.copy_expert(copy_sql, output_file)

        raw_connection.commit()
        cursor.close()

    except Exception:
        raw_connection.rollback()
        output_path.unlink(missing_ok=True)
        raise

    finally:
        raw_connection.close()

    # Counting lines in a compressed multi-million-row file would add another
    # long scan. Use the already cached state unique total as the displayed
    # beneficiary estimate instead.
    estimated_rows = load_state_unique_total(state_name)
    return str(output_path), estimated_rows


def open_export_file(file_path: str) -> BinaryIO:
    """Open a prepared export for Streamlit's download button."""
    return open(file_path, "rb")


def clear_current_export() -> None:
    """Delete the current session's generated export."""
    export_path = st.session_state.get("full_export_path")

    if export_path:
        try:
            Path(export_path).unlink(missing_ok=True)
        except OSError:
            pass

    st.session_state.full_export_path = None
    st.session_state.full_export_rows = None
    st.session_state.full_export_state = None


# ============================================================
# 10. SIDEBAR NAVIGATION
# ============================================================

def render_sidebar(
    cookie_manager: stx.CookieManager,
    assigned_state: str,
) -> str:
    """Render navigation so only one data page runs per rerun."""
    st.sidebar.title("NCTO MIS Portal")
    st.sidebar.success(
        f"User: {st.session_state.logged_in_user}"
    )
    st.sidebar.info(f"State: {assigned_state}")

    st.sidebar.markdown("---")

    page = st.sidebar.radio(
        "Navigation",
        options=[
            "Home",
            "State Summary",
            "LGA Summary",
            "Beneficiary Search",
            "Full Details Export",
        ],
        captions=[
            "Portal overview",
            "State and tranche metrics",
            "LGA-level distribution",
            "Search without reloading summaries",
            "Prepare the complete CSV file",
        ],
    )

    st.sidebar.markdown("---")

    if st.sidebar.button("Logout", use_container_width=True):
        logout(cookie_manager)
        time.sleep(0.2)
        st.rerun()

    return page


# ============================================================
# 11. PAGE RENDERERS
# ============================================================

def render_home_page(assigned_state: str) -> None:
    """Render a lightweight landing page with no payment-table query."""
    st.title("NCTO State Officer Portal")
    st.subheader(f"{assigned_state} State")

    st.info(
        "Select a function from the sidebar. Each section now loads separately, "
        "so opening the portal does not automatically run all summary, search, "
        "and export queries."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("#### State Summary")
        st.write("View tranche, household, LGA, ward, and community totals.")

    with col2:
        st.markdown("#### Beneficiary Search")
        st.write("Search a separate result table without recalculating summaries.")

    with col3:
        st.markdown("#### Full Export")
        st.write("Prepare a compressed CSV directly from PostgreSQL.")


def render_state_summary_page(assigned_state: str) -> None:
    """Render only the state summary query and its download."""
    st.title("State Summary")
    st.caption(f"Payment summary for {assigned_state} State")

    try:
        with st.spinner("Loading state summary..."):
            summary_df = load_state_summary(assigned_state)
            unique_total = load_state_unique_total(assigned_state)

    except Exception as exc:
        st.error(f"Unable to load the state summary: {exc}")
        return

    if summary_df.empty:
        st.warning("No state summary was found.")
        return

    cumulative_amount = float(summary_df["total_amount_paid"].sum())

    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("Total Beneficiaries", f"{unique_total:,}")
    metric2.metric("Total Households", f"{unique_total:,}")
    metric3.metric(
        "Tranches Available",
        f"{summary_df['tranche'].nunique():,}",
    )
    metric4.metric(
        "Cumulative Amount Paid",
        f"₦{cumulative_amount:,.2f}",
    )

    display_summary_df = summary_df.rename(
        columns={
            "tranche": "Tranche",
            "state": "State",
            "total_households": "Total Households",
            "total_beneficiaries": "Total Beneficiaries",
            "total_amount_paid": "Amount Paid (₦)",
            "total_lgas": "Total LGAs",
            "total_wards": "Total Wards",
            "total_communities": "Total Communities",
        }
    )

    st.dataframe(
        display_summary_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Amount Paid (₦)": st.column_config.NumberColumn(
                "Amount Paid (₦)",
                format="₦%.2f",
            ),
        },
    )

    summary_excel = dataframe_to_excel_bytes(
        display_summary_df,
        "State Summary",
    )

    st.download_button(
        label="Download State Summary",
        data=summary_excel,
        file_name=(
            f"{normalize_filename(assigned_state)}_State_Summary.xlsx"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
    )


def render_lga_summary_page(assigned_state: str) -> None:
    """Render only the LGA summary query and its download."""
    st.title("LGA Summary")
    st.caption(f"LGA-level payment distribution for {assigned_state} State")

    try:
        with st.spinner("Loading LGA summary..."):
            lga_df = load_lga_summary(assigned_state)

    except Exception as exc:
        st.error(f"Unable to load the LGA summary: {exc}")
        return

    if lga_df.empty:
        st.warning("No LGA summary was found.")
        return

    cumulative_amount = float(lga_df["Cumulative_Amount"].sum())

    metric1, metric2, metric3, metric4 = st.columns(4)
    metric1.metric("LGAs", f"{len(lga_df):,}")
    metric2.metric(
        "Unique Beneficiaries",
        f"{int(lga_df['Total_Unique_Beneficiaries'].sum()):,}",
    )
    metric3.metric(
        "Communities",
        f"{int(lga_df['Total_Communities'].sum()):,}",
    )
    metric4.metric(
        "Cumulative Amount Paid",
        f"₦{cumulative_amount:,.2f}",
    )

    st.dataframe(
        lga_df,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "First_Tranche_Amount": st.column_config.NumberColumn(
                "First Tranche Amount (₦)",
                format="₦%.2f",
            ),
            "Second_Tranche_Amount": st.column_config.NumberColumn(
                "Second Tranche Amount (₦)",
                format="₦%.2f",
            ),
            "Third_Tranche_Amount": st.column_config.NumberColumn(
                "Third Tranche Amount (₦)",
                format="₦%.2f",
            ),
            "Cumulative_Amount": st.column_config.NumberColumn(
                "Cumulative Amount (₦)",
                format="₦%.2f",
            ),
        },
    )

    lga_excel = dataframe_to_excel_bytes(
        lga_df,
        "LGA Summary",
    )

    st.download_button(
        label="Download LGA Summary",
        data=lga_excel,
        file_name=(
            f"{normalize_filename(assigned_state)}_LGA_Summary.xlsx"
        ),
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
    )


def render_search_page(assigned_state: str) -> None:
    """
    Render a standalone search page.

    Results are stored in session state. They do not affect or trigger the
    state/LGA summary functions.
    """
    st.title("Beneficiary Search")
    st.caption(
        "Search results are displayed in a separate table and do not "
        "recalculate the summary pages."
    )

    with st.form("beneficiary_search_form"):
        filter_col1, filter_col2, filter_col3 = st.columns([1, 2, 1])

        with filter_col1:
            tranche_filter = st.selectbox(
                "Tranche",
                [
                    "All",
                    "First Tranche",
                    "Second Tranche",
                    "Third Tranche",
                ],
            )

        with filter_col2:
            search_value = st.text_input(
                "Search",
                placeholder=(
                    "NID, NIDHH, NIN, account number, name, or household ID"
                ),
            )

        with filter_col3:
            limit_rows = st.number_input(
                "Maximum result rows",
                min_value=100,
                max_value=SEARCH_MAX_LIMIT,
                value=SEARCH_DEFAULT_LIMIT,
                step=500,
            )

        search_submitted = st.form_submit_button(
            "Run Search",
            use_container_width=True,
        )

    if search_submitted:
        if not search_value.strip():
            st.warning(
                "Enter a search value. Loading an unfiltered detail table "
                "is disabled on the search page to protect performance."
            )
        else:
            try:
                with st.spinner("Searching beneficiary records..."):
                    results = search_state_data(
                        state_name=assigned_state,
                        tranche_filter=tranche_filter,
                        search_value=search_value,
                        limit_rows=int(limit_rows),
                    )

                st.session_state.search_results = results
                st.session_state.search_parameters = {
                    "tranche": tranche_filter,
                    "search": search_value,
                    "limit": int(limit_rows),
                }

            except Exception as exc:
                st.error(f"Search failed: {exc}")

    results = st.session_state.get("search_results")
    parameters = st.session_state.get("search_parameters")

    if isinstance(results, pd.DataFrame):
        st.markdown("---")

        if results.empty:
            st.info("No matching beneficiary record was found.")
            return

        st.success(f"{len(results):,} matching records loaded.")

        if parameters:
            st.caption(
                f"Tranche: {parameters['tranche']} | "
                f"Search: {parameters['search']} | "
                f"Limit: {parameters['limit']:,}"
            )

        st.dataframe(
            results,
            use_container_width=True,
            hide_index=True,
            height=620,
        )

        st.download_button(
            label="Download Displayed Search Results",
            data=dataframe_to_csv_bytes(results),
            file_name=(
                f"{normalize_filename(assigned_state)}_Search_Results.csv"
            ),
            mime="text/csv",
            use_container_width=True,
        )


def render_full_export_page(assigned_state: str) -> None:
    """Render the dedicated file-based full export page."""
    st.title("Full Beneficiary Details Export")
    st.caption(
        "The export is generated on the server and becomes downloadable "
        "after preparation finishes."
    )

    st.info(
        "For large states, use compressed CSV. PostgreSQL COPY streams records "
        "directly to the file, and the export is not sorted because sorting "
        "millions of rows causes unnecessary delay."
    )

    compress_file = st.checkbox(
        "Compress as CSV.GZ",
        value=True,
        help=(
            "Recommended. The compressed file is usually much smaller and "
            "downloads faster. It can be opened with 7-Zip, WinRAR, or Python."
        ),
    )

    generate_col, clear_col = st.columns([2, 1])

    with generate_col:
        generate_clicked = st.button(
            "Prepare Full State Export",
            type="primary",
            use_container_width=True,
        )

    with clear_col:
        clear_clicked = st.button(
            "Clear Prepared Export",
            use_container_width=True,
        )

    if clear_clicked:
        clear_current_export()
        st.success("Prepared export cleared.")
        st.rerun()

    if generate_clicked:
        clear_current_export()

        try:
            with st.spinner(
                "Streaming state records from PostgreSQL into the export file..."
            ):
                export_path, estimated_rows = prepare_full_state_export(
                    state_name=assigned_state,
                    compress_file=compress_file,
                )

            st.session_state.full_export_path = export_path
            st.session_state.full_export_rows = estimated_rows
            st.session_state.full_export_state = assigned_state

        except Exception as exc:
            st.error(f"Unable to prepare the full export: {exc}")

    export_path = st.session_state.get("full_export_path")
    export_state = st.session_state.get("full_export_state")

    if (
        export_path
        and export_state == assigned_state
        and Path(export_path).exists()
    ):
        file_path = Path(export_path)
        file_size = file_path.stat().st_size
        estimated_rows = int(
            st.session_state.get("full_export_rows") or 0
        )

        st.success("The export file is ready.")
        info1, info2 = st.columns(2)
        info1.metric(
            "Estimated Unique Beneficiaries",
            f"{estimated_rows:,}",
        )
        info2.metric("Prepared File Size", format_file_size(file_size))

        download_file = open_export_file(str(file_path))

        try:
            st.download_button(
                label=f"Download {file_path.name}",
                data=download_file,
                file_name=file_path.name,
                mime=(
                    "application/gzip"
                    if file_path.suffix == ".gz"
                    else "text/csv"
                ),
                use_container_width=True,
            )
        finally:
            download_file.close()


# ============================================================
# 12. APPLICATION ENTRY POINT
# ============================================================

def main() -> None:
    """Run the optimized State Officer Portal."""
    cleanup_old_export_files()

    cookie_manager = initialize_session_state()
    restore_session_from_cookies(cookie_manager)

    if not st.session_state.logged_in:
        render_login_page(cookie_manager)

    assigned_state = st.session_state.assigned_state

    if not assigned_state:
        st.error("The logged-in account has no assigned state.")
        logout(cookie_manager)
        st.stop()

    selected_page = render_sidebar(
        cookie_manager=cookie_manager,
        assigned_state=assigned_state,
    )

    if selected_page == "Home":
        render_home_page(assigned_state)

    elif selected_page == "State Summary":
        render_state_summary_page(assigned_state)

    elif selected_page == "LGA Summary":
        render_lga_summary_page(assigned_state)

    elif selected_page == "Beneficiary Search":
        render_search_page(assigned_state)

    elif selected_page == "Full Details Export":
        render_full_export_page(assigned_state)


if __name__ == "__main__":
    main()
