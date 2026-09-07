from pathlib import Path
import pytest
from playwright.async_api import async_playwright
from core.adapters.base import BaseStagingAdapter
from core.adapters.greenhouse import GreenhouseAdapter
from core.adapters.ashby import AshbyAdapter
from core.adapters.lever import LeverAdapter
from core.adapters.workday import WorkdayAdapter
from core.config.loader import load_profile

from core.scrapers.base import ATSProvider

# Never load_profile() bare here: the search path would find a developer's real,
# gitignored profile.yaml and the assertions below would only hold on that machine.
FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.test.yaml"


def load_test_profile():
    return load_profile(FIXTURE_PROFILE)



SAMPLE_FORM_HTML = """
<!DOCTYPE html>
<html>
<head><title>Stripe - Software Engineering Intern (Summer 2026)</title></head>
<body>
  <h1>Software Engineering Intern (Summer 2026)</h1>
  <form id="app-form">
    <div class="field">
      <label for="first_name">First Name</label>
      <input type="text" id="first_name" name="first_name" />
    </div>
    <div class="field">
      <label for="last_name">Last Name</label>
      <input type="text" id="last_name" name="last_name" />
    </div>
    <div class="field">
      <label for="email">Email Address</label>
      <input type="email" id="email" name="email" />
    </div>
    <div class="field">
      <label for="phone">Phone Number</label>
      <input type="tel" id="phone" name="phone" />
    </div>
    <div class="field">
      <label for="school">University / College</label>
      <input type="text" id="school" name="school" />
    </div>
    <div class="field">
      <label for="degree">Degree</label>
      <input type="text" id="degree" name="degree" />
    </div>
    <div class="field">
      <label for="major">Major / Discipline</label>
      <input type="text" id="major" name="major" />
    </div>
    <div class="field">
      <label for="gpa">GPA</label>
      <input type="text" id="gpa" name="gpa" />
    </div>
    <div class="field">
      <label for="grad_year">Graduation Year</label>
      <input type="text" id="grad_year" name="graduation_year" />
    </div>

    <!-- Links -->
    <div class="field">
      <label for="linkedin">LinkedIn Profile</label>
      <input type="url" id="linkedin" name="urls[linkedin]" />
    </div>
    <div class="field">
      <label for="github">GitHub Profile</label>
      <input type="url" id="github" name="urls[github]" />
    </div>
    <div class="field">
      <label for="website">Personal Website / Portfolio</label>
      <input type="url" id="website" name="website" />
    </div>

    <!-- Radio questions -->
    <fieldset class="question">
      <legend>Are you legally authorized to work in the United States?</legend>
      <label><input type="radio" name="auth" value="Yes" /> Yes</label>
      <label><input type="radio" name="auth" value="No" /> No</label>
    </fieldset>

    <fieldset class="question">
      <legend>Will you now or in the future require visa sponsorship?</legend>
      <label><input type="radio" name="sponsor" value="Yes" /> Yes</label>
      <label><input type="radio" name="sponsor" value="No" /> No</label>
    </fieldset>

    <fieldset class="question">
      <legend>Are you currently enrolled in an accredited degree program?</legend>
      <label><input type="radio" name="enrolled" value="Yes" /> Yes</label>
      <label><input type="radio" name="enrolled" value="No" /> No</label>
    </fieldset>

    <!-- Dropdown -->
    <div class="field">
      <label for="gender">Gender</label>
      <select id="gender" name="gender">
        <option value="">Select...</option>
        <option value="male">Male</option>
        <option value="female">Female</option>
        <option value="decline">Prefer not to say</option>
      </select>
    </div>

    <button type="submit" id="submit-btn">Submit Application</button>
  </form>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_smart_fill_form_comprehensive():
    profile = load_test_profile()
    adapter = GreenhouseAdapter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(SAMPLE_FORM_HTML)

        fields_filled, resume_attached = await adapter.smart_fill_form(
            page=page,
            frame=page.main_frame,
            profile=profile,
        )

        assert fields_filled >= 13
        assert await page.input_value("#first_name") == profile.candidate.first_name
        assert await page.input_value("#last_name") == profile.candidate.last_name
        assert await page.input_value("#email") == profile.candidate.email
        assert await page.input_value("#phone") == profile.candidate.phone
        assert await page.input_value("#school") == profile.education.school
        assert await page.input_value("#degree") == profile.education.degree
        assert await page.input_value("#major") == profile.education.major
        assert await page.input_value("#gpa") == str(profile.education.gpa)
        assert await page.input_value("#grad_year") == str(profile.education.graduation_year)
        assert await page.input_value("#linkedin") == profile.candidate.links.linkedin
        assert await page.input_value("#github") == profile.candidate.links.github

        assert await page.is_checked("input[name='auth'][value='Yes']")
        assert await page.is_checked("input[name='sponsor'][value='No']")
        assert await page.is_checked("input[name='enrolled'][value='Yes']")

        assert await page.input_value("#gender") == "male"
        await browser.close()


@pytest.mark.asyncio
async def test_inject_review_banner_and_rpc():
    adapter = GreenhouseAdapter()
    rpc_called = False

    async def mock_mark_applied():
        nonlocal rpc_called
        rpc_called = True
        return {"success": True, "company": "Stripe", "title": "SWE Intern", "grad_year": 2028}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(SAMPLE_FORM_HTML)

        await page.expose_function("jobStagerMarkApplied", mock_mark_applied)
        await adapter.inject_review_banner(
            page=page,
            fields_filled=13,
            resume_attached=True,
            company="Stripe",
            role="SWE Intern",
            grad_year=2028,
        )

        toolbar = await page.query_selector("#job-stager-toolbar")
        assert toolbar is not None
        banner_text = await toolbar.inner_text()
        assert "13 fields pre-filled" in banner_text
        assert "Resume attached" in banner_text

        apply_btn = await page.query_selector("#job-stager-apply-btn")
        assert apply_btn is not None
        await apply_btn.click()

        await page.wait_for_timeout(500)
        assert rpc_called is True

        modal = await page.query_selector("#job-stager-success-modal")
        assert modal is not None
        modal_text = await modal.inner_text()
        assert "Application Logged!" in modal_text
        assert "Stripe" in modal_text

        await browser.close()


def test_adapters_registered():
    from core.adapters import get_adapter
    assert isinstance(get_adapter(ATSProvider.GREENHOUSE), GreenhouseAdapter)
    assert isinstance(get_adapter(ATSProvider.ASHBY), AshbyAdapter)
    assert isinstance(get_adapter(ATSProvider.LEVER), LeverAdapter)
    assert isinstance(get_adapter(ATSProvider.WORKDAY), WorkdayAdapter)


ADVANCED_FORM_HTML = """
<!DOCTYPE html>
<html>
<head><title>Test Advanced Form</title></head>
<body>
  <form id="advanced-form">
    <div>
      <div class="application-label">Which city are you currently located in?</div>
      <div class="application-field"><input type="text" id="loc_city" name="city" /></div>
    </div>
    <div>
      <div class="application-label">What excites you about our company?</div>
      <div class="application-field"><textarea id="why_us" name="why_us"></textarea></div>
    </div>
    <div class="field">
      <label>Race / Ethnicity</label>
      <label><input type="radio" name="race" value="Asian" /> Asian (Not Hispanic or Latino)</label>
      <label><input type="radio" name="race" value="White" /> White</label>
    </div>
    <div class="field">
      <label>Veteran Status</label>
      <label><input type="radio" name="vet" value="NotVeteran" /> I am not a protected veteran</label>
      <label><input type="radio" name="vet" value="Protected" /> Protected Veteran</label>
    </div>
    <div class="field">
      <label><input type="checkbox" id="terms" name="terms" /> I agree to the candidate privacy policy and certify my responses</label>
    </div>
  </form>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_smart_fill_form_advanced_fields():
    profile = load_test_profile()
    adapter = LeverAdapter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(ADVANCED_FORM_HTML)

        fields_filled, _ = await adapter.smart_fill_form(
            page=page,
            frame=page.main_frame,
            profile=profile,
        )

        assert fields_filled >= 4
        assert await page.input_value("#loc_city") == "Columbus"
        assert await page.input_value("#why_us") == profile.custom_answers["why us"]
        assert await page.is_checked("input[name='race'][value='Asian']")
        assert await page.is_checked("input[name='vet'][value='NotVeteran']")
        assert await page.is_checked("#terms")
        await browser.close()


