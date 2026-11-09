"""One persisted usage/timing projection for public runs, SSE and management."""
from datetime import datetime
from sqlalchemy import select
from app.errors import AppError
from .models import InvocationUsage,InvocationReservation,CallTiming
from .schemas import UsageView,TimingView
from .storage import fresh,load
from .values import bounded_reported_usage

SOURCES={'provider_reported','no_model_calls','persisted_ledger','unavailable','pending'}


def project_usage(value,*,reserved_tokens=None,source=None):
    if isinstance(value,dict):
        if reserved_tokens is None:reserved_tokens=value.get('reserved_tokens')
        source=source or value.get('source')
    reported=bounded_reported_usage(value)
    status='reserved' if isinstance(value,dict) and value.get('status')=='reserved' else reported['status']
    source=source if source in SOURCES else 'pending' if status=='reserved' else 'unavailable' if status=='unknown' else 'persisted_ledger'
    if type(reserved_tokens) is not int or reserved_tokens<0:reserved_tokens=None
    try:
        return UsageView(status=status,tokens=reported['tokens'],reserved_tokens=reserved_tokens,source=source).model_dump(mode='json',exclude_none=True)|{'reserved_tokens':reserved_tokens}
    except ValueError:raise AppError('APP_LEDGER_INVALID','调用用量来源或数字记录不一致',503) from None


def usage_for_run(session,run_id):
    row=fresh(session,InvocationUsage,run_id);timing=fresh(session,CallTiming,run_id)
    if row is not None:
        status='known' if row.status=='actual' else 'partial' if row.status=='partial' else 'unknown'
        return project_usage({'status':status,'tokens':load(row.tokens)},reserved_tokens=row.reserved_tokens,
            source=timing.usage_source if timing is not None else None)
    reservation=session.scalar(select(InvocationReservation).where(InvocationReservation.run_id==run_id))
    if reservation is not None and reservation.settled:
        # Missing settlement evidence is not proof that its initial reservation
        # is still charged. Do not reconstruct actual or reserved from labels.
        return project_usage(None,reserved_tokens=None)
    status='reserved' if reservation is not None and reservation.status not in {'outcome_unknown','interrupted'} else 'unknown'
    return project_usage({'status':status},reserved_tokens=reservation.reserved_tokens if reservation else None)


def timing_for_run(session,run_id):
    row=fresh(session,CallTiming,run_id)
    return TimingView(status=row.timing_status if row else 'unknown',started_at=row.admitted_at if row else None,
        finished_at=row.finished_at if row else None,elapsed_ms=row.elapsed_ms if row else None).model_dump(mode='json')


def finish_timing(row,finished_at):
    if row is None or row.finished_at is not None:return
    row.finished_at=finished_at
    try:
        start=datetime.fromisoformat(row.admitted_at);end=datetime.fromisoformat(finished_at)
        if start.tzinfo is None or end.tzinfo is None:raise ValueError()
        delta=end-start
        elapsed=(delta.days*86400+delta.seconds)*1000+delta.microseconds//1000
        if elapsed<0:raise ValueError()
    except (ValueError,TypeError,OverflowError):row.timing_status='unknown';row.elapsed_ms=None
    else:row.timing_status='known';row.elapsed_ms=elapsed
