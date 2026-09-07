"""What an application form is asking, independent of how it asks it.

Every ATS renders the same two dozen screening questions, and each one renders them
with a different control: Ashby uses option pills, Greenhouse uses radios, Workday
uses comboboxes. Classification belongs in one table so that "are you authorized to
work in the US" is recognised once rather than once per control type.

`classify` returns only the question's meaning. Turning a meaning into an answer is
`core.solver.resolver`'s job, because the answer depends on the profile and on what
options the form actually offers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import FrozenSet, List, Optional, Pattern


class Kind(str, Enum):
    """The control a question is rendered with."""

    BUTTON = "button"        # segmented pills / Yes-No button groups
    RADIO = "radio"
    CHECKBOX = "checkbox"
    SELECT = "select"        # native <select>
    COMBOBOX = "combobox"    # react-select, Workday dropdowns, geo autocompletes
    TEXT = "text"            # text, email, tel, url, number, textarea


CHOICE_KINDS: FrozenSet[Kind] = frozenset(
    {Kind.BUTTON, Kind.RADIO, Kind.CHECKBOX, Kind.SELECT, Kind.COMBOBOX}
)
ALL_KINDS: FrozenSet[Kind] = CHOICE_KINDS | frozenset({Kind.TEXT})


class QKey(str, Enum):
    """A question's meaning."""

    # Screening
    WORK_AUTH = "work_auth"
    SPONSORSHIP = "sponsorship"
    CITIZENSHIP = "citizenship"
    ENROLLED = "enrolled"
    AGE_18 = "age_18"
    EXPERIENCE_YEARS = "experience_years"
    PRIOR_EXPERIENCE = "prior_experience"
    RELOCATION = "relocation"
    ONSITE_LOCATION = "onsite_location"
    AVAILABILITY_TERM = "availability_term"
    REFERRAL_SOURCE = "referral_source"
    AGREEMENT = "agreement"
    OFFER_DEADLINE = "offer_deadline"
    TEST_SCORES = "test_scores"

    # Demographics
    GENDER = "gender"
    PRONOUNS = "pronouns"
    HISPANIC = "hispanic"
    RACE = "race"
    VETERAN = "veteran"
    DISABILITY = "disability"

    # Identity
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    PREFERRED_NAME = "preferred_name"
    EMAIL = "email"
    PHONE = "phone"

    # Education
    SCHOOL = "school"
    EDUCATION_LEVEL = "education_level"
    DEGREE = "degree"
    MAJOR = "major"
    MINOR = "minor"
    GPA = "gpa"
    GRAD_YEAR = "grad_year"
    GRAD_MONTH = "grad_month"
    GRAD_DATE = "grad_date"

    # Address
    COUNTRY = "country"
    STREET = "street"
    CITY = "city"
    STATE = "state"
    POSTAL_CODE = "postal_code"
    LOCATION = "location"

    # Links
    LINKEDIN = "linkedin"
    GITHUB = "github"
    WEBSITE = "website"

    # Logistics
    START_DATE = "start_date"
    END_DATE = "end_date"
    SALARY = "salary"
    CURRENT_EMPLOYER = "current_employer"
    CURRENT_TITLE = "current_title"

    # Free response
    OPEN_RESPONSE = "open_response"


@dataclass(frozen=True)
class QuestionPattern:
    key: QKey
    match: Pattern[str]
    kinds: FrozenSet[Kind]
    # A question this pattern would otherwise capture but does not mean.
    veto: Optional[Pattern[str]] = None


def _p(expr: str) -> Pattern[str]:
    return re.compile(expr, re.IGNORECASE)


_CHOICE = CHOICE_KINDS
_ALL = ALL_KINDS
_TEXT = frozenset({Kind.TEXT})


