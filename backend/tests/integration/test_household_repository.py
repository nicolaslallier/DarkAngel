"""Integration — HouseholdRepository against real PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from app.repositories.households import AlreadyMember, HouseholdRepository, InvitationInvalid


@pytest.fixture
def households(db):
    return HouseholdRepository(db)


def test_create_makes_the_creator_the_owner(households):
    household = households.create("alice", "Maison")

    member = households.membership("alice")

    assert (member.household_id, member.role) == (household.id, "owner")


def test_a_person_belongs_to_one_household(households):
    households.create("alice", "Maison")

    with pytest.raises(AlreadyMember):
        households.create("alice", "Chalet")


def test_an_invitation_is_single_use(households):
    household = households.create("alice", "Maison")
    token = households.invite(household.id, "member", "alice")

    member = households.redeem(token, "bob")

    assert (member.household_id, member.role) == (household.id, "member")
    with pytest.raises(InvitationInvalid):
        households.redeem(token, "carol")


def test_an_expired_invitation_is_refused(households):
    household = households.create("alice", "Maison")
    token = households.invite(household.id, "member", "alice", ttl=timedelta(seconds=-1))

    with pytest.raises(InvitationInvalid):
        households.redeem(token, "bob")


def test_an_unknown_token_is_refused(households):
    with pytest.raises(InvitationInvalid):
        households.redeem("nope", "bob")


def test_redeeming_as_an_existing_member_keeps_the_link_usable(households):
    household = households.create("alice", "Maison")
    households.create("dave", "Chalet")
    token = households.invite(household.id, "viewer", "alice")

    with pytest.raises(AlreadyMember):
        households.redeem(token, "dave")

    assert households.redeem(token, "bob").role == "viewer"


def test_the_database_keeps_only_the_hash(households, db):
    household = households.create("alice", "Maison")
    token = households.invite(household.id, "member", "alice")

    stored = db.execute(text("SELECT token_hash FROM household_invitations")).scalars().all()

    assert len(stored) == 1 and len(stored[0]) == 64 and token not in stored[0]


def test_roles_and_removal_are_scoped_to_the_household(households):
    mine = households.create("alice", "Maison")
    theirs = households.create("dave", "Chalet")
    households.redeem(households.invite(mine.id, "member", "alice"), "bob")

    assert households.set_role(theirs.id, "bob", "viewer") is None
    assert households.remove(theirs.id, "bob") is False
    assert households.set_role(mine.id, "bob", "viewer").role == "viewer"
    assert households.remove(mine.id, "bob") is True
    assert households.membership("bob") is None


def test_deleting_a_household_removes_its_members_and_invitations(households, db):
    household = households.create("alice", "Maison")
    households.invite(household.id, "member", "alice")

    households.delete(household.id)

    assert households.membership("alice") is None
    assert db.execute(text("SELECT count(*) FROM household_invitations")).scalar() == 0


def test_a_member_who_left_meanwhile_is_not_written_through_a_stale_row(households, db):
    mine = households.create("alice", "Maison")
    households.redeem(households.invite(mine.id, "member", "alice"), "bob")
    households.membership("bob")  # bob is now in the session's identity map
    db.execute(text("DELETE FROM household_members WHERE sub = 'bob'"))  # left elsewhere

    assert households.set_role(mine.id, "bob", "viewer") is None
    assert households.remove(mine.id, "bob") is False
    assert db.execute(text("SELECT count(*) FROM household_members")).scalar() == 1


def test_the_display_name_is_stored_on_create_and_on_join(households):
    mine = households.create("alice", "Maison", "alice")
    households.redeem(households.invite(mine.id, "member", "alice"), "bob", "bob@example.com")

    names = {m.sub: m.display_name for m in households.members(mine.id)}

    assert names == {"alice": "alice", "bob": "bob@example.com"}
