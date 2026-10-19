import datetime
import html
import json
import os
import re
import urllib.request

MANAGER_URL = os.environ.get("MANAGER_URL", "http://localhost:8081")
MANAGER_TOKEN = os.environ.get("MANAGER_TOKEN", "")
DAYS = 3
SUFFIX = "-daily"
HEADING = re.compile(r"^# \S+ — (.+)$", re.M)
HELP = re.compile(r"^## Нужна помощь\n+((?:- .*\n?)*)", re.M)
BLOCKS = [
    (re.compile(r"<h1[^>]*>(.*?)</h1>", re.S), "\n# \\1\n"),
    (re.compile(r"<h2[^>]*>(.*?)</h2>", re.S), "\n## \\1\n"),
    (re.compile(r"<li[^>]*>(.*?)</li>", re.S), "- \\1\n"),
    (re.compile(r"<p[^>]*>(.*?)</p>", re.S), "\\1\n"),
]
TAG = re.compile(r"<[^>]+>")


def post(base, path, token, body, accept="application/json"):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": accept,
            "Authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def graphql(base, token, query, variables=None):
    answer = post(base, "/_system/graphql", token, {"query": query, "variables": variables or {}})
    if answer.get("errors"):
        raise RuntimeError("%s: %s" % (base, answer["errors"]))
    return answer["data"]


def write_notes(base, token, notes):
    data = graphql(
        base,
        token,
        "mutation($i: UpdateNotesInput!) { updateNotes(input: $i) { __typename"
        " ... on UpdateNotesSuccessPayload { paths } ... on ErrorPayload { message } } }",
        {"i": {"changes": [{"upsert": {"path": path, "content": content}} for path, content in notes.items()]}},
    )["updateNotes"]
    if data["__typename"] != "UpdateNotesSuccessPayload":
        raise RuntimeError("%s refused the notes: %s" % (base, data))
    return data["paths"]


def sources():
    rows = graphql(MANAGER_URL, MANAGER_TOKEN, "{ admin { federationSecrets { kid kbUrl revokedAt } } }")
    live = [r["kid"] for r in rows["admin"]["federationSecrets"]
            if r.get("kbUrl") and not r.get("revokedAt") and r["kid"].endswith(SUFFIX)]
    return [(kid, kid[: -len(SUFFIX)]) for kid in sorted(live)]


def federated_note_html(kb_id, path):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "federated_note_html", "arguments": {"kb_id": kb_id, "path": path}}}
    answer = post(MANAGER_URL, "/_system/mcp", MANAGER_TOKEN, body, "application/json, text/event-stream")
    result = answer.get("result") or {}
    text = "".join(p.get("text", "") for p in result.get("content") or [] if p.get("type") == "text")
    if "error" in answer or result.get("isError") or not text.lstrip().startswith("<"):
        return None
    return text


def markdown(text):
    for pattern, replacement in BLOCKS:
        text = pattern.sub(replacement, text)
    text = html.unescape(TAG.sub("", text))
    text = re.sub(r"\n\s*\n(?=- )", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def copy_of(alias, day, page):
    body = re.sub(r"^## [^\n]+\n+(?=## |\Z)", "", markdown(page), flags=re.M).strip() + "\n"
    found = HEADING.search(body)
    person = found.group(1).strip() if found else alias
    asks = HELP.search(body)
    items = [line[2:].strip() for line in (asks.group(1).splitlines() if asks else [])]
    needs_help = any(item and item.rstrip(".").lower() not in ("—", "нет") for item in items)
    return "---\ndate: %s\nalias: %s\nperson: %s\nhelp: %s\n---\n\n%s" % (
        day, alias, person, "true" if needs_help else "false", body)


def dashboard(aliases, dates):
    return "\n".join([
        "---",
        "title: Команда — последние дни",
        "layout: team",
        "free: true",
        "people: [%s]" % ", ".join(aliases),
        "dates: [%s]" % ", ".join('"%s"' % d for d in dates),
        "---",
        "",
        "Страница собирается из копий статусов дня в `team/`. Править здесь нечего.",
        "",
    ])


def main():
    today = datetime.date.today()
    dates = [(today - datetime.timedelta(days=i)).isoformat() for i in range(DAYS)]
    team = sources()
    notes = {"dashboards/team.md": dashboard([alias for _, alias in team], dates)}
    for kb_id, alias in team:
        for day in dates:
            page = federated_note_html(kb_id, "daily-sync/%s.md" % day)
            if page is None:
                print("%s %s: no status" % (alias, day))
                continue
            notes["team/%s/%s.md" % (alias, day)] = copy_of(alias, day, page)
    for path in write_notes(MANAGER_URL, MANAGER_TOKEN, notes):
        print("wrote " + path)


if __name__ == "__main__":
    main()
