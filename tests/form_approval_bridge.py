"""Synthetic React -> actual ASGI test bridge; no external sockets, DB or AI."""

import base64
from copy import deepcopy
import json
import socket
import sys

from tests.test_demo_api import Adapter, setup
from tests.test_excel_export import read_workbook
from tests.test_form_approval import clean_result


def deny_network(*args, **kwargs):
    raise RuntimeError("NETWORK_DISABLED_FOR_OFFLINE_FORM_TEST")


def main():
    original_connect = socket.socket.connect
    def local_connect(self, address):
        # Windows asyncio's internal socketpair may use a loopback TCP socket.
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            return deny_network()
        return original_connect(self, address)
    socket.socket.connect = local_connect
    socket.create_connection = deny_network
    result = clean_result()
    second = deepcopy(result.logical_forms[0])
    second.form_id = "form-2"
    second.fields.israeli_id.values = []
    second.fields.israeli_id.value_status = "missing"
    second.fields.sample_receipt_date.notes = "Synthetic uncertainty: inspect original"
    result.logical_forms.append(second)
    client, workflow, provider = setup(Adapter(result))
    with client:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                if request.get("op") == "stats":
                    payload = {"provider_mock_calls": len(provider.calls), "external_calls": 0,
                               "database_calls": 0}
                elif request.get("op") == "inspect_workbook":
                    book = read_workbook(base64.b64decode(request["content"]))
                    payload = {name: rows for name, (_, rows) in book.items()}
                else:
                    options = {}
                    if "upload" in request:
                        options = {"files": {"file": ("synthetic.pdf", base64.b64decode(request["upload"]), "application/pdf")},
                                   "data": {"allow_external_processing": "true"}}
                    elif "body" in request:
                        options = {"content": request["body"], "headers": {"Content-Type": "application/json"}}
                    response = client.request(request.get("method", "GET"), request["path"], **options)
                    payload = {"status": response.status_code, "headers": dict(response.headers),
                               "content": base64.b64encode(response.content).decode("ascii")}
                print(json.dumps({"id": request["id"], "payload": payload}), flush=True)
            except Exception:
                print(json.dumps({"id": request["id"], "error": "OFFLINE_BRIDGE_FAILURE"}), flush=True)


if __name__ == "__main__":
    main()
