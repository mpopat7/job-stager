"""One answer per question, whatever control the question is wearing.

`AnswerResolver.resolve` is the single place that turns a classified question into
something to type or an option to pick. Before this existed the same decision was
spelled out once per control type -- six near-identical if/elif ladders in
`core.adapters.base` and a seventh in the CLI solver -- so "are you authorized to
work in the US" had six answers that could drift apart.

The resolver never touches the page. It returns an `Answer`; the caller decides how
to apply it. For a choice control the answer is a best-first list of option texts to
look for, because the resolver cannot know what a given form will offer: a work-model
question might list Remote/Onsite or Remote/Hybrid/In-office, and both have to work.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Sequence

from core.config.schema import CandidateProfile
from core.solver.questions import Kind, QKey, classify

# Nothing here answers these; leaving one silently blank reads as "nothing to fill in".
PERSONAL_KEYS = frozenset({
    QKey.STREET, QKey.CITY, QKey.STATE, QKey.POSTAL_CODE, QKey.LOCATION,
    QKey.PRONOUNS, QKey.SALARY, QKey.START_DATE, QKey.END_DATE,
})

# A box whose label is the thing being agreed to, not one option among several.
CONSENT_KEYS = frozenset({QKey.AGREEMENT})

# A screening question worded to invite a "no".
_ADVERSE = ("felony", "convict", "crime", "terminate", "fired")


def _norm(value: str) -> str:
    """Fold case and punctuation so "on-site" and "Onsite" compare equal."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def pick_option(desired: Sequence[str], offered: Sequence[str]) -> Optional[str]:
    """The highest-ranked wanted text that appears among the options a form offers.

    Each tier is tried across the whole option list before the next one, so an exact
    "Male" wins over a "Female" that merely contains the letters. The last tier is a
    whole-word search rather than a substring search for the same reason.
    """
    pairs = [(o, _norm(o), (o or "").lower()) for o in offered]
    pairs = [(o, n, low) for o, n, low in pairs if n]
    for want in desired:
        nw = _norm(want)
        if not nw:
            continue
        for original, normed, _low in pairs:
            if normed == nw:
                return original
        for original, normed, _low in pairs:
            if normed.startswith(nw) or nw.startswith(normed):
                return original
        low_want = (want or "").lower().strip()
        if low_want:
            word = re.compile(rf"\b{re.escape(low_want)}\b")
            for original, _normed, low in pairs:
                if word.search(low):
                    return original
    return None


@dataclass(frozen=True)
class Answer:
    """What to put in one control, and where it came from.

    `source` and `confident` exist for the review UI and for the free-response pass:
    a low-confidence answer is what gets flagged for the human, and an unanswered
    open question is what a model is asked to write.
    """

    key: Optional[QKey] = None
    value: Any = None                       # str for text, list[str] of candidates for a choice
    source: str = "none"                    # profile|disclosure|preference|policy|custom|none
    confident: bool = True

    def __bool__(self) -> bool:
        return bool(self.candidates) if isinstance(self.value, list) else bool(self.value)

    @property
    def candidates(self) -> List[str]:
        if self.value is None:
            return []
        if isinstance(self.value, (list, tuple)):
            return [str(v) for v in self.value if v]
        return [str(self.value)] if str(self.value) else []

    @property
    def text(self) -> Optional[str]:
        c = self.candidates
        return c[0] if c else None

    @property
    def is_consent(self) -> bool:
        """True when ticking the box *is* the answer, whatever its label says."""
        return self.key in CONSENT_KEYS

    @property
    def needs_attention(self) -> bool:
        return not self and self.key in PERSONAL_KEYS


NO_ANSWER = Answer()


