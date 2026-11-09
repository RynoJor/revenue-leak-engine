from route import Assigner, Rep, Router

REPS = [
    Rep("R01", "Ana", "North America", "ana@x.example"),
    Rep("R02", "Ben", "North America", "ben@x.example"),
    Rep("R03", "Cai", "EMEA", "cai@x.example"),
]


def test_round_robin_alternates_within_region():
    a = Assigner(REPS, capacity=10)
    picks = [a.assign("North America")[0] for _ in range(4)]
    assert picks == ["R01", "R02", "R01", "R02"]
    assert a.assign("EMEA")[0] == "R03"


def test_assignment_counts_against_load():
    a = Assigner(REPS, load={"R01": 3}, capacity=10)
    a.assign("North America")
    assert a.load["R01"] == 4


def test_skips_rep_at_capacity():
    a = Assigner(REPS, load={"R01": 5, "R02": 0}, capacity=5)
    rep, reason, flags = a.assign("North America")
    assert rep == "R02"
    assert "skipped 1" in reason
    assert flags == []


def test_all_reps_full_goes_to_least_loaded_and_flags():
    a = Assigner(REPS, load={"R01": 7, "R02": 6}, capacity=5)
    rep, _, flags = a.assign("North America")
    assert rep == "R02"
    assert "over_capacity" in flags


def test_unknown_region_uses_all_reps_and_flags():
    a = Assigner(REPS, capacity=10)
    rep, _, flags = a.assign(None)
    assert rep in {"R01", "R02", "R03"}
    assert "unknown_region" in flags


def test_duplicate_goes_to_existing_owner_not_round_robin():
    existing = [{"lead_id": "L1", "company": "Larkwell Bakery LLC", "contact_name": "Nora Galloway",
                 "email": "nora@larkwellbakery.com", "owner_rep_id": "R02", "status": "contacted"}]
    router = Router(REPS, existing, capacity=10, alert_mode="silent")
    res = router.route({"lead_id": "NEW", "company": "Larkwell Bakery", "contact_name": "Nora G",
                        "email": "NORA@larkwellbakery.com", "region": "North America",
                        "source": "demo_request"})
    assert res.assigned_rep == "R02"
    assert res.dedupe_hit and res.dedupe_match == "email:L1"


def test_missing_email_is_rejected_before_assignment():
    router = Router(REPS, [], alert_mode="silent")
    res = router.route({"lead_id": "NEW", "company": "Acme", "contact_name": "A B",
                        "region": "EMEA", "source": "website_form"})
    assert res.status == "rejected"
    assert res.assigned_rep == ""
    assert "missing_email" in res.flags
