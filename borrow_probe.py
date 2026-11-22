import json

from portal.records import load_records
from portal.render import Portal
from portal.view import edit_filter, read_filter
from portal.wire import CaptureLinkLost, ScriptedLink


ACTOR = "capture/probe/export"


def interrupted(link, field, execute):
    try:
        link.send_edit("borrow", field, execute)
    except CaptureLinkLost:
        return
    raise AssertionError("the scripted cut did not fire")


def main():
    records = load_records("samples/records.csv")
    observed = {}
    for mode in ("drop_reply", "hold"):
        portal = Portal("river-gate", records, "east", 1)
        link = ScriptedLink()
        link.arm("borrow", "region", mode)
        interrupted(link, "region", lambda: edit_filter(portal.view, "region", "west", ACTOR, 0))
        if mode == "hold":
            assert read_filter(portal.view, "region").version == 0
            edit_filter(portal.view, "region", "west", "operator")
        region = read_filter(portal.view, "region")
        assert (region.value, region.version, region.actor) == (
            "west", 1, ACTOR if mode == "drop_reply" else "operator",
        )
        observed[mode] = [region.value, region.version, region.actor]
        portal.close()

    portal = Portal("river-gate", records, "east", 1)
    link = ScriptedLink()
    edit_filter(portal.view, "region", "west", ACTOR, 0)
    link.arm("borrow", "quarter", "hold")
    interrupted(link, "quarter", lambda: edit_filter(portal.view, "quarter", 2, ACTOR, 0))
    region = read_filter(portal.view, "region")
    before = read_filter(portal.view, "quarter")
    history = portal.events.events_through(region.sequence)
    assert [entry.field for entry in history] == ["region"]
    after = link.deliver()
    assert (before.value, before.version, after.value, after.version) == (1, 0, 2, 1)
    observed["delayed_quarter"] = [[before.value, before.version], [after.value, after.version]]
    portal.close()
    print(json.dumps(observed, sort_keys=True))


if __name__ == "__main__":
    main()
