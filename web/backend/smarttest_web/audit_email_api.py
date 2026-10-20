from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from core.weekly_audit import fixed_weekly_audit_scope

from .audit_http import confluence_access


def create_router(authenticated_session, email_history, email_job, sessions, cache_database, facts,
                  jira_audit_owner, confluence_audit_owner) -> APIRouter:
    router = APIRouter()

    def access_context(request):
        return confluence_access(sessions, cache_database, request)

    @router.get("/api/audit-email/runs")
    def list_audit_email_runs(offset: int = Query(0, ge=0), value=Depends(authenticated_session)):
        return email_history.list_runs(value.username, offset)

    @router.get("/api/audit-email/history/{kind}")
    def browse_audit_email_history(kind: Literal['jira', 'confluence'],
                                  offset: int = Query(0, ge=0),
                                  start_date: date | None = None, end_date: date | None = None,
                                  value=Depends(authenticated_session)):
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=422, detail='开始日期不能晚于结束日期')
        return email_history.browse(value.username, kind, offset, start_date, end_date)

    @router.get("/api/audit-email/runs/{run_id}")
    def get_audit_email_run(run_id: str, value=Depends(authenticated_session)):
        try:
            return email_history.get(value.username, run_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Report not found") from error

    @router.post("/api/audit-email/runs")
    def create_audit_email_run(request: Request, value=Depends(authenticated_session)):
        return email_job.trigger(value.username, fixed_weekly_audit_scope(datetime.now().astimezone()), access_context(request),
                                 value.password, value.expires_at, facts, jira_audit_owner, confluence_audit_owner,
                                 trigger_source='manual')

    @router.get("/api/audit-email/runs/{run_id}/attachments/{kind}/{filename}")
    def audit_email_attachment(run_id: str, kind: str, filename: str, value=Depends(authenticated_session)):
        try:
            result = email_history.get(value.username, run_id)
            if filename not in result['reports'].get(kind, {}).get('attachments', []):
                raise LookupError(filename)
            path = email_job.root / result['id'] / kind / filename
            if not path.is_file():
                raise LookupError(filename)
        except LookupError as error:
            raise HTTPException(status_code=404, detail='Attachment not found') from error
        return FileResponse(path, filename=filename,
                            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    return router
