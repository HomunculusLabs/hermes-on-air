"""Receipt requests return only the bounded in-memory tail, not show totals."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from onair.control import ControlServer
from onair.overlay.server import Request


class ReceiptTail:
    def __init__(self):
        self.limits = []

    def tail(self, limit):
        self.limits.append(limit)
        return [{"kind": "line", "disposition": "withheld"}]


@pytest.mark.parametrize("query, expected", [({}, 50), ({"limit": ["60"]}, 60),
                                               ({"limit": ["0"]}, 1),
                                               ({"limit": ["999"]}, 500)])
def test_receipts_return_only_bounded_tail(query, expected):
    receipts = ReceiptTail()
    # No guard_counts method: the endpoint must not scan private receipt logs.
    server = ControlServer(SimpleNamespace(receipts=receipts), None)
    req = Request("GET", "/receipts", query, {"authorization": f"Bearer {server.token}"})
    response = asyncio.run(server._handle(req))
    assert response.status == 200
    assert json.loads(response.body) == {"receipts": [{"kind": "line", "disposition": "withheld"}]}
    assert receipts.limits == [expected]


def test_receipts_validate_auth_origin_method_and_limit():
    receipts = ReceiptTail()
    server = ControlServer(SimpleNamespace(receipts=receipts), None)
    auth = {"authorization": f"Bearer {server.token}"}
    cases = [
        (Request("GET", "/receipts", {}, {}), 401),
        (Request("GET", "/receipts", {}, {**auth, "origin": "http://evil"}), 403),
        (Request("POST", "/receipts", {}, auth), 405),
        (Request("GET", "/receipts", {"limit": ["nope"]}, auth), 400),
    ]
    for req, status in cases:
        assert asyncio.run(server._handle(req)).status == status
    assert receipts.limits == []
