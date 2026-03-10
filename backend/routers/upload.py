from pathlib import Path
import tempfile
import os
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from migrate_data import Migrator


router = APIRouter()

DATA_TYPE_METHODS: Dict[str, str] = {
    "option_data": "import_option_file",
    "spot_data": "import_spot_file",
    "expiry_calendar": "import_expiry_file",
    "trading_holidays": "import_holiday_file",
    "super_trend_segments": "import_str_file",
}


class UploadSummary(BaseModel):
    data_type: str
    table: str
    file_name: str
    status: str
    rows_read: int = 0
    rows_valid: int = 0
    rows_skipped: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    skip_reason: Optional[str] = None
    errors: List[str] = Field(default_factory=list)


@router.post("/data/upload", response_model=UploadSummary, tags=["data"])
async def upload_csv(
    data_type: str = Form(..., description="Target table name"),
    file: UploadFile = File(..., description="CSV file to import"),
    force: bool = Form(False, description="Re-import even if file already seen"),
):
    """Upload a CSV and import it into the matching PostgreSQL table."""

    normalized = data_type.strip().lower()
    method_name = DATA_TYPE_METHODS.get(normalized)
    if method_name is None:
        allowed = ", ".join(sorted(DATA_TYPE_METHODS.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown data_type '{data_type}'. Allowed: {allowed}",
        )

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be .csv")

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            temp_file.write(chunk)
        temp_file.flush()
        temp_path = Path(temp_file.name)
    finally:
        temp_file.close()

    try:
        migrator = Migrator(force=force)
        import_fn = getattr(migrator, method_name)
        result = import_fn(temp_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return UploadSummary(
        data_type=normalized,
        table=result.get("table", normalized),
        file_name=file.filename,
        status=result.get("status", "unknown"),
        rows_read=result.get("rows_read", 0),
        rows_valid=result.get("rows_valid", 0),
        rows_skipped=result.get("rows_skipped", 0),
        rows_inserted=result.get("rows_inserted", 0),
        rows_updated=result.get("rows_updated", 0),
        skip_reason=result.get("skip_reason"),
        errors=result.get("errors") or [],
    )
