"""Tests for question classification and answer resolution.

These exercise the resolver directly rather than through a browser: the six control
types now share one taxonomy, so a question only has to be got right once, and this
is where that is checked.
"""

from pathlib import Path
import pytest

from core.config.loader import load_profile
from core.solver.questions import Kind, QKey, classify
from core.solver.resolver import AnswerResolver, pick_option

FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.test.yaml"


@pytest.fixture
def profile():
    return load_profile(FIXTURE_PROFILE)


@pytest.fixture
def resolver(profile):
    return AnswerResolver(profile)


# -- pick_option ---------------------------------------------------------

def test_exact_option_beats_a_longer_one_containing_it():
    assert pick_option(["Male"], ["Female", "Male"]) == "Male"


def test_substring_match_respects_word_boundaries():
    """"male" must not find itself inside "female"."""
    assert pick_option(["Male"], ["Female"]) is None


def test_a_lower_ranked_option_cannot_win_by_appearing_first():
    offered = ["Remote", "Hybrid"]
    assert pick_option(["Hybrid", "Remote"], offered) == "Hybrid"


def test_prefix_match_finds_a_qualified_yes():
    assert pick_option(["Yes"], ["Yes, I am authorized", "No"]) == "Yes, I am authorized"


def test_no_offered_option_returns_none():
    assert pick_option(["Asian"], ["White", "Black or African American"]) is None


# -- classification ------------------------------------------------------

CONTROL_KINDS = [Kind.BUTTON, Kind.RADIO, Kind.CHECKBOX, Kind.SELECT, Kind.COMBOBOX]


@pytest.mark.parametrize("kind", CONTROL_KINDS)
def test_work_auth_is_recognised_whatever_control_asks_it(kind):
    q = "Are you legally authorized to work in the United States?"
    assert classify(q, kind) is QKey.WORK_AUTH


@pytest.mark.parametrize("kind", CONTROL_KINDS)
def test_sponsorship_is_not_mistaken_for_work_auth(kind):
    q = "Will you now or in the future require visa sponsorship for employment?"
    assert classify(q, kind) is QKey.SPONSORSHIP


def test_age_pattern_cannot_fire_on_a_text_input():
    """The text path matches against ids and class names, where a bare "age" is noise."""
    assert classify("age", Kind.RADIO) is QKey.AGE_18
    assert classify("age", Kind.TEXT) is not QKey.AGE_18


def test_city_and_location_stay_distinct_for_text():
    assert classify("Which city are you located in?", Kind.TEXT) is QKey.CITY
    assert classify("Current location", Kind.TEXT) is QKey.LOCATION


def test_school_field_is_not_read_as_a_state_field():
    assert classify("school state", Kind.TEXT) is not QKey.STATE


# -- answers come from the profile, not from the code --------------------

def test_work_auth_follows_the_disclosure(profile):
    assert AnswerResolver(profile).resolve(
        "Are you legally authorized to work in the US?", Kind.RADIO
    ).text == "Yes"

    profile.disclosures.us_authorized = False
    assert AnswerResolver(profile).resolve(
        "Are you legally authorized to work in the US?", Kind.RADIO
    ).text == "No"


def test_sponsorship_follows_the_disclosure(profile):
    assert AnswerResolver(profile).resolve(
        "Will you require sponsorship?", Kind.SELECT
    ).text == "No"

    profile.disclosures.requires_future_sponsorship = True
    assert AnswerResolver(profile).resolve(
        "Will you require sponsorship?", Kind.SELECT
    ).text == "Yes"


def test_race_is_read_from_the_profile(resolver, profile):
    ans = resolver.resolve("Race / Ethnicity", Kind.RADIO)
    assert ans.candidates == [profile.disclosures.race_ethnicity]
    assert ans.source == "disclosure"


def test_an_unset_demographic_is_flagged_rather_than_guessed(profile):
    """Assuming a race is a wrong answer about a real person, not a safe default."""
    profile.disclosures.race_ethnicity = None
    ans = AnswerResolver(profile).resolve("Race / Ethnicity", Kind.RADIO)
    assert not ans
    assert ans.key is QKey.RACE


def test_veteran_answer_offers_every_phrasing_a_form_might_use(resolver):
    ans = resolver.resolve("Protected Veteran Status", Kind.SELECT)
    assert pick_option(ans.candidates, ["I am not a protected veteran"])
    assert pick_option(ans.candidates, ["Not a Veteran"])
    assert pick_option(ans.candidates, ["Yes", "No"]) == "No"


