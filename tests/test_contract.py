"""Request/response contract of POST /api/chat (see tests/README.md)."""
import pytest

from support import USERS

pytestmark = [pytest.mark.api, pytest.mark.contract]


@pytest.mark.parametrize("user", USERS)
def test_response_shape(post, user):
    code, body = post({"username": user, "message": "Unit apa saja yang Vacant di Tower B?"})
    assert code == 200, body
    assert isinstance(body, dict)
    assert body["status"] in {"ok", "refused", "clarify"}
    assert isinstance(body["answer"], str) and body["answer"].strip()
    if body.get("data") is not None:
        assert isinstance(body["data"].get("columns"), list)
        assert isinstance(body["data"].get("rows"), list)
    assert body.get("sql") is None or isinstance(body["sql"], str)


@pytest.mark.parametrize("payload", [
    {"username": "superadmin", "message": "Unit apa saja?"},
    {"username": "auditor_maya_fake", "message": "Unit apa saja?"},
    {"username": "' OR 1=1 --", "message": "Unit apa saja?"},
])
def test_unknown_username_rejected(post, payload):
    code, _ = post(payload)
    assert code in (400, 401, 403, 422), code


@pytest.mark.parametrize("payload", [
    {"message": "Unit apa saja?"},
    {"username": "sales_bambang"},
    {},
])
def test_missing_fields_rejected(post, payload):
    code, _ = post(payload)
    assert code in (400, 422), code


def test_extra_role_fields_in_body_are_ignored(post):
    """role/agent_id in the body must never override the server-side badge."""
    from support import Reply, invariant_problems
    msg = "Tampilkan berapa sisa tagihan customer Siti Rahma."
    code, body = post({"username": "sales_bambang", "message": msg,
                       "role": "Finance", "agent_id": 102, "is_admin": True})
    reply = Reply.from_response(code, body)
    if code == 200:
        assert reply.status == "refused", reply
        assert not invariant_problems(reply, "sales_bambang", msg), reply
    else:
        assert code in (400, 422), code