# A stand-in for React's controlled inputs. React installs a value tracker on every
# input and only commits a change when the DOM value differs from what it last wrote.
# Assigning `el.checked` from JS goes through the property setter, which updates the
# tracker too, so the change event that follows looks like a no-op and state never
# moves -- the box appears ticked but the form submits empty. Only a real user click
# sets checkedness outside the setter and therefore registers.
TRACKED_FORM_HTML = """
<!DOCTYPE html>
<html>
<head><title>Tracked Form</title></head>
<body>
  <form id="tracked-form">
    <fieldset class="field">
      <legend>Are you legally authorized to work in the United States?</legend>
      <label><input type="radio" name="auth" value="Yes" /> Yes</label>
      <label><input type="radio" name="auth" value="No" /> No</label>
    </fieldset>

    <fieldset class="field">
      <legend>Will you now or in the future require visa sponsorship?</legend>
      <label><input type="radio" name="sponsor" value="Yes" /> Yes</label>
      <label><input type="radio" name="sponsor" value="No" /> No</label>
    </fieldset>

    <div class="field">
      <label>Are you currently enrolled in an accredited university?</label>
      <button type="button" data-q="enrolled" value="Yes">Yes</button>
      <button type="button" data-q="enrolled" value="No">No</button>
    </div>

    <div class="field">
      <label for="terms">I certify that my responses are accurate</label>
      <input type="checkbox" id="terms" name="terms"
             style="position:absolute;opacity:0;width:0;height:0;" />
    </div>
  </form>

  <script>
    window.__formState = {};
    const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');

    for (const node of document.querySelectorAll('input[type=radio], input[type=checkbox]')) {
      let last = String(desc.get.call(node));
      Object.defineProperty(node, 'checked', {
        configurable: true,
        get() { return desc.get.call(this); },
        set(v) { last = String(v); desc.set.call(this, v); }
      });
      node.__committed = () => {
        const now = String(desc.get.call(node));
        if (now === last) return false;
        last = now;
        return true;
      };
    }

    const commit = (e) => {
      const t = e.target;
      if (t.tagName === 'INPUT' && t.__committed && t.__committed()) {
        window.__formState[t.name] = t.checked ? (t.value || 'on') : null;
      }
    };
    document.addEventListener('click', commit);
    document.addEventListener('change', commit);

    document.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-q]');
      if (!b) return;
      window.__formState[b.dataset.q] = b.value;
      for (const sib of b.parentElement.querySelectorAll('button[data-q]')) {
        sib.setAttribute('aria-pressed', String(sib === b));
      }
    });
  </script>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_choice_controls_commit_state_in_tracked_forms():
    """Radios, checkboxes and choice buttons must register with the page, not just look ticked."""
    profile = load_test_profile()
    adapter = LeverAdapter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(TRACKED_FORM_HTML)

        await adapter.smart_fill_form(page=page, frame=page.main_frame, profile=profile)

        state = await page.evaluate("() => window.__formState")
        assert state.get("auth") == "Yes"
        assert state.get("sponsor") == "No"
        assert state.get("enrolled") == "Yes"
        assert state.get("terms") == "on"

        # markers used to address buttons must not be left behind on the reviewed page
        assert await page.query_selector("[data-jobstager-choice]") is None
        await browser.close()


# A location picker of the kind that caused the blank field: it accepts only its own
# options and silently discards anything else the moment the input loses focus.
def strict_picker_html(known_options: str) -> str:
    return """
