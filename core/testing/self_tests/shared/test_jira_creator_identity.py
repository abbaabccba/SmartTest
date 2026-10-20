from core.ai import AIConfigurationError
from core.domain.detail import DetailSection
from core.domain.values import NamedValue, PersonRef
from core.jira.audit.models import JiraAuditScope
from core.jira.audit.rules import creator_names
from core.jira.audit.use_case import JiraAuditUseCase
from core.jira.domain import Issue, IssueIdentity, JiraProjectRef, RichText


def test_audit_result_retains_actual_creator_identity_and_account_through_ai_processing():
    display = next(iter(creator_names()))
    issue = Issue(IssueIdentity('id', 'SH-1', 'https://jira/SH-1'), '', JiraProjectRef('SH'), NamedValue('open', 'Open'), NamedValue('bug', 'Bug'),
                  creator=PersonRef('stable-user-key', 'actual.account', display),
                  description=DetailSection.loaded(RichText('invalid description')))

    class Source:
        def list_issues(self, scope, cancellation): return (issue,)
        def load_details(self, value, details): return value

    def no_external_ai(_model):
        raise AIConfigurationError('not configured')

    report = JiraAuditUseCase(Source(), ai_client_factory=no_external_ai).run(JiraAuditScope('jql', '', 'project=SH'))
    assert len(report.issues) == 1
    result = report.issues[0]
    assert result.creator == display
    assert result.creator_identity == 'stable-user-key'
    assert result.creator_account == 'actual.account'
