import pytest

from smarttest_web.test_suite_repository import (
    NameConflictError,
    RevisionConflictError,
    TestSuiteRepository,
)


def test_suite_repository_persists_visibility_order_and_permissions(
    tmp_path, *, database_factory
):
    path = tmp_path / "web.db"
    repo = TestSuiteRepository(database_factory(), now=lambda: 10.0)
    own = repo.create(owner_username="coco", owner_display_name="Coco", name=" IPTV ",
                      description="smoke", visibility="private",
                      ordered_nodeids=["b", "a", "b"])
    shared = repo.create(owner_username="atlas", owner_display_name="Atlas", name="Shared",
                         description="", visibility="shared", ordered_nodeids=["c"])
    assert own.name == "IPTV" and own.ordered_nodeids == ("b", "a")
    reopened = TestSuiteRepository(database_factory(), now=lambda: 20.0)
    assert [row.id for row in reopened.list_mine("coco")] == [own.id]
    assert [row.id for row in reopened.list_shared("coco")] == [shared.id]
    assert reopened.get_visible(own.id, "atlas") is None
    assert reopened.update(own.id, owner_username="atlas", revision=1, name="x",
                           description="", visibility="private", ordered_nodeids=["a"]) is None
    assert not reopened.delete(own.id, owner_username="atlas")


def test_suite_repository_conflicts_revision_and_copy(tmp_path, *, database_factory):
    repo = TestSuiteRepository(database_factory(), now=lambda: 10.0)
    source = repo.create(owner_username="coco", owner_display_name="Coco", name="Suite",
                         description="d", visibility="shared", ordered_nodeids=["a", "b"])
    with pytest.raises(NameConflictError):
        repo.create(owner_username="coco", owner_display_name="Coco", name="Suite",
                    description="", visibility="private", ordered_nodeids=["x"])
    with pytest.raises(RevisionConflictError):
        repo.update(source.id, owner_username="coco", revision=2, name="Suite",
                    description="", visibility="shared", ordered_nodeids=["a"])
    copied = repo.copy(source.id, reader_username="atlas", owner_display_name="Atlas",
                       name="Copy")
    assert copied and copied.owner_username == "atlas" and copied.visibility == "private"
    assert copied.ordered_nodeids == source.ordered_nodeids
