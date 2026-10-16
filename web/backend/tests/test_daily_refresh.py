from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
from smarttest_web.daily_refresh import DailyRefreshClock, latest_slot

def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


def test_beijing_five_slot_and_restart_claim(tmp_path, *, database_factory):
    now=[stamp('2026-10-15T04:59:59')]
    assert latest_slot(now[0])==stamp('2026-10-14T05:00:00')
    now[0]+=1
    assert latest_slot(now[0])==stamp('2026-10-15T05:00:00')
    clock = DailyRefreshClock(database_factory(), now=lambda: now[0])
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(lambda _:clock.claim(['alice']),range(2)))
    assert sum(len(x) for x in result)==1
    clock.recover()
    assert clock.claim(['alice'])==[]
    now[0]=stamp('2026-10-18T08:00:00')
    assert clock.claim(['alice'])==['alice']
    assert clock.claim(['alice'])==[]
    clock.finish('alice','remote_unavailable')
    assert clock.claim(['alice'])==[]
    assert clock.claim(['bob'])==['bob']


def test_forced_confluence_sync_propagates_failure():
 from smarttest_web.project_facts_api import ProjectFactsWebOwner
 owner=object.__new__(ProjectFactsWebOwner)
 observed=[]
 owner._acquisition_scope=lambda access,filters,force=False: observed.append(force) or ('one',)
 owner._service=lambda *args:object()
 class Coordinator:
  def sync(self,*args,**kwargs):return ['failed']
 owner._sync_coordinator_factory=lambda service:Coordinator()
 import pytest
 with pytest.raises(RuntimeError,match='confluence_details_failed'):
  owner.sync_details(object(),'secret',force=True)
 assert observed==[True]

def test_jira_global_and_submitted_namespaces_and_empty_dedup(monkeypatch):
    from types import SimpleNamespace
    from smarttest_web.daily_refresh import DailyFactsRefresh, REFRESH_ITEMS
    from core.async_tasks import CancellationToken
    from smarttest_web.jira import analytics_service, analytics_refresh
    calls=[];metrics=[]
    class Service:
        def search(self,*args,**kwargs):
            calls.append((self.card,self.storage,args[3]['jql']))
            return {'validation':{'valid':True}}
    def factory(*args,storage_key=None):
        service=Service();service.card=args[-1];service.storage=storage_key;return service
    monkeypatch.setattr(analytics_service,'card_analytics_service',factory)
    monkeypatch.setattr(analytics_refresh,'refresh_metric',lambda *args:metrics.append((args[4],args[5])))
    owner=object.__new__(DailyFactsRefresh)
    owner.repository=SimpleNamespace(history_scopes=lambda account:{'self-test':'project=A','task':''})
    owner.gateway_factory=lambda *args:(object(),object());owner.tasks=SimpleNamespace(pages=object())
    owner._jira(REFRESH_ITEMS[0],'alice','secret','session',CancellationToken(),lambda *_:None)
    assert calls==[('self-test','daily:self-test',''),('self-test','self-test','project=A'),
                ('task','task',''),('customer-feedback','daily:customer-feedback','')]
    assert len(metrics)==8
    assert len({q for card,q in metrics if card=='self-test'})==4


def test_registered_handler_runs_and_session_is_revoked(
    monkeypatch, tmp_path, *, database_factory
):
    from types import SimpleNamespace
    from core.async_tasks import CancellationToken
    from smarttest_web import daily_refresh
    events=[]
    owner=object.__new__(daily_refresh.DailyFactsRefresh)
    owner.clock = daily_refresh.DailyRefreshClock(database_factory())
    owner.clock.claim(['alice'])
    owner.sessions=SimpleNamespace(saved_credentials=lambda account:(account,'secret'),
   create_from_saved=lambda account:'internal',delete=lambda token:events.append(('revoke',token)))
    owner.handlers={'new':lambda item,*args:events.append(('handler',item['scope']))}
    monkeypatch.setattr(daily_refresh,'REFRESH_ITEMS',({'handler':'new','scope':'approved'},))
    owner.refresh_account('alice',CancellationToken(),lambda *_:None)
    assert events==[('handler','approved'),('revoke','internal')]
    assert owner.clock.claim(['alice'])==[]


def test_manual_permission_matches_registered_metrics():
    from smarttest_web.daily_refresh import manual_metric_allowed
    assert manual_metric_allowed('self-test','comment')
    assert not manual_metric_allowed('self-test','verify')


def test_scheduled_begin_does_not_replace_user_pending_or_new_scope(
    tmp_path, *, database_factory
):
    from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
    from core.async_tasks import TaskCancelled
    import pytest
    repo = JiraAnalyticsRepository(database_factory())
    repo.register_history_scope('alice','self-test','project=A')
    pending=repo.begin('user','alice','query',{},'',expires_at=100,card_key='self-test',user_jql='project=A')
    with pytest.raises(TaskCancelled,match='query_in_progress'):
        repo.begin('','alice','query',{},'',expires_at=0,card_key='self-test',expected_user_jql='project=A')
    repo.finish(pending,'failed')
    repo.register_history_scope('alice','self-test','project=B')
    with pytest.raises(TaskCancelled,match='scope_changed'):
        repo.begin('','alice','query',{},'',expires_at=0,card_key='self-test',expected_user_jql='project=A')


def test_confluence_manual_flag_is_enforced(monkeypatch):
 from smarttest_web import daily_refresh
 from smarttest_web.project_facts_api import ProjectFactsWebOwner
 import pytest
 monkeypatch.setattr(daily_refresh,'REFRESH_ITEMS',({'handler':'confluence','manual_allowed':False},))
 owner=object.__new__(ProjectFactsWebOwner)
 with pytest.raises(PermissionError,match='manual_refresh_disabled'):
  owner.refresh(object(),'secret')