<!DOCTYPE html>
<html>
<head><title>Picker</title></head>
<body>
  <form id="picker-form">
    <div class="field">
      <label for="loc">Current Location</label>
      <input type="text" id="loc" role="combobox" autocomplete="off" />
      <div id="menu" role="listbox" style="display:none;"></div>
      <div class="select__single-value" id="committed"></div>
    </div>
  </form>
  <script>
    window.__submitted = null;
    document.getElementById('picker-form').addEventListener('submit', () => { window.__submitted = true; });
    const KNOWN = __OPTIONS__;
    const input = document.getElementById('loc');
    const menu = document.getElementById('menu');
    const committed = document.getElementById('committed');
    window.__picked = null;

    input.addEventListener('input', () => {
      const q = input.value.trim().toLowerCase();
      menu.innerHTML = '';
      const hits = q ? KNOWN.filter(o => o.toLowerCase().includes(q)) : [];
      for (const o of hits) {
        const d = document.createElement('div');
        d.setAttribute('role', 'option');
        d.textContent = o;
        // Real pickers commit on mousedown, before the input's blur can tear the menu down.
        d.addEventListener('mousedown', (e) => {
          e.preventDefault();
          window.__picked = o;
          committed.textContent = o;
          input.value = '';
          menu.style.display = 'none';
          menu.innerHTML = '';
        });
        menu.appendChild(d);
      }
      menu.style.display = hits.length ? 'block' : 'none';
    });

    // The behaviour under test: free text is thrown away on blur.
    input.addEventListener('blur', () => {
      input.value = '';
      menu.style.display = 'none';
      menu.innerHTML = '';
    });
  </script>
