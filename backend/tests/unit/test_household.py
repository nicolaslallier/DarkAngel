"""Unit — the household routes, against FakeHouseholdRepository."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def invite(role="member", sub="user-1"):
    return client.post("/api/household/invitations", headers=auth(sub), json={"role": role})


def test_a_person_without_a_household_gets_409(ledger):
    response = client.get("/api/household", headers=auth("newcomer"))

    assert response.status_code == 409
    assert response.json()["detail"] == "no_household"


def test_create_makes_the_caller_the_owner(ledger):
    response = client.post("/api/household", headers=auth("alice"), json={"name": "Maison"})

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["role"]) == ("Maison", "owner")
    assert body["members"] == [{"sub": "alice", "role": "owner"}]


def test_a_second_create_is_a_409(home):
    response = client.post("/api/household", headers=auth(), json={"name": "Chalet"})

    assert response.status_code == 409


def test_get_lists_the_members_and_the_callers_role(home):
    home.ledger.households.add_member(home.id, "user-2", "viewer")

    body = client.get("/api/household", headers=auth("user-2")).json()

    assert body["role"] == "viewer"
    assert {m["sub"] for m in body["members"]} == {"user-1", "user-2"}


def test_an_invitation_lets_someone_join_with_the_offered_role(home):
    link = invite("viewer").json()
    assert link["expires_in_days"] == 7

    joined = client.post("/api/household/join", headers=auth("bob"), json={"token": link["token"]})

    assert joined.status_code == 200
    assert joined.json()["role"] == "viewer"


def test_a_token_works_once(home):
    link = invite().json()
    client.post("/api/household/join", headers=auth("bob"), json={"token": link["token"]})

    again = client.post("/api/household/join", headers=auth("carol"), json={"token": link["token"]})

    assert again.status_code == 404


def test_joining_while_already_in_a_household_is_409_and_keeps_the_link(home):
    home.ledger.households.create("dave", "Chalet")
    link = invite().json()

    refused = client.post(
        "/api/household/join", headers=auth("dave"), json={"token": link["token"]}
    )
    ok = client.post("/api/household/join", headers=auth("bob"), json={"token": link["token"]})

    assert (refused.status_code, ok.status_code) == (409, 200)


def test_only_the_owner_manages_the_household(home):
    households = home.ledger.households
    households.add_member(home.id, "member", "member")
    households.add_member(home.id, "viewer", "viewer")

    for sub in ("member", "viewer"):
        assert invite(sub=sub).status_code == 403
        assert client.delete("/api/household", headers=auth(sub)).status_code == 403
        assert (
            client.patch(
                "/api/household/members/viewer", headers=auth(sub), json={"role": "member"}
            ).status_code
            == 403
        )
        assert client.delete("/api/household/members/viewer", headers=auth(sub)).status_code == 403


def test_the_owner_changes_a_role_and_removes_a_member(home):
    home.ledger.households.add_member(home.id, "bob", "member")

    changed = client.patch("/api/household/members/bob", headers=auth(), json={"role": "viewer"})
    removed = client.delete("/api/household/members/bob", headers=auth())

    assert (changed.status_code, changed.json()) == (200, {"sub": "bob", "role": "viewer"})
    assert removed.status_code == 204
    assert client.get("/api/household", headers=auth("bob")).status_code == 409


def test_the_owner_cannot_be_demoted_or_removed(home):
    assert (
        client.patch(
            "/api/household/members/user-1", headers=auth(), json={"role": "viewer"}
        ).status_code
        == 409
    )
    assert client.delete("/api/household/members/user-1", headers=auth()).status_code == 409


def test_an_unknown_member_is_a_404(home):
    assert client.delete("/api/household/members/ghost", headers=auth()).status_code == 404
    assert (
        client.patch(
            "/api/household/members/ghost", headers=auth(), json={"role": "member"}
        ).status_code
        == 404
    )


def test_a_member_can_leave_but_the_owner_cannot(home):
    home.ledger.households.add_member(home.id, "bob", "member")

    left = client.post("/api/household/leave", headers=auth("bob"))
    stuck = client.post("/api/household/leave", headers=auth())

    assert (left.status_code, stuck.status_code) == (204, 409)


def test_the_owner_deletes_the_household(home):
    home.ledger.households.add_member(home.id, "bob", "member")

    assert client.delete("/api/household", headers=auth()).status_code == 204
    assert client.get("/api/household", headers=auth("bob")).status_code == 409
