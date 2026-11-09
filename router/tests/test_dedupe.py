import csv

import pytest

from route import DATA_DIR, LeadIndex, load_leads, normalise_company

ORIGINAL = {"lead_id": "L1", "company": "Larkwell Bakery LLC", "contact_name": "Nora Galloway",
            "email": "nora.galloway@larkwellbakery.com", "owner_rep_id": "R03"}


def lead(**kw):
    base = {"lead_id": "NEW", "company": "", "contact_name": "", "email": ""}
    return {**base, **kw}


def test_exact_email_ignores_case():
    idx = LeadIndex([ORIGINAL])
    assert idx.find(lead(email="Nora.Galloway@LarkwellBakery.com")) == ("email", "L1", "R03")


@pytest.mark.parametrize("name", ["larkwell bakery", "Larkwell Bakery, Inc.", "Lark Well Bakery"])
def test_company_variants_normalise_to_the_same_key(name):
    assert normalise_company(name) == normalise_company("Larkwell Bakery LLC")


def test_known_account_matches_on_company_name():
    idx = LeadIndex([ORIGINAL])
    hit = idx.find(lead(company="Larkwell Bakery Co.", contact_name="Sam Other",
                        email="sam@gmail.com"))
    assert hit == ("company", "L1", "R03")


def test_typo_in_company_matches_when_surname_agrees():
    idx = LeadIndex([ORIGINAL])
    hit = idx.find(lead(company="Lakrwell Bakery", contact_name="N. Galloway",
                        email="ngalloway@gmail.com"))
    assert hit == ("company", "L1", "R03")


def test_similar_name_alone_is_not_a_match():
    # 'Riverlight' vs 'Silverlight' scores > 0.9 but these are different businesses.
    idx = LeadIndex([{"lead_id": "L1", "company": "Silverlight Clinic Co.",
                      "contact_name": "Ann Lee", "email": "ann@silverlightclinic.com",
                      "owner_rep_id": "R05"}])
    assert idx.find(lead(company="Riverlight Clinic", contact_name="Bo Chan",
                         email="bo@riverlightclinic.com")) is None


def test_shared_public_domain_is_not_a_signal():
    idx = LeadIndex([{"lead_id": "L1", "company": "Silverlight Clinic", "contact_name": "Ann Lee",
                      "email": "ann.lee@gmail.com", "owner_rep_id": "R05"}])
    assert idx.find(lead(company="Riverlight Clinic", contact_name="Bo Chan",
                         email="bo.chan@gmail.com")) is None


def test_no_match_for_new_company():
    assert LeadIndex([ORIGINAL]).find(lead(company="Quillbrook Pilates",
                                           email="a@quillbrook.example")) is None


# Eval against the planted answer key (tests may read it; the router never does).

@pytest.fixture(scope="module")
def sample():
    leads = load_leads()
    with open(DATA_DIR / "answer_key" / "duplicate_pairs.csv", newline="", encoding="utf-8") as f:
        pairs = list(csv.DictReader(f))
    return leads, pairs


def test_finds_every_planted_duplicate(sample):
    leads, pairs = sample
    by_id = {l["lead_id"]: l for l in leads}
    dup_ids = {p["duplicate_lead_id"] for p in pairs}
    idx = LeadIndex([l for l in leads if l["lead_id"] not in dup_ids])
    found = sum(
        (hit := idx.find(by_id[p["duplicate_lead_id"]])) is not None
        and hit[1] == p["original_lead_id"]
        for p in pairs
    )
    assert found == len(pairs) == 90


def test_no_false_matches_among_original_leads(sample):
    leads, pairs = sample
    dup_ids = {p["duplicate_lead_id"] for p in pairs}
    idx, false_hits = LeadIndex(), []
    for l in sorted((l for l in leads if l["lead_id"] not in dup_ids), key=lambda l: l["lead_id"]):
        if idx.find(l):
            false_hits.append(l["lead_id"])
        idx.add(l, l["owner_rep_id"])
    assert false_hits == []
