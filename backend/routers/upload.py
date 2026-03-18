from pathlib import Path
import tempfile

import aiofiles
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from worker.tasks import migrate_csv_task

from services.upload_config import DATA_TYPE_METHODS

router = APIRouter()


async def _save_upload_async(upload: UploadFile) -> Path:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    temp_path = Path(temp_file.name)
    temp_file.close()

    async with aiofiles.open(temp_path, "wb") as out_file:
        while chunk := await upload.read(1024 * 1024):
            await out_file.write(chunk)

    return temp_path


@router.post("/data/upload", tags=["data"])
async def upload_csv(
    data_type: str = Form(..., description="Target table name"),
    file: UploadFile = File(..., description="CSV file to import"),
    force: bool = Form(False, description="Re-import even if file already seen"),
):
    """Queue a CSV migration task and return immediately with a job_id."""

    normalized = data_type.strip().lower()
    if normalized not in DATA_TYPE_METHODS:
        allowed = ", ".join(sorted(DATA_TYPE_METHODS.keys()))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown data_type '{data_type}'. Allowed: {allowed}",
        )

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Uploaded file must be .csv")

    temp_path = await _save_upload_async(file)
    task = migrate_csv_task.apply_async(
        args=[str(temp_path), normalized],
        kwargs={"force": force},
        queue="uploads",
    )

    return {
        "status": "queued",
        "job_id": task.id,
        "file": file.filename,
    }


@router.get("/data/upload/{job_id}", tags=["data"])
async def check_upload_status(job_id: str):
    """Check the status of a previously queued upload job."""
    task = celery_app.AsyncResult(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="Upload job not found")

    payload = {"job_id": job_id, "status": task.status}
    if task.status == "FAILURE":
        payload["error"] = str(task.result)
    elif task.successful():
        payload["result"] = task.result
    if task.info:
        payload["meta"] = task.info
    return payload