# Order is behaviour. The first pattern that matches wins, so a narrow question has
# to precede the broad one that would swallow it: sponsorship before work
# authorization, relocation before which-onsite-location, city before location.
PATTERNS: List[QuestionPattern] = [
    QuestionPattern(
        QKey.SPONSORSHIP,
        _p(r"\b(require.*(sponsorship|sponsor|visa|work authorization)|will you.*require|future.*sponsor|sponsorship.*future)\b"),
        _CHOICE,
    ),
    QuestionPattern(
        QKey.WORK_AUTH,
        _p(r"\b(authorized to work|legally authorized|eligible to work|legal right to work|work authorization.*united states|authorized.*u\.?s\.?)\b"),
        _CHOICE,
        veto=_p(r"\b(require|need|sponsorship|future)\b"),
    ),
    QuestionPattern(
        QKey.CITIZENSHIP,
        _p(r"\b(international student|work permit|citizenship)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.GRAD_DATE,
        _p(r"\b(graduation[\s_-]?date|expected[\s_-]?grad)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.GRAD_YEAR,
        _p(r"\b(graduation[\s_-]?year|grad[\s_-]?year|graduate in|year will you graduate|expected[\s_-]?graduation)\b"),
        frozenset({Kind.BUTTON, Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
    ),
    QuestionPattern(
        QKey.GRAD_MONTH,
        _p(r"\b(graduation[\s_-]?month|grad[\s_-]?month)\b"),
        frozenset({Kind.SELECT, Kind.TEXT}),
    ),
    QuestionPattern(
        QKey.ENROLLED,
        _p(r"\b(currently[\s_-]?enrolled|current student|are you currently enrolled|enrolled in an accredited)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.SELECT, Kind.TEXT}),
    ),
    QuestionPattern(
        QKey.AGE_18,
        _p(r"\b(18 years|at least 18|legal age|18 or older|age)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.SELECT}),
    ),
    QuestionPattern(
        QKey.EXPERIENCE_YEARS,
        _p(r"\b(years of experience|experience level)\b"),
        frozenset({Kind.RADIO}),
    ),
    QuestionPattern(
        QKey.PRIOR_EXPERIENCE,
        _p(r"\b(prior internship|prior experience|co-op experience|previous experience|experience building|years of experience)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.RELOCATION,
        _p(r"\b(relocate|relocation|willing to relocate|open to relocate|on-site|work on-site)\b"),
        _CHOICE,
    ),
    QuestionPattern(
        QKey.ONSITE_LOCATION,
        _p(r"\b(which onsite|onsite location)\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.AVAILABILITY_TERM,
        _p(r"\b(available full-time|available to work full-time|ideal term duration|term duration|co-op duration|intern season|season)\b"),
        _ALL,
    ),
    QuestionPattern(
        QKey.GENDER,
        _p(r"\b(gender|sex|gender[\s_-]?identity)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.HISPANIC,
        _p(r"\b(hispanic|latino)\b"),
        frozenset({Kind.BUTTON, Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.RACE,
        _p(r"\b(race|ethnicity|ethnic[\s_-]?origin|demographic)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.CHECKBOX, Kind.SELECT}),
    ),
    QuestionPattern(
        QKey.VETERAN,
        _p(r"\b(veteran|military[\s_-]?status|protected[\s_-]?veteran|military)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.DISABILITY,
        _p(r"\b(disability|handicap|impairment|physical[\s_-]?or[\s_-]?mental[\s_-]?impairment)\b"),
        frozenset({Kind.BUTTON, Kind.RADIO, Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.REFERRAL_SOURCE,
        _p(r"\b(how did you hear|referral|source|where did you hear)\b"),
        frozenset({Kind.RADIO, Kind.CHECKBOX, Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.OFFER_DEADLINE,
        _p(r"\b(offer deadline|deadline)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.TEST_SCORES,
        _p(r"\b(sat[\s_-]?score|act[\s_-]?score)\b"),
        _TEXT,
    ),

    # Identity. Text only: these are named by the input's own label, never asked as
    # a multiple choice.
    QuestionPattern(
        QKey.FULL_NAME,
        _p(r"\b(full[\s_-]?name|legal[\s_-]?name)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.FIRST_NAME,
        _p(r"\b(first[\s_-]?name|given[\s_-]?name|firstname)\b"),
        _TEXT,
        veto=_p(r"\blast\b"),
    ),
    QuestionPattern(
        QKey.LAST_NAME,
        _p(r"\b(last[\s_-]?name|family[\s_-]?name|surname|lastname)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.PREFERRED_NAME,
        _p(r"\bpreferred[\s_-]?name\b|\bnickname\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.FULL_NAME,
        _p(r"\bname\b"),
        _TEXT,
        veto=_p(r"\b(first|last|user|file|company|school|preferred|middle|login)\b"),
    ),
    QuestionPattern(
        QKey.PRONOUNS,
        _p(r"\bpronoun"),
        frozenset({Kind.RADIO, Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
    ),
    QuestionPattern(QKey.EMAIL, _p(r"\bemail\b"), _TEXT),
    QuestionPattern(QKey.PHONE, _p(r"\b(phone|mobile|cell|telephone)\b"), _TEXT),

    # Address. Country first, then the narrow parts, then the catch-all.
    QuestionPattern(
        QKey.COUNTRY,
        _p(r"\bcountry\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
    ),
    QuestionPattern(
        QKey.STREET,
        _p(r"\b(address[\s_-]?line[\s_-]?1|street[\s_-]?address)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.CITY,
        _p(r"\b(which city|current[\s_-]?city|city)\b"),
        _TEXT,
        veto=_p(r"\b(school|job)\b"),
    ),
    QuestionPattern(
        QKey.STATE,
        _p(r"\b(state|province|region)\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
        veto=_p(r"\b(school|job|education)\b"),
    ),
    QuestionPattern(
        QKey.POSTAL_CODE,
        _p(r"\b(zip|postal[\s_-]?code|postcode)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.LOCATION,
        _p(r"\b(location|city|where do you plan|current[\s_-]?address)\b"),
        frozenset({Kind.COMBOBOX, Kind.TEXT}),
        veto=_p(r"\b(school|job|education)\b"),
    ),

    # Education. School before degree: "highest level of education" names no school,
    # so the flip is safe and it stops "degree program" from eating a school field.
    QuestionPattern(
        QKey.SCHOOL,
        _p(r"\b(school|university|college|institution|post secondary)\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
        veto=_p(r"\bhigh school\b"),
    ),
    QuestionPattern(
        QKey.EDUCATION_LEVEL,
        _p(r"\b(highest level of education|education level)\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX}),
    ),
    QuestionPattern(
        QKey.DEGREE,
        _p(r"\bdegree\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
        veto=_p(r"\b(are you|enrolled)\b"),
    ),
    QuestionPattern(
        QKey.MAJOR,
        _p(r"\b(major|discipline|field[\s_-]?of[\s_-]?study)\b"),
        frozenset({Kind.SELECT, Kind.COMBOBOX, Kind.TEXT}),
    ),
    QuestionPattern(QKey.MINOR, _p(r"\bminor\b"), _TEXT),
    QuestionPattern(QKey.GPA, _p(r"\bgpa\b|\bgrade[\s_-]?point\b"), _TEXT),

    # Links
    QuestionPattern(QKey.LINKEDIN, _p(r"\blinkedin\b"), _TEXT),
    QuestionPattern(QKey.GITHUB, _p(r"\bgithub\b"), _TEXT),
    QuestionPattern(
        QKey.WEBSITE,
        _p(r"\b(website|portfolio|personal[\s_-]?site)\b"),
        _TEXT,
    ),

    # Logistics
    QuestionPattern(
        QKey.START_DATE,
        _p(r"\b(start[\s_-]?date|earliest[\s_-]?start|available[\s_-]?to[\s_-]?start|availability)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.END_DATE,
        _p(r"\b(end[\s_-]?date|available[\s_-]?until)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.SALARY,
        _p(r"\b(salary|compensation|desired[\s_-]?pay|pay[\s_-]?expectation|hourly[\s_-]?rate)\b"),
        _TEXT,
    ),
    QuestionPattern(
        QKey.CURRENT_EMPLOYER,
        _p(r"\b(current[\s_-]?company|employer|current[\s_-]?employer|organization|org)\b"),
        _TEXT,
        veto=_p(r"\b(school|education)\b"),
    ),
    QuestionPattern(
        QKey.CURRENT_TITLE,
        _p(r"\b(current[\s_-]?title|current[\s_-]?role|job[\s_-]?title)\b"),
        _TEXT,
        veto=_p(r"\b(school|education)\b"),
    ),

    # Consent boxes, and anything left that reads like an essay prompt.
    QuestionPattern(
        QKey.AGREEMENT,
        _p(r"\b(certify|certif|agree|terms|condition|privacy|declaration|policy|attest|consent|authorized|acknowledge)\b"),
        frozenset({Kind.CHECKBOX}),
    ),
    QuestionPattern(
        QKey.OPEN_RESPONSE,
        _p(r"\b(tell us|why|excite|interest|share|describe|about yourself|statement|cover|note|anything else)\b"),
        _TEXT,
    ),
]


def classify(text: str, kind: Kind) -> Optional[QKey]:
    """Name what `text` is asking, or None if no pattern claims it.

    `kind` is not cosmetic: the text a text input is matched against includes its id
    and class names, so a pattern like AGE_18's bare "age" is only allowed to fire on
    controls where it is really reading a question.
    """
    if not text:
        return None
    for p in PATTERNS:
        if kind not in p.kinds:
            continue
        if not p.match.search(text):
            continue
        if p.veto is not None and p.veto.search(text):
            continue
        return p.key
    return None