class AnswerResolver:
    """Answers one form's questions from one candidate's profile."""

    def __init__(
        self,
        profile: CandidateProfile,
        grad_year: Optional[int] = None,
        answers: Optional[Dict[str, str]] = None,
    ):
        self.profile = profile
        self.grad_year = grad_year or profile.education.graduation_year
        # Answers the solver produced for this posting, keyed by question fragment.
        self.answers = answers or {}

    # -- entry point ------------------------------------------------------

    def resolve(
        self,
        question: str,
        kind: Kind,
        offered: Optional[Sequence[str]] = None,
    ) -> Answer:
        """Answer `question` as asked by a control of type `kind`.

        `offered` is the complete set of options the control shows, and only a control
        that shows them all at once can pass it -- a button group or a `<select>`. A
        radio or checkbox sees one option at a time, so it passes nothing and matches
        the returned candidates against itself.

        A keyed answer comes back as its full ranked candidate list rather than one
        string: the resolver cannot know whether a given form spells the option
        "No" or "I do not require sponsorship".
        """
        key = classify(question, kind)
        if key is not None:
            ans = self._answer_for(key, kind)
            if not ans:
                # A key with nothing behind it still names the field, which is what
                # makes an empty address box a flag rather than a skipped control.
                return Answer(key=key)
            if offered is None or pick_option(ans.candidates, offered):
                return ans
            # Understood, but this form offers none of the answers to it. Fall through
            # rather than leave a required control blank.

        custom = self._custom_answer(question)
        if custom:
            return custom

        if offered is not None:
            screen = self._yes_no_screen(question, offered)
            if screen:
                return screen

        return Answer(key=key) if key is not None else NO_ANSWER

    # -- generic fallbacks ------------------------------------------------

    def _custom_answer(self, question: str) -> Answer:
        """Answers written for this posting, then standing answers on the profile."""
        q = (question or "").lower()
        for q_key, value in self.answers.items():
            if q_key and q_key.lower() in q:
                return Answer(value=value, source="custom")
        for c_key, value in self.profile.custom_answers.items():
            if c_key and c_key.lower() in q:
                return Answer(value=value, source="custom")
        return NO_ANSWER

    def _yes_no_screen(self, question: str, options: Sequence[str]) -> Answer:
        """A two-way screening question nothing else answered.

        Marked unconfident on purpose: this is a guess from the shape of the control,
        not from anything the profile says.
        """
        offered = {_norm(o) for o in options if o}
        if offered != {"yes", "no"}:
            return NO_ANSWER
        q = (question or "").lower()
        want = "No" if any(bad in q for bad in _ADVERSE) else "Yes"
        return Answer(value=[want], source="policy", confident=False)

    def open_response(self, question: str) -> Answer:
        """Text for an essay box, sourced from the profile rather than invented.

        A canned blurb about someone else is worse than an empty box, so this returns
        nothing when the profile holds nothing -- which is the slot the free-response
        model fills.
        """
        q = (question or "").lower()
        if re.search(r"\b(project|built|code|work sample|portfolio)\b", q):
            summary = " ".join(
                h.summary for h in self.profile.experience_highlights[:2] if h.summary
            )
            if summary:
                return Answer(key=QKey.OPEN_RESPONSE, value=summary,
                              source="profile", confident=False)
            return NO_ANSWER
        blurb = next(
            (v for k, v in self.profile.custom_answers.items()
             if k.lower() in ("why us", "about", "about me")),
            None,
        )
        if blurb:
            return Answer(key=QKey.OPEN_RESPONSE, value=blurb,
                          source="custom", confident=False)
        return NO_ANSWER

    def wants_term_option(self, option_text: str) -> bool:
        """Whether a bare checkbox's own label names the term the candidate wants.

        Some forms list availability as a row of checkboxes with no question above
        them, so the option text is the only thing to read.
        """
        return bool(pick_option(self._term_candidates(), [option_text]))

    # -- the answer table -------------------------------------------------

    def _answer_for(self, key: QKey, kind: Kind) -> Answer:
        p = self.profile
        c = p.candidate
        d = p.disclosures
        prefs = p.preferences
        edu = p.education
        addr = prefs.address
        text = kind is Kind.TEXT

        def A(value, source, confident=True):
            return Answer(key=key, value=value, source=source, confident=confident)

        # -- screening
        if key is QKey.WORK_AUTH:
            return A(["Yes" if d.us_authorized else "No"], "disclosure")
        if key is QKey.SPONSORSHIP:
            needs = d.requires_sponsorship or d.requires_future_sponsorship
            return A(["Yes" if needs else "No"], "disclosure")
        if key is QKey.CITIZENSHIP:
            auth = d.work_authorization or "Authorized to work"
            if d.requires_sponsorship or d.requires_future_sponsorship:
                return A(f"{auth}, sponsorship required", "disclosure")
            return A(f"{auth}, authorized to work with no sponsorship or visa required",
                     "disclosure")
        if key is QKey.ENROLLED:
            return A("Yes" if text else ["Yes"], "policy")
        if key is QKey.AGE_18:
            return A(["Yes", "18 or older", "18+", "18-20"], "policy")
        if key is QKey.EXPERIENCE_YEARS:
            return A(["0-1", "1-2", "1", "Entry"], "policy", confident=False)
        if key is QKey.PRIOR_EXPERIENCE:
            return A(["Yes"], "profile" if p.experience_highlights else "policy")
        if key is QKey.RELOCATION:
            return A(["Yes" if d.open_to_relocation else "No"], "disclosure")
        if key is QKey.ONSITE_LOCATION:
            return A(list(prefs.locations_ranked), "preference")
        if key is QKey.AVAILABILITY_TERM:
            terms = self._term_candidates()
            return A(terms[0] if text else terms, "preference")
        if key is QKey.REFERRAL_SOURCE:
            ranked = [prefs.referral_source, "LinkedIn", "Company Website",
                      "Career Site", "Job Board", "Internet"]
            return A(ranked[0] if text else ranked, "preference")
        if key is QKey.AGREEMENT:
            return A(["Yes"], "policy")
        if key is QKey.OFFER_DEADLINE:
            return A("None currently.", "policy")
        if key is QKey.TEST_SCORES:
            return A("N/A", "policy")

        # -- demographics. Never hardcoded: an assumed answer here is a wrong answer
        # about a real person, so an unset disclosure returns nothing and gets flagged.
        if key is QKey.GENDER:
            return A(self._gender_candidates(), "disclosure")
        if key is QKey.PRONOUNS:
            return A(prefs.pronouns, "preference")
        if key is QKey.HISPANIC:
            race = (d.race_ethnicity or "").lower()
            if not d.race_ethnicity:
                return NO_ANSWER
            hispanic = "hispanic" in race or "latino" in race
            return A(["Yes"] if hispanic else ["No", "Not Hispanic or Latino"], "disclosure")
        if key is QKey.RACE:
            return A([d.race_ethnicity] if d.race_ethnicity else [], "disclosure")
        if key is QKey.VETERAN:
            return A(self._veteran_candidates(), "disclosure")
        if key is QKey.DISABILITY:
            return A(self._disability_candidates(), "disclosure")

        # -- identity
        if key is QKey.FIRST_NAME:
            return A(c.first_name, "profile")
        if key is QKey.LAST_NAME:
            return A(c.last_name, "profile")
        if key is QKey.FULL_NAME:
            return A(c.full_name, "profile")
        if key is QKey.PREFERRED_NAME:
            return A(c.preferred_name or c.first_name, "profile")
        if key is QKey.EMAIL:
            return A(c.email, "profile")
        if key is QKey.PHONE:
            return A(c.phone, "profile")

        # -- education
        if key is QKey.SCHOOL:
            return A(edu.school, "profile")
        if key is QKey.EDUCATION_LEVEL:
            return A(["Bachelor's Degree", "Bachelor of Science", "Bachelor", "Bachelors"],
                     "profile")
        if key is QKey.DEGREE:
            ranked = [edu.degree, "Bachelor of Science", "Bachelor's Degree", "Bachelor"]
            return A(edu.degree if text else ranked, "profile")
        if key is QKey.MAJOR:
            return A(edu.major, "profile")
        if key is QKey.MINOR:
            return A(edu.minor or "", "profile")
        if key is QKey.GPA:
            return A(str(edu.gpa) if edu.gpa else "", "profile")
        if key is QKey.GRAD_YEAR:
            return A(str(self.grad_year), "profile")
        if key is QKey.GRAD_MONTH:
            return A(edu.graduation_month or "May", "profile")
        if key is QKey.GRAD_DATE:
            return A(f"{edu.graduation_month or 'May'} {self.grad_year}", "profile")

        # -- address
        if key is QKey.COUNTRY:
            country = addr.country
            if text:
                return A(country, "preference")
            return A([country, "United States of America", "USA"] if country else [],
                     "preference")
        if key is QKey.STREET:
            return A(addr.street, "preference")
        if key is QKey.CITY:
            return A(addr.city if text else prefs.location_queries(), "preference")
        if key is QKey.STATE:
            return A(addr.state, "preference")
        if key is QKey.POSTAL_CODE:
            return A(addr.postal_code, "preference")
        if key is QKey.LOCATION:
            return A(addr.one_line() if text else prefs.location_queries(), "preference")

        # -- links
        if key is QKey.LINKEDIN:
            return A(c.links.linkedin, "profile")
        if key is QKey.GITHUB:
            return A(c.links.github, "profile")
        if key is QKey.WEBSITE:
            return A(c.links.portfolio or c.links.github, "profile")

        # -- logistics
        if key is QKey.START_DATE:
            return A(prefs.availability.formatted(prefs.availability.start_date), "preference")
        if key is QKey.END_DATE:
            return A(prefs.availability.formatted(prefs.availability.end_date), "preference")
        if key is QKey.SALARY:
            rate = prefs.compensation.hourly_rate
            if not rate:
                return A(prefs.compensation.note, "preference", confident=False)
            return A(rate if kind is Kind.TEXT else f"${rate}/hr", "preference")
        if key is QKey.CURRENT_EMPLOYER:
            return A(edu.school, "profile")
        if key is QKey.CURRENT_TITLE:
            return A(prefs.current_title, "preference")

        return NO_ANSWER

    # -- candidate lists that more than one key needs ---------------------

    def _term_candidates(self) -> List[str]:
        label = self.profile.preferences.availability.term_label
        ranked = [label] if label else []
        ranked += ["Summer", "4-month", "12-week", "Full-time", "Yes"]
        return [t for t in ranked if t]

    def _gender_candidates(self) -> List[str]:
        g = (self.profile.disclosures.gender or "").strip()
        if not g:
            return []
        low = g.lower()
        if low.startswith(("male", "man", "m ")) or low == "m":
            return [g, "Male", "Man", "Cisgender Man"]
        if low.startswith(("female", "woman")) or low == "f":
            return [g, "Female", "Woman", "Cisgender Woman"]
        return [g]

    def _veteran_candidates(self) -> List[str]:
        v = (self.profile.disclosures.veteran_status or "").strip()
        if not v:
            return []
        if _norm(v) in ("no", "notaveteran", "notaprotectedveteran", "none", "false"):
            return ["I am not a protected veteran", "Not a protected veteran",
                    "Not a veteran", "I am not", "No"]
        return [v, "I identify as one or more of the classifications of a protected veteran",
                "Protected Veteran", "Yes"]

    def _disability_candidates(self) -> List[str]:
        s = (self.profile.disclosures.disability_status or "").strip()
        if not s:
            return []
        if _norm(s) in ("no", "nodisability", "none", "false"):
            return ["No, I do not have a disability", "I do not have a disability",
                    "No disability", "No"]
        return [s, "Yes, I have a disability", "Yes"]
