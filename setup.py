import datetime
import os
import pathlib
import time

import sync

SEED = pathlib.Path(__file__).parent / "seed"
LAYOUT = pathlib.Path(__file__).parent / "layout" / "team.html"
SUBGRAPH = "daily_sync"
EMPLOYEES = {alias: os.environ[alias.upper() + "_TOKEN"] for alias in ("masha", "petya")}


def employee_url(alias):
    return "http://%s:8080" % alias


def seed(alias, token):
    today = datetime.date.today()
    notes = {"daily-sync/_subgraph_patch.md": (SEED / "_subgraph_patch.md").read_text(encoding="utf-8")}
    for days_ago in range(sync.DAYS):
        day = (today - datetime.timedelta(days=days_ago)).isoformat()
        text = (SEED / alias / ("%d.md" % days_ago)).read_text(encoding="utf-8")
        notes["daily-sync/%s.md" % day] = text.replace("{date}", day)
    sync.write_notes(employee_url(alias), token, notes)


def subgraph_id(alias, token):
    for _ in range(30):
        data = sync.graphql(employee_url(alias), token, "{ admin { allSubgraphs { nodes { id name } } } }")
        found = [n["id"] for n in data["admin"]["allSubgraphs"]["nodes"] if n["name"] == SUBGRAPH]
        if found:
            return found[0]
        time.sleep(1)
    raise RuntimeError("%s never created subgraph %s" % (alias, SUBGRAPH))


def admin_call(base, token, mutation, payload_type, variables):
    data = sync.graphql(base, token, mutation, variables)["admin"]
    payload = next(iter(data.values()))
    if payload["__typename"] != payload_type:
        raise RuntimeError("%s: %s" % (base, payload))
    return payload


def share(alias, token, kid):
    minted = admin_call(
        employee_url(alias), token,
        "mutation($i: CreateInboundFederationSecretInput!) { admin { createInboundFederationSecret(input: $i) {"
        " __typename ... on CreateInboundFederationSecretPayload { kid secretHex key } ... on ErrorPayload { message } } } }",
        "CreateInboundFederationSecretPayload",
        {"i": {"kid": kid, "description": "status of the day for the manager"}},
    )
    admin_call(
        employee_url(alias), token,
        "mutation($i: AddFederationSecretSubgraphInput!) { admin { addFederationSecretSubgraph(input: $i) {"
        " __typename ... on AddFederationSecretSubgraphPayload { success } ... on ErrorPayload { message } } } }",
        "AddFederationSecretSubgraphPayload",
        {"i": {"kid": kid, "subgraphID": subgraph_id(alias, token)}},
    )
    return minted["secretHex"]


def connect(alias, kid, secret_hex):
    kb_url = employee_url(alias) + "/_system/mcp"
    admin_call(
        sync.MANAGER_URL, sync.MANAGER_TOKEN,
        "mutation($i: CreateOutboundFederationSecretInput!) { admin { createOutboundFederationSecret(input: $i) {"
        " __typename ... on CreateOutboundFederationSecretPayload { kid } ... on ErrorPayload { message } } } }",
        "CreateOutboundFederationSecretPayload",
        {"i": {"kid": kid, "secretHex": secret_hex, "kbURL": kb_url, "description": alias + "'s status of the day"}},
    )
    kb_note = "---\nmcp_federation_kb_url: %s\nmcp_federation_kb_id: %s\n---\n\nUse when: %s's status of the day.\n" % (
        kb_url, kid, alias)
    sync.write_notes(sync.MANAGER_URL, sync.MANAGER_TOKEN, {"federation/%s.md" % kid: kb_note})


def main():
    connected = {kid for kid, _ in sync.sources()}
    for alias, token in EMPLOYEES.items():
        seed(alias, token)
        kid = alias + sync.SUFFIX
        if kid in connected:
            print("%s: already connected" % kid)
            continue
        connect(alias, kid, share(alias, token, kid))
        print("%s: shared %s and connected to the manager" % (kid, SUBGRAPH))
    sync.write_notes(sync.MANAGER_URL, sync.MANAGER_TOKEN, {"_layouts/team.html": LAYOUT.read_text(encoding="utf-8")})
    sync.main()


if __name__ == "__main__":
    main()