def test_disability_answer_offers_every_phrasing_a_form_might_use(resolver):
    ans = resolver.resolve("Disability Status", Kind.SELECT)
    assert pick_option(ans.candidates, ["No, I do not have a disability"])
    assert pick_option(ans.candidates, ["Yes", "No"]) == "No"


def test_school_is_the_candidates_school_not_a_hardcoded_one(resolver, profile):
    ans = resolver.resolve("University or College", Kind.SELECT)
    assert ans.candidates == [profile.education.school]


def test_grad_year_honours_the_targeted_cohort(profile):
    assert AnswerResolver(profile, grad_year=2029).resolve(
        "Expected graduation year", Kind.TEXT
    ).text == "2029"


# -- kind changes the shape of the answer, not its meaning ---------------

def test_a_text_box_gets_one_city_and_a_picker_gets_a_fallback_chain(resolver):
    typed = resolver.resolve("Which city are you located in?", Kind.TEXT)
    assert typed.text == "Columbus"

    picker = resolver.resolve("Current location", Kind.COMBOBOX)
    assert picker.candidates[0] == "Columbus, OH"
    assert "Columbus" in picker.candidates


def test_country_picker_offers_the_spellings_a_dropdown_may_use(resolver):
    ans = resolver.resolve("Country", Kind.COMBOBOX)
    assert pick_option(ans.candidates, ["United States of America"])
    assert pick_option(ans.candidates, ["United States"])


# -- fallbacks -----------------------------------------------------------

def test_an_unclaimed_yes_no_question_is_answered_but_not_trusted(resolver):
    ans = resolver.resolve(
        "Have you read the job description?", Kind.BUTTON, offered=["Yes", "No"]
    )
    assert ans.text == "Yes"
    assert ans.confident is False


def test_an_adverse_screening_question_gets_a_no(resolver):
    ans = resolver.resolve(
        "Have you ever been convicted of a felony?", Kind.BUTTON, offered=["Yes", "No"]
    )
    assert ans.text == "No"


def test_the_yes_no_fallback_needs_a_complete_two_way_choice(resolver):
    assert not resolver.resolve(
        "Pick a colour", Kind.BUTTON, offered=["Red", "Green", "Blue"]
    )


def test_a_known_question_a_form_cannot_answer_falls_through(resolver):
    """Ranked answers are matched against what the form offers, not asserted at it."""
    ans = resolver.resolve(
        "Are you open to relocation?", Kind.BUTTON, offered=["Yes", "No"]
    )
    assert ans.key is QKey.RELOCATION

    weird = resolver.resolve(
        "Are you open to relocation?", Kind.BUTTON, offered=["Maybe", "Unsure"]
    )
    assert not weird


def test_a_posting_specific_answer_wins_over_no_answer_at_all(profile):
    r = AnswerResolver(profile, answers={"favourite editor": "Neovim"})
    assert r.resolve("What is your favourite editor?", Kind.TEXT).text == "Neovim"
    assert r.resolve("What is your favourite editor?", Kind.TEXT).source == "custom"


def test_an_empty_personal_field_asks_for_a_human(profile):
    profile.preferences.address.street = None
    ans = AnswerResolver(profile).resolve("Street Address", Kind.TEXT)
    assert not ans
    assert ans.needs_attention


def test_a_missing_answer_to_a_non_personal_field_is_not_flagged(profile):
    profile.candidate.links.linkedin = None
    ans = AnswerResolver(profile).resolve("LinkedIn Profile", Kind.TEXT)
    assert not ans
    assert not ans.needs_attention


def test_open_response_is_sourced_from_the_profile(resolver, profile):
    ans = resolver.open_response("What excites you about our company?")
    assert ans.text == profile.custom_answers["why us"]
    assert ans.confident is False


def test_open_response_stays_empty_when_the_profile_says_nothing(profile):
    profile.custom_answers = {}
    profile.experience_highlights = []
    assert not AnswerResolver(profile).open_response("Tell us about yourself")


def test_a_consent_box_is_ticked_whatever_its_label_says(resolver):
    ans = resolver.resolve(
        "I agree to the candidate privacy policy and certify my responses",
        Kind.CHECKBOX,
    )
    assert ans.is_consent
    assert ans


def test_a_bare_availability_checkbox_reads_its_own_label(resolver):
    assert resolver.wants_term_option("Summer 2028")
    assert not resolver.wants_term_option("Spring 2027 (part-time)")