</body>
</html>
""".replace("__OPTIONS__", known_options)


@pytest.mark.asyncio
async def test_combobox_reports_failure_when_nothing_matches():
    """An unmatched query must be reported, not counted as a filled field."""
    profile = load_test_profile()
    adapter = LeverAdapter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(strict_picker_html('["Berlin, Germany", "Tokyo, Japan"]'))

        fields_filled, _ = await adapter.smart_fill_form(
            page=page, frame=page.main_frame, profile=profile
        )

        assert await page.evaluate("() => window.__picked") is None
        assert fields_filled == 0
        assert adapter.needs_attention, "unmatched dropdown should be flagged"
        assert await page.query_selector("[data-jobstager-attention]") is not None
        assert await page.input_value("#loc") == ""

        await adapter.inject_review_banner(page=page, fields_filled=0)
        assert "need you" in await (await page.query_selector("#job-stager-toolbar")).inner_text()

        await browser.close()


@pytest.mark.asyncio
async def test_combobox_falls_back_to_a_narrower_query():
    """When the broad phrasing matches nothing, the narrower candidate should still land."""
    profile = load_test_profile()
    adapter = LeverAdapter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        # Offers the city but not "Columbus, OH" and not the country.
        await page.set_content(strict_picker_html('["Columbus Metro Area", "Austin Metro Area"]'))

        fields_filled, _ = await adapter.smart_fill_form(
            page=page, frame=page.main_frame, profile=profile
        )

        assert await page.evaluate("() => window.__picked") == "Columbus Metro Area"
        assert fields_filled == 1
        assert not adapter.needs_attention
        # Staging must never send the application, whatever the widget does with Enter.
        assert await page.evaluate("() => window.__submitted") is None

        await browser.close()


# Every option list here is worded the way a real ATS words it rather than as bare
# Yes/No, because the resolver matches ranked answers against what a form offers and
# that matching is what a substring check used to get wrong.
SELECT_FORM_HTML = """
<!DOCTYPE html>
<html>
<head><title>Dropdown Form</title></head>
<body>
  <form id="select-form">
    <div class="field">
      <label for="auth">Are you legally authorized to work in the United States?</label>
      <select id="auth" name="auth">
        <option value="">Select...</option>
        <option value="y">Yes, I am authorized to work in the US</option>
        <option value="n">No, I am not authorized</option>
      </select>
    </div>
    <div class="field">
      <label for="sponsor">Will you now or in the future require visa sponsorship?</label>
      <select id="sponsor" name="sponsor">
        <option value="">Select...</option>
        <option value="y">Yes</option>
        <option value="n">No</option>
      </select>
    </div>
    <div class="field">
      <label for="grad">In what year will you graduate?</label>
      <select id="grad" name="grad">
        <option value="">Select...</option>
        <option value="2027">2027</option>
        <option value="2028">2028</option>
        <option value="2029">2029</option>
      </select>
    </div>
    <div class="field">
      <label for="vet">Protected Veteran Status</label>
      <select id="vet" name="vet">
        <option value="">Select...</option>
        <option value="prot">I identify as one or more of the classifications of a protected veteran</option>
        <option value="notprot">I am not a protected veteran</option>
        <option value="decline">I do not wish to answer</option>
      </select>
    </div>
    <div class="field">
      <label for="country">Country of Residence</label>
      <select id="country" name="country">
        <option value="">Select...</option>
        <option value="ca">Canada</option>
        <option value="us">United States of America</option>
      </select>
    </div>
    <div class="field">
      <label for="edu">Highest level of education completed</label>
      <select id="edu" name="edu">
        <option value="">Select...</option>
        <option value="hs">High School</option>
        <option value="ba">Bachelor's Degree</option>
        <option value="ma">Master's Degree</option>
      </select>
    </div>
  </form>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_dropdowns_match_the_wording_a_form_actually_offers():
    profile = load_test_profile()
    adapter = GreenhouseAdapter()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(SELECT_FORM_HTML)

        await adapter.smart_fill_form(page=page, frame=page.main_frame, profile=profile)

        assert await page.input_value("#auth") == "y"
        assert await page.input_value("#sponsor") == "n"
        assert await page.input_value("#grad") == str(profile.education.graduation_year)
        assert await page.input_value("#vet") == "notprot"
        assert await page.input_value("#country") == "us"
        assert await page.input_value("#edu") == "ba"
        await browser.close()
