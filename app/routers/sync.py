from fastapi import APIRouter, HTTPException

from services import suggestion_service, sync_service

router = APIRouter(prefix="/sync", tags=["sync"])

_last_run: dict = {}


@router.post("/calendar")
def sync_calendar() -> dict:
    try:
        result = sync_service.run_sync()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _last_run["calendar"] = result
    return result


@router.post("/telegram")
def sync_telegram() -> dict:
    try:
        ingest_result = suggestion_service.ingest_telegram()
        review_result = suggestion_service.process_reviewed()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result = {"ingest": ingest_result, "review": review_result}
    _last_run["telegram"] = result
    return result


@router.post("/gmail")
def sync_gmail() -> dict:
    try:
        ingest_result = suggestion_service.ingest_gmail()
        review_result = suggestion_service.process_reviewed()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    result = {"ingest": ingest_result, "review": review_result}
    _last_run["gmail"] = result
    return result


@router.get("/status")
def sync_status() -> dict:
    if not _last_run:
        return {"status": "no sync has run yet"}
    return _last_run
