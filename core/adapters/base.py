"""Base Playwright staging adapter for browser autofill and human review."""

from __future__ import annotations

from abc import ABC, abstractmethod
import html
import logging
from pathlib import Path
import re
from typing import Any, Dict, Optional
from pydantic import BaseModel
from playwright.async_api import Page

from core.config.schema import CandidateProfile
from core.scrapers.base import ATSProvider
from core.solver.questions import Kind, QKey
from core.solver.resolver import AnswerResolver, pick_option

logger = logging.getLogger(__name__)


class StagingResult(BaseModel):
    success: bool
    provider: ATSProvider
    url: str
    fields_filled: int = 0
    resume_attached: bool = False
    grad_year: Optional[int] = None
    message: str = ""
    error: Optional[str] = None


class BaseStagingAdapter(ABC):
    """Abstract staging adapter that uses Playwright to pre-fill an ATS application form."""

    provider: ATSProvider = ATSProvider.UNKNOWN

    @abstractmethod
    async def stage(
        self,
        page: Page,
        url: str,
        profile: CandidateProfile,
        answers: Optional[Dict[str, str]] = None,
    ) -> StagingResult:
        """Navigate to application, fill all fields, attach resume, and show review banner."""
        pass

    async def smooth_scroll_page(self, page: Page) -> None:
        """Progressively scroll down the page to trigger lazy component mounting."""
        try:
            await page.evaluate("""async () => {
                for (let i = 0; i < 8; i++) {
                    window.scrollBy(0, 500);
                    await new Promise(r => setTimeout(r, 40));
                }
            }""")
            await page.wait_for_timeout(200)
        except Exception as e:
            logger.debug(f"Smooth scroll encountered non-critical error: {e}")

    async def install_submit_guard(self, page: Page) -> None:
        """Block form submission while filling.

        Selecting from a dropdown with the keyboard ends in Enter, which in a plain form
        submits it. Staging must never send an application -- the person reviews and submits.
        """
        try:
            await page.evaluate("""() => {
                if (window.__jobStagerGuard) return;
                window.__jobStagerBlockedSubmits = 0;
                window.__jobStagerGuard = (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    window.__jobStagerBlockedSubmits += 1;
                };
                document.addEventListener('submit', window.__jobStagerGuard, true);
            }""")
        except Exception as err:
            logger.debug(f"Could not install submit guard: {err}")

    async def remove_submit_guard(self, page: Page) -> int:
        """Hand submission back to the person and report anything the guard caught."""
        try:
            return await page.evaluate("""() => {
                if (!window.__jobStagerGuard) return 0;
                document.removeEventListener('submit', window.__jobStagerGuard, true);
                window.__jobStagerGuard = null;
                return window.__jobStagerBlockedSubmits || 0;
            }""")
        except Exception as err:
            logger.debug(f"Could not remove submit guard: {err}")
            return 0

    async def native_click(self, frame: Any, el: Any) -> bool:
        """Click an element the way a person would, through Playwright's input pipeline.

        An el.click() issued inside evaluate() is an untrusted event and does not reliably
        reach a framework's delegated listeners; Playwright dispatches real input events.
        """
        try:
            await el.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        for attempt in (dict(timeout=2000), dict(timeout=1500, force=True)):
            try:
                await el.click(**attempt)
                return True
            except Exception as err:
                logger.debug(f"Click attempt {attempt} failed: {err}")
        try:
            await el.evaluate("el => el.click()")
            return True
        except Exception as err:
            logger.debug(f"DOM click fallback failed: {err}")
        return False

    async def check_input(self, frame: Any, el: Any, info: Dict[str, Any]) -> bool:
        """Tick a radio or checkbox.

        Assigning el.checked from JS also updates React's internal value tracker, so React
        sees no change, swallows the event and never updates state: the control looks ticked
        but submits empty. Every path below goes through a real click instead.
        """
        try:
            await el.scroll_into_view_if_needed(timeout=1500)
        except Exception:
            pass
        try:
            await el.check(timeout=2000)
            return True
        except Exception as err:
            logger.debug(f"check() failed, trying the label: {err}")

        # ATS forms routinely hide the real input and paint a styled label on top of it.
        label = None
        for finder in (
            lambda: frame.query_selector(f'label[for="{info["id"]}"]') if info.get('id') else None,
            lambda: el.evaluate_handle("el => el.closest('label')"),
        ):
            try:
                found = finder()
                found = await found if found is not None else None
            except Exception:
                found = None
            if found is None:
                continue
            label = found.as_element() if hasattr(found, 'as_element') else found
            if label is not None:
                break

        if label is not None and await self.native_click(frame, label):
            return True
        return await self.native_click(frame, el)

    MENU_SELECTOR = (
        '.select__menu-list, .select__menu, ._floatingContainer_d7ago_103, '
        '.ashby-application-form-input-autocomplete-popup, '
        '[role="listbox"]:not(.iti__country-list), [data-automation-id="promptOptionList"]'
    )
    OPTION_SELECTOR = (
        '.select__option, ._result_d7ago_107, '
        '.ashby-application-form-input-autocomplete-popup-result, '
        '[role="option"]:not(.iti__country), [data-automation-id="promptOption"], li[role="option"]'
    )

    async def combobox_selection(self, el: Any) -> str:
        """Read back what a combobox actually committed, '' if nothing stuck.

        Must be called after the control has been blurred. While it holds focus the typed
        query sits in el.value and is indistinguishable from a real selection -- which is
        exactly how an unmatched query passes for a filled field and then vanishes on blur.
        """
        try:
            return await el.evaluate("""el => {
                // React re-mounts inputs; a detached node still reports the value it held
                // when it was dropped, which reads as a success that no longer exists.
                if (!el.isConnected) return '';
                if (el.getAttribute('aria-invalid') === 'true') return '';
                // The input's own value is the committed selection on Ashby and Workday.
                if ((el.value || '').trim()) return el.value.trim();

                const root = el.closest(
                    '.select__control, [class*="select" i], [class*="combobox" i], '
                    + '[class*="autocomplete" i], [class*="field" i]'
                ) || el.parentElement;
                if (!root) return '';
                const candidates = root.querySelectorAll(
                    '.select__single-value, .select__multi-value__label, [class*="singleValue" i], '
                    + '[class*="selectedValue" i], [data-automation-id="selectedItem"]'
                );
                for (const c of candidates) {
                    // A highlighted row in the open dropdown is a hover state, not an answer.
                    if (c.closest('[role="listbox"], [class*="popup" i], [class*="menu" i], [class*="dropdown" i]')) continue;
                    if (c.getAttribute('role') === 'option' || c.closest('[role="option"]')) continue;
                    if (c.innerText.trim()) return c.innerText.trim();
                }
                return '';
            }""")
        except Exception as err:
            logger.debug(f"Combobox readback failed: {err}")
            return ''

    CONTROL_SELECTOR = 'input[role="combobox"], input[aria-autocomplete="list"], [role="combobox"]'

    async def control_index(self, frame: Any, el: Any) -> int:
        """Document-order position of a control among the page's comboboxes."""
        try:
            return await el.evaluate(
                "(el, sel) => Array.from(document.querySelectorAll(sel)).indexOf(el)",
                self.CONTROL_SELECTOR,
            )
        except Exception:
            return -1

    async def refind_control(self, frame: Any, label: str, index: int = -1) -> Any:
        """Re-acquire a combobox React re-mounted, by field label or document position."""
        if not label and index < 0:
            return None
        try:
            found = await frame.evaluate("""({label, index}) => {
                const els = document.querySelectorAll(
                    'input[role="combobox"], input[aria-autocomplete="list"], [role="combobox"]'
                );
                for (const el of els) {
                    const box = el.closest('[class*="Field" i], fieldset, [class*="question" i]');
                    let l = '';
                    if (el.id) {
                        const t = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                        if (t) l = t.innerText.trim();
                    }
                    if (!l && box) {
                        const t = box.querySelector('label, legend');
                        if (t) l = t.innerText.trim();
                    }
                    if (label && l === label) {
                        el.setAttribute('data-jobstager-cb', '1');
                        return true;
                    }
                }
                // Labels get re-worded or re-nested across a re-mount; position does not.
                if (index >= 0 && index < els.length) {
                    els[index].setAttribute('data-jobstager-cb', '1');
                    return true;
                }
                return false;
            }""", {"label": label, "index": index})
            if not found:
                return None
            fresh = await frame.query_selector('[data-jobstager-cb="1"]')
            if fresh is not None:
                await fresh.evaluate("el => el.removeAttribute('data-jobstager-cb')")
            return fresh
        except Exception as err:
            logger.debug(f"Could not re-find control '{label}': {err}")
            return None

    async def ensure_attached(self, frame: Any, el: Any, label: str, index: int = -1) -> Any:
        """Return a live handle for a control, re-finding it if React swapped the node."""
        try:
            if await el.evaluate("el => el.isConnected"):
                return el
        except Exception:
            pass
        fresh = await self.refind_control(frame, label, index)
        if fresh is not None:
            logger.debug(f"Re-acquired '{label}' after a re-mount")
        return fresh

    async def clear_combobox(self, page: Page, el: Any) -> None:
        """Remove a typed query the widget never matched, so it isn't left looking answered."""
        try:
            await el.click(timeout=1500)
            await page.keyboard.press("ControlOrMeta+a")
            await page.keyboard.press("Backspace")
        except Exception as err:
            logger.debug(f"Could not clear combobox text: {err}")
        try:
            await el.evaluate("el => el.blur()")
        except Exception:
            pass

    async def fill_combobox(self, page: Page, frame: Any, el: Any, text: Any, label: str = "") -> bool:
        """Type into a React-Select / Ashby / Workday dropdown and confirm a real selection.

        Accepts a single query or an ordered list of them; a location autocomplete often
        rejects the broad phrasing and accepts the narrow one, or the reverse. Returns
        whether the widget is actually holding a value afterwards, so a query that matched
        nothing is reported instead of being counted as a filled field.
        """
        candidates = [text] if isinstance(text, str) else [t for t in text if t]

        # A hidden control (a collapsed popup listbox, say) can never be typed into, and
        # without a bound every attempt would sit out Playwright's 30s actionability wait.
        try:
            if not await el.is_visible():
                return False
        except Exception:
            return False

        # Earlier fills trigger the re-render that wipes an in-progress query, so let the
        # DOM go quiet before typing rather than racing it.
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        await page.wait_for_timeout(400)
        control_idx = await self.control_index(frame, el)

        # A query that offers no options gets no keyboard retry: there is nothing to pick.
        attempts = [(c, mode) for c in candidates for mode in ('click', 'keyboard')]
        barren: set[str] = set()

        for candidate, mode in attempts:
            if mode == 'keyboard' and candidate in barren:
                continue
            try:
                el = await self.ensure_attached(frame, el, label, control_idx)
                if el is None:
                    logger.debug(f"Control '{label}' vanished and could not be re-found")
                    return False
                await el.scroll_into_view_if_needed(timeout=1500)
                await el.click(timeout=2500)
                await page.wait_for_timeout(150)

                # Filling earlier fields can trigger a re-render that wipes the query while it
                # is still being typed, leaving every later step to work on an empty field.
                typed = ''
                for attempt in range(3):
                    await page.keyboard.type(candidate, delay=20)
                    await page.wait_for_timeout(250)
                    typed = await el.evaluate("el => el.value || ''")
                    if typed.strip():
                        break
                    logger.debug(f"Query '{candidate}' was cleared while typing; retrying ({attempt + 1}/3)")
                    try:
                        if not await el.evaluate("el => el.isConnected"):
                            fresh = await self.refind_control(frame, label, control_idx)
                            if fresh is None:
                                break
                            el = fresh
                            logger.debug(f"Re-acquired '{label}' after a re-mount")
                        await el.click(timeout=2000)
                        await page.wait_for_timeout(200)
                    except Exception:
                        break
                if not typed.strip():
                    logger.debug(f"Could not get '{candidate}' to stay in the field")
                    continue

                option_count = 0
                waited = 0
                while waited < 2500:
                    option_count = await page.evaluate(
                        """([menuSel, optSel]) => {
                            const menu = document.querySelector(menuSel);
                            const scope = menu || document;
                            return scope.querySelectorAll(optSel).length;
                        }""",
                        [self.MENU_SELECTOR, self.OPTION_SELECTOR],
                    )
                    if option_count:
                        break
                    await page.wait_for_timeout(120)
                    waited += 120

                if option_count and mode == 'keyboard':
                    await page.keyboard.press("ArrowDown")
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(300)
                elif option_count:
                    # Mark the best option rather than clicking it here: a .click() issued
                    # inside evaluate() is untrusted and Ashby's result rows ignore it.
                    token = await page.evaluate("""([target, menuSel, optSel]) => {
                        const menu = document.querySelector(menuSel);
                        const searchScope = menu || document;
                        const opts = Array.from(searchScope.querySelectorAll(optSel));
                        if (!opts.length) return null;

                        const norm = s => (s || '').trim().toLowerCase();
                        const targetLower = norm(target);
                        const head = targetLower.split(',')[0].trim();

                        const scored = (opt) => {
                            const t = norm(opt.innerText);
                            if (targetLower === 'no') {
                                if (t === 'no' || t.startsWith('no ') || t.includes('not a protected veteran') || t.includes('do not have a disability')) return 100;
                                return -1;
                            }
                            if (targetLower === 'yes') {
                                if (t === 'yes' || t.startsWith('yes ') || t.includes('authorized to work')) return 100;
                                return -1;
                            }
                            if (targetLower === 'male') {
                                if (t === 'male' || t === 'man' || t.startsWith('male ')) return 100;
                                return -1;
                            }
                            if (targetLower === 'asian') return t.startsWith('asian') ? 100 : -1;
                            if (t === targetLower) return 100;
                            // "Houston, TX" must reach "Houston, Texas, United States": the
                            // leading segment is the part a geo lookup actually echoes back.
                            if (head && t.startsWith(head)) return 80;
                            if (t.includes(targetLower)) return 60;
                            if (head && t.includes(head)) return 40;
                            return -1;
                        };

                        let best = null, bestScore = 0;
                        for (const opt of opts) {
                            const sc = scored(opt);
                            if (sc > bestScore) { best = opt; bestScore = sc; }
                        }
                        if (!best) {
                            best = searchScope.querySelector('.select__option--is-focused, ._active_d7ago_88, [role="option"][aria-selected="true"]') || opts[0];
                        }
                        if (!best) return null;
                        const tok = 'opt' + Date.now();
                        best.setAttribute('data-jobstager-opt', tok);
                        return tok;
                    }""", [candidate, self.MENU_SELECTOR, self.OPTION_SELECTOR])

                    clicked = False
                    if token:
                        opt_el = await page.query_selector(f"[data-jobstager-opt='{token}']")
                        if opt_el is not None:
                            clicked = await self.native_click(frame, opt_el)
                    if not clicked:
                        await page.keyboard.press("ArrowDown")
                        await page.keyboard.press("Enter")
                    await page.wait_for_timeout(300)



                # Blur first: only then can a committed selection be told apart from a query
                # the widget is about to discard.
                try:
                    await el.evaluate("el => el.blur()")
                except Exception:
                    pass
                # Ashby clears an unmatched query ~300ms after blur; reading sooner than that
                # returns the doomed text and reports a fill that is about to disappear.
                await page.wait_for_timeout(700)

                if await self.combobox_selection(el):
                    return True

                if not option_count:
                    barren.add(candidate)
                logger.debug(f"Combobox kept nothing for '{candidate}' via {mode} ({option_count} options offered)")
                el = await self.ensure_attached(frame, el, label, control_idx)
                if el is None:
                    continue
                await self.clear_combobox(page, el)
            except Exception as e:
                logger.debug(f"Combobox fill error on '{candidate}': {e}")

        return False

    async def flag_for_attention(self, el: Any, label: str) -> None:
        """Outline a control this run could not answer, so it is obvious during review."""
        if not hasattr(self, 'needs_attention'):
            self.needs_attention: list[str] = []
        if label and label not in self.needs_attention:
            self.needs_attention.append(label)
        try:
            await el.evaluate("""el => {
                const box = el.closest('.select__control, [class*="field" i], [class*="question" i]') || el.parentElement;
                if (!box) return;
                box.style.outline = '2px solid #f85149';
                box.style.outlineOffset = '2px';
                box.style.borderRadius = '4px';
                box.setAttribute('data-jobstager-attention', '1');
            }""")
        except Exception as err:
            logger.debug(f"Could not flag field for attention: {err}")

    async def smart_fill_form(
        self,
        page: Page,
        frame: Any,
        profile: CandidateProfile,
        answers: Optional[Dict[str, str]] = None,
        grad_year: Optional[int] = None,
    ) -> tuple[int, bool]:
        """Intelligently scan and pre-fill all form controls across multiple progressive passes."""
        answers = answers or {}

        # Auto-detect target graduation cohort from posting text if candidate has multiple cohorts configured
        from core.config.grad_detector import detect_grad_year

        available_cohorts = profile.resumes.get_available_years()
        target_grad_year = grad_year
        if not target_grad_year and len(available_cohorts) > 1:
            try:
                page_title = await page.title()
                page_text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 8000) : ''")
                detected_yr, reason = detect_grad_year(
                    f"{page_title}\n{page_text}",
                    available_years=available_cohorts,
                    default_year=profile.education.graduation_year,
                )
                target_grad_year = detected_yr
                logger.info(f"Auto-detected graduation cohort for posting: {detected_yr} ({reason})")
            except Exception as err:
                logger.debug(f"Grad year auto-detection error: {err}")
                target_grad_year = profile.education.graduation_year
        elif not target_grad_year:
            target_grad_year = profile.education.graduation_year

        profile.education.graduation_year = target_grad_year
        grad_year = target_grad_year
        # Every control type below asks this one object what to put in a field, so a
        # question means the same thing whether the form renders it as a pill, a
        # radio, a dropdown or a text box.
        resolver = AnswerResolver(profile, grad_year=grad_year, answers=answers)
        fields_filled = 0
        resume_attached = False
        processed_elements: set[str] = set()
        self.needs_attention: list[str] = []

        async def fill_current_pass() -> int:
            nonlocal fields_filled, resume_attached
            pass_filled = 0

            # Gather frames, skipping external trackers and recaptcha frames
            frames_to_check = [frame]
            if frame == page.main_frame:
                for f in page.frames:
                    f_url = f.url.lower()
                    if f != page.main_frame and not any(bad in f_url for bad in ('recaptcha', 'google', 'doubleclick', 'segment', 'analytics', 'datadog')):
                        frames_to_check.append(f)

            for current_frame in frames_to_check:
                # 0. Choice Button Groups (Yes/No buttons, segmented options, Ashby option pills, Workday buttons)
                try:
                    button_groups = await current_frame.evaluate("""() => {
                        const candidates = [];
                        document.querySelectorAll('[data-jobstager-choice]').forEach(e => e.removeAttribute('data-jobstager-choice'));
                        const containers = Array.from(document.querySelectorAll('fieldset, [class*="question" i], [class*="field" i], [role="group"], [role="radiogroup"], .application-question'));
                        // Reverse document order visits a descendant before its ancestor, so the
                        // tightest container wins and a page-level wrapper cannot lump every
                        // question's buttons into one bogus group.
                        containers.reverse();
                        const claimed = new Set();
                        let groupIdx = 0;
                        for (const container of containers) {
                            const qEl = container.querySelector('legend, label, [class*="label" i], h3, h4, [class*="title" i]');
                            const qText = (qEl ? qEl.innerText : '').trim();
                            if (!qText) continue;

                            const btns = Array.from(container.querySelectorAll('button, [role="button"], [role="radio"]')).filter(b => {
                                const txt = (b.innerText || '').trim().toLowerCase();
                                if (!txt || txt.length > 60) return false;
                                if (txt.includes('upload') || txt.includes('attach') || txt.includes('submit') || txt.includes('apply') || txt.includes('dropbox') || txt.includes('google drive') || txt.includes('manually')) return false;
                                // A <button> in a form defaults to type=submit; clicking one sends
                                // the application instead of answering the question.
                                if (b.tagName === 'BUTTON' && b.type === 'submit' && b.form) return false;
                                return true;
                            });
                            if (btns.length < 2) continue;
                            if (btns.every(b => claimed.has(b))) continue;
                            btns.forEach(b => claimed.add(b));

                            const btnList = btns.map((b, idx) => {
                                const token = groupIdx + '-' + idx;
                                b.setAttribute('data-jobstager-choice', token);
                                const cls = typeof b.className === 'string' ? b.className : '';
                                return {
                                    idx: idx,
                                    token: token,
                                    text: (b.innerText || '').trim(),
                                    active: b.getAttribute('aria-checked') === 'true'
                                        || b.getAttribute('aria-pressed') === 'true'
                                        || b.getAttribute('aria-selected') === 'true'
                                        || b.dataset.state === 'checked'
                                        || /(^|[\\s_-])active([\\s_-]|$)/i.test(cls)
                                        || /_active_/i.test(cls)
                                };
                            });
                            groupIdx += 1;
                            candidates.push({ question: qText, buttons: btnList });
                        }
                        return candidates;
                    }""")

                    for bg in button_groups:
                        texts = [b['text'] for b in bg['buttons']]
                        bg_ans = resolver.resolve(bg['question'], Kind.BUTTON, offered=texts)
                        chosen = pick_option(bg_ans.candidates, texts) if bg_ans else None
                        target_btn_idx = texts.index(chosen) if chosen is not None else None

                        if target_btn_idx is not None:
                            target_info = bg['buttons'][target_btn_idx]
                            btn_key = f"btn_{bg['question'][:20]}_{target_info['text']}"
                            if not target_info['active'] and btn_key not in processed_elements:
                                btn_el = await current_frame.query_selector(
                                    f"[data-jobstager-choice='{target_info['token']}']"
                                )
                                if btn_el is not None and await self.native_click(current_frame, btn_el):
                                    fields_filled += 1
                                    pass_filled += 1
                                    processed_elements.add(btn_key)
                                    logger.info(f"Clicked choice button '{target_info['text']}' for '{bg['question'][:40]}'")
                                else:
                                    logger.warning(f"Could not click '{target_info['text']}' for '{bg['question'][:40]}'")
                except Exception as err:
                    logger.debug(f"Choice button pass error: {err}")

                try:
                    inputs = await current_frame.query_selector_all("input, textarea, select, [role='combobox'], [role='listbox'], .select__control, button[aria-haspopup='listbox'], button[data-automation-id*='dropdown'], button[data-automation-id*='select']")
                except Exception:
                    continue

                for el in inputs:
                    try:
                        info = await el.evaluate("""el => {
                            // Only ever read a <label> element. Falling back to a container's
                            // innerText hands back the whole block, and reaching for a sibling
                            // container hands back the previous field's label -- both of which
                            // sent values into the wrong inputs.
                            let labelText = '';
                            const id = el.id;
                            if (id) {
                                const l = document.querySelector('label[for="' + CSS.escape(id) + '"]');
                                if (l) labelText = l.innerText;
                            }
                            if (!labelText && el.getAttribute('aria-labelledby')) {
                                const l = document.getElementById(el.getAttribute('aria-labelledby'));
                                if (l) labelText = l.innerText;
                            }
                            if (!labelText && el.getAttribute('aria-label')) {
                                labelText = el.getAttribute('aria-label');
                            }
                            if (!labelText) {
                                const own = el.closest('label');
                                if (own) labelText = own.innerText;
                            }
                            if (!labelText) {
                                // Climb to the nearest wrapper that holds this control alone;
                                // a wrapper around several fields cannot name any one of them.
                                let n = el.parentElement;
                                for (let i = 0; i < 6 && n; i++) {
                                    const others = Array.from(
                                        n.querySelectorAll('input:not([type=hidden]), textarea, select')
                                    ).filter(c => c !== el && c.type !== 'radio' && c.type !== 'checkbox');
                                    if (others.length) break;
                                    // Lever and some custom forms name a field with a div
                                    // carrying a label-ish class rather than a <label>.
                                    const l = n.querySelector(
                                        'label, legend, .application-label, .card-field-title, [class*="label" i]'
                                    );
                                    if (l && l.innerText.trim()) { labelText = l.innerText; break; }
                                    n = n.parentElement;
                                }
                            }

                            // For a choice control the label above names the option, not the
                            // question; the question lives on the group wrapper that encloses
                            // every option, so climb to that boundary and read its legend.
                            let questionText = '';
                            let n = el.parentElement;
                            for (let i = 0; i < 8 && n; i++) {
                                const cls = (n.className || '').toString();
                                const isBoundary = n.tagName === 'FIELDSET'
                                    || n.getAttribute('role') === 'radiogroup'
                                    || n.getAttribute('role') === 'group'
                                    || /fieldentry|question|application-field/i.test(cls);
                                if (isBoundary) {
                                    const q = n.querySelector('legend, label, [class*="label" i], h3, h4');
                                    if (q && q.innerText.trim()) questionText = q.innerText;
                                    break;
                                }
                                n = n.parentElement;
                            }
                            if (!questionText) {
                                const container = el.closest('fieldset, [class*="question" i], [class*="field" i], [data-test*="field"], .application-question');
                                if (container) {
                                    const qEl = container.querySelector('legend, label, [class*="label" i], h3, h4');
                                    if (qEl) questionText = qEl.innerText;
                                }
                            }
                            if (!questionText) questionText = labelText;

                            let optText = '';
                            if (el.id) {
                                const ol = document.querySelector('label[for="' + el.id + '"]');
                                if (ol) optText = ol.innerText;
                            }
                            if (!optText && el.parentElement) {
                                optText = el.parentElement.innerText;
                            }

                            return {
                                label: labelText || '',
                                name: el.name || '',
                                placeholder: el.placeholder || '',
                                type: el.type || '',
                                id: el.id || '',
                                tag: el.tagName.toLowerCase(),
                                aria: el.getAttribute('aria-label') || '',
                                role: el.getAttribute('role') || '',
                                className: el.className || '',
                                automationId: el.getAttribute('data-automation-id') || '',
                                question: questionText || '',
                                optText: optText || '',
                                value: el.value || '',
                                checked: el.checked || false
                            };
                        }""")

                        el_key = f"{info['tag']}_{info['id']}_{info['name']}_{info['label'][:20]}"
                        if el_key in processed_elements:
                            continue

                        desc = f"{info['label']} {info['name']} {info['placeholder']} {info['id']} {info['aria']} {info.get('automationId', '')} {info.get('className', '')}".lower()
                        q_desc = f"{info.get('question', '')} {desc}".lower()
                        opt_desc = f"{info.get('optText', '')} {info.get('value', '')}".lower().strip()
                        t = info['type']
                        tag = info['tag']
                        role = info.get('role', '')

                        if t in ('hidden', 'submit', 'search') or any(bad in desc for bad in ('recaptcha', 'cf-turnstile', 'hcaptcha', 'g-recaptcha')):
                            continue

                        # 1. File Upload (Resume / CV)
                        if t == 'file':
                            if re.search(r'\b(resume|cv|autofill|file)\b', desc) and not any(k in desc for k in ['cover', 'transcript', 'portfolio', 'writing']):
                                res_file = profile.resumes.resolve_resume(grad_year)
                                if res_file and res_file.exists():
                                    await el.set_input_files(str(res_file))
                                    resume_attached = True
                                    fields_filled += 1
                                    pass_filled += 1
                                    processed_elements.add(el_key)
                                    logger.info(f"Attached resume to file input: {info['id'] or info['name']}")
                            continue

                        # 2. Radio Buttons
                        if t == 'radio':
                            radio_ans = resolver.resolve(q_desc, Kind.RADIO)
                            target_checked = bool(radio_ans) and pick_option(
                                radio_ans.candidates, [opt_desc]
                            ) is not None

                            if target_checked and not info['checked']:
                                if await self.check_input(current_frame, el, info):
                                    fields_filled += 1
                                    pass_filled += 1
                                    processed_elements.add(el_key)
                                    logger.info(f"Checked radio button for '{info['question'][:30]}': {opt_desc}")
                                else:
                                    logger.warning(f"Could not check radio for '{info['question'][:30]}': {opt_desc}")
                            continue

                        # 3. Checkboxes (Agreements, policies, declarations, demographics, Yes/No boxes)
                        if t == 'checkbox':
                            cb_ans = resolver.resolve(q_desc, Kind.CHECKBOX)
                            if cb_ans.is_consent or not opt_desc:
                                # Either the label is the agreement itself, or there is no
                                # label at all; either way the question is the whole of
                                # what the box asks and ticking it is the answer.
                                target_check = bool(cb_ans)
                            elif cb_ans:
                                target_check = pick_option(cb_ans.candidates, [opt_desc]) is not None
                            else:
                                # Some forms list availability as a bare row of boxes with
                                # no question above them; the label is all there is to read.
                                target_check = resolver.wants_term_option(opt_desc)

                            if target_check and not info['checked']:
                                if await self.check_input(current_frame, el, info):
                                    fields_filled += 1
                                    pass_filled += 1
                                    processed_elements.add(el_key)
                                    logger.info(f"Checked checkbox '{info['label'][:30] or info['name']}'")
                                else:
                                    logger.warning(f"Could not check checkbox '{info['label'][:30] or info['name']}'")
                            continue

                        # 4. Select Dropdowns (Standard HTML <select>)
                        if tag == 'select':
                            opts = await el.query_selector_all('option')
                            opt_texts = [(await o.inner_text()).strip() for o in opts]
                            sel_answer = resolver.resolve(q_desc, Kind.SELECT, offered=opt_texts)
                            target_opt = pick_option(sel_answer.candidates, opt_texts)

                            if target_opt is not None:
                                opt = opts[opt_texts.index(target_opt)]
                                val = await opt.get_attribute('value')
                                if val is not None:
                                    await el.select_option(val)
                                    fields_filled += 1
                                    pass_filled += 1
                                    processed_elements.add(el_key)
                                    logger.info(f"Selected option in dropdown {info['id'] or info['name']}: {target_opt}")
                            continue

                        # 5. Autocomplete / React-Select / Combobox / Custom Dropdown Controls
                        is_combobox = (
                            (tag == 'input' and (
                                role == 'combobox'
                                or 'select__input' in info.get('className', '')
                                or 'selectized' in info.get('className', '')
                                or 'autocomplete' in info.get('className', '')
                                or info['id'] in ('country', 'candidate-location', 'location-input', 'gender', 'hispanic_ethnicity', 'veteran_status', 'disability_status')
                                or 'react-select' in info['id']
                                or info.get('ariaHasPopup') == 'listbox'
                            )) or (
                                tag in ('button', 'div') and (
                                    role in ('combobox', 'listbox')
                                    or info.get('ariaHasPopup') == 'listbox'
                                    or 'select__control' in info.get('className', '')
                                    or 'dropdown' in info.get('className', '').lower()
                                    or 'dropdown' in info.get('automationId', '').lower()
                                ) and not any(k in desc for k in ('upload', 'attach', 'submit', 'apply', 'dropbox'))
                            )
                        )

                        if is_combobox:
                            try:
                                if not await el.is_visible():
                                    continue
                                # A rendered option list is the result of filling a combobox,
                                # not a target to fill; flagging one is a false alarm.
                                if await el.evaluate(
                                    """el => !!el.querySelector('[role="option"], .select__option')"""
                                ):
                                    continue
                            except Exception:
                                continue

                            already = await self.combobox_selection(el)
                            if already:
                                processed_elements.add(el_key)
                                continue

                            cb_answer = resolver.resolve(q_desc, Kind.COMBOBOX)
                            cb_text = cb_answer.candidates or None
                            if cb_answer.needs_attention:
                                await self.flag_for_attention(
                                    el, (info.get('question') or info['label']
                                         or 'Unnamed dropdown')[:60]
                                )

                            if cb_text:
                                ok = await self.fill_combobox(
                                    page, current_frame, el, cb_text,
                                    label=(info['label'] or info.get('question') or '').strip(),
                                )
                                q_label = (info.get('question') or info['label'] or info['name'] or info['id']).strip()
                                if ok:
                                    fields_filled += 1
                                    pass_filled += 1
                                    processed_elements.add(el_key)
                                    logger.info(f"Filled combobox {info['id'] or info['name']} -> {cb_text}")
                                else:
                                    logger.warning(
                                        f"Combobox matched no option for '{q_label[:60]}' "
                                        f"(tried {cb_text}) -- left blank and flagged"
                                    )
                                    await self.flag_for_attention(el, q_label[:60] or 'Unnamed dropdown')
                                    # It was visible and every candidate missed; the second
                                    # pass would only pay the same lookup waits again.
                                    processed_elements.add(el_key)
                                # Never fall through to the text path: typing into a dropdown
                                # that rejects free text is what leaves it looking answered.
                                continue

                        # 6. Text inputs, email, tel, url, number, textareas
                        # Input type is stronger evidence than any label: a type=email
                        # box is an email box whatever its id says.
                        typed = {'email': 'email address', 'tel': 'phone number'}.get(t, '')
                        ans = resolver.resolve(f"{typed} {desc}".strip(), Kind.TEXT)
                        val = ans.text

                        if not val and (
                            ans.key is QKey.OPEN_RESPONSE
                            or (tag == 'textarea' and info['label'])
                        ):
                            val = resolver.open_response(desc).text

                        if ans.needs_attention and not val:
                            await self.flag_for_attention(
                                el, (info['label'] or info['name'] or 'Unnamed field')[:60]
                            )

                        if val and (t in ('text', 'email', 'tel', 'url', 'number', 'textarea', '') or tag == 'textarea'):
                            if not info['value'] or info['value'].strip() == '':
                                try:
                                    if await el.is_visible():
                                        await el.scroll_into_view_if_needed()
                                        await el.fill(val, timeout=2000)
                                        fields_filled += 1
                                        pass_filled += 1
                                        processed_elements.add(el_key)
                                        logger.info(f"Filled {info['label'][:30] or info['name']} -> {val}")
                                except Exception as err:
                                    logger.debug(f"Input fill error: {err}")

                    except Exception as e:
                        logger.debug(f"Error handling element during smart fill: {e}")

            return pass_filled

        # Scroll smoothly to bottom so all lazy sections mount and expand
        await self.smooth_scroll_page(page)

        await self.install_submit_guard(page)
        try:
            # Pass 1: Fill all mounted form elements
            await fill_current_pass()

            # Pass 2: Fill any conditional or newly rendered fields
            await page.wait_for_timeout(300)
            await fill_current_pass()

            # Pass 3: repair. A re-render can undo a write after it was made, and the
            # handle that made it is detached, so the undo is invisible to that pass.
            # Every branch here skips a control that already holds the right answer, so
            # re-running against a settled page only touches what was actually lost.
            try:
                await page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            await page.wait_for_timeout(600)
            processed_elements.clear()
            repaired = await fill_current_pass()
            if repaired:
                logger.info(f"Repair pass re-applied {repaired} field(s) undone by a re-render")
        finally:
            blocked = await self.remove_submit_guard(page)
            if blocked:
                logger.warning(f"Blocked {blocked} form submission(s) triggered while filling")

        # Drop the scan markers so the reviewed page is left as the site rendered it
        try:
            await page.evaluate(
                "() => document.querySelectorAll('[data-jobstager-choice]')"
                ".forEach(e => e.removeAttribute('data-jobstager-choice'))"
            )
        except Exception:
            pass

        # Scroll smoothly back to top so user can review the entire application from the top
        try:
            await page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
            await page.wait_for_timeout(300)
        except Exception:
            pass

        return fields_filled, resume_attached


    async def inject_review_banner(
        self,
        page: Page,
        fields_filled: int = 0,
        resume_attached: bool = False,
        company: str = "",
        role: str = "",
        grad_year: int = 2028,
    ) -> None:
        """Inject a floating sticky review banner with on-site 'Mark as Applied' popup."""
        try:
            unanswered = getattr(self, 'needs_attention', [])
            if unanswered:
                shown = '; '.join(unanswered[:3])
                if len(unanswered) > 3:
                    shown += f' +{len(unanswered) - 3} more'
                attention_html = (
                    '<span style="color:#ffa198;border-left:1px solid #30363d;padding-left:16px;" '
                    f'title="{html.escape(chr(10).join(unanswered), quote=True)}">'
                    f'&#9888; {len(unanswered)} need you: {html.escape(shown)}</span>'
                )
            else:
                attention_html = ''

            banner_script = f"""
            (() => {{
                const existing = document.getElementById('job-stager-toolbar');
                if (existing) return;

                const bar = document.createElement('div');
                bar.id = 'job-stager-toolbar';
                bar.style.cssText = `
                    position: fixed;
                    top: 14px;
                    left: 50%;
                    transform: translateX(-50%);
                    background: #07130b;
                    border: 2px solid #238636;
                    border-radius: 10px;
                    padding: 8px 16px;
                    z-index: 2147483647;
                    box-shadow: 0 12px 36px rgba(0,0,0,0.85);
                    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    font-size: 12px;
                    color: #f0f6fc;
                    display: flex;
                    align-items: center;
                    gap: 16px;
                    user-select: none;
                `;

                bar.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #28c840; box-shadow: 0 0 8px #28c840;"></span>
                        <strong style="color: #7ee787;">⚡ JobStager:</strong>
                        <span style="color: #c9d1d9;">{fields_filled} fields pre-filled{' · Resume attached' if resume_attached else ''}</span>
                        {attention_html}
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <button id="job-stager-apply-btn" style="
                            background: #238636;
                            color: #ffffff;
                            font-weight: bold;
                            font-size: 11px;
                            padding: 6px 14px;
                            border-radius: 6px;
                            border: 1px solid #2ea043;
                            cursor: pointer;
                            font-family: inherit;
                            transition: all 0.2s;
                        ">✓ Mark as Applied</button>
                        <button id="job-stager-close-btn" style="
                            background: #21262d;
                            color: #8b949e;
                            font-size: 11px;
                            padding: 6px 10px;
                            border-radius: 6px;
                            border: 1px solid #30363d;
                            cursor: pointer;
                            font-family: inherit;
                        ">✕ Close</button>
                    </div>
                `;

                document.body.appendChild(bar);

                function showSuccessModal(compName, roleName) {{
                    const existingModal = document.getElementById('job-stager-success-modal');
                    if (existingModal) return;

                    const modal = document.createElement('div');
                    modal.id = 'job-stager-success-modal';
                    modal.style.cssText = `
                        position: fixed;
                        inset: 0;
                        background: rgba(0, 0, 0, 0.75);
                        backdrop-filter: blur(4px);
                        z-index: 2147483647;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    `;

                    modal.innerHTML = `
                        <div style="
                            background: #07130b;
                            border: 2px solid #238636;
                            border-radius: 12px;
                            padding: 28px 32px;
                            max-width: 440px;
                            width: 90%;
                            text-align: center;
                            box-shadow: 0 20px 60px rgba(0,0,0,0.9);
                            color: #f0f6fc;
                        ">
                            <div style="font-size: 36px; margin-bottom: 8px;">🎉</div>
                            <div style="font-size: 16px; font-weight: bold; color: #7ee787;">Application Logged!</div>
                            <div style="font-size: 13px; color: #c9d1d9; margin-top: 8px; line-height: 1.5;">
                                Recorded directly into your Google Sheets tracker and marked as applied in your local database.
                            </div>
                            <div style="
                                margin-top: 14px;
                                background: #030905;
                                border: 1px solid #163d22;
                                border-radius: 8px;
                                padding: 10px 14px;
                                text-align: left;
                                font-size: 11px;
                                color: #8b949e;
                                line-height: 1.6;
                            ">
                                <div><span style="color:#7ee787;">Company:</span> <span style="color:#f0f6fc;">${{compName || '{company or "Employer"}'}}</span></div>
                                <div><span style="color:#7ee787;">Role:</span> <span style="color:#f0f6fc;">${{roleName || '{role or "Internship"}'}}</span></div>
                                <div><span style="color:#7ee787;">Stage:</span> <span style="color:#e3b341;">Applied (May {grad_year} Resume)</span></div>
                            </div>
                            <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: center;">
                                <button id="job-stager-modal-done-btn" style="
                                    background: #238636;
                                    color: #ffffff;
                                    font-weight: bold;
                                    padding: 8px 18px;
                                    border-radius: 6px;
                                    border: none;
                                    cursor: pointer;
                                    font-size: 12px;
                                    font-family: inherit;
                                ">Keep Browsing</button>
                                <button id="job-stager-modal-close-window-btn" style="
                                    background: #21262d;
                                    color: #c9d1d9;
                                    padding: 8px 16px;
                                    border-radius: 6px;
                                    border: 1px solid #30363d;
                                    cursor: pointer;
                                    font-size: 12px;
                                    font-family: inherit;
                                ">Close Window</button>
                            </div>
                        </div>
                    `;

                    document.body.appendChild(modal);

                    document.getElementById('job-stager-modal-done-btn').addEventListener('click', () => {{
                        modal.remove();
                    }});

                    document.getElementById('job-stager-modal-close-window-btn').addEventListener('click', () => {{
                        if (window.jobStagerCloseWindow) {{
                            window.jobStagerCloseWindow();
                        }} else {{
                            window.close();
                        }}
                    }});
                }}

                let applied = false;
                async function doMarkApplied() {{
                    if (applied) return;
                    applied = true;
                    const btn = document.getElementById('job-stager-apply-btn');
                    if (btn) {{
                        btn.innerHTML = '<span>⚙ Logging...</span>';
                        btn.disabled = true;
                    }}

                    try {{
                        if (window.jobStagerMarkApplied) {{
                            const res = await window.jobStagerMarkApplied();
                            showSuccessModal(res?.company, res?.title);
                        }} else {{
                            showSuccessModal('{company}', '{role}');
                        }}
                        if (btn) {{
                            btn.innerHTML = '<span>✓ Logged</span>';
                            btn.style.background = '#2ea043';
                        }}
                    }} catch (err) {{
                        console.error('JobStager logging error:', err);
                        if (btn) {{
                            btn.innerHTML = '<span>✕ Error</span>';
                            btn.disabled = false;
                        }}
                    }}
                }}

                document.getElementById('job-stager-apply-btn').addEventListener('click', doMarkApplied);

                document.getElementById('job-stager-close-btn').addEventListener('click', () => {{
                    if (window.jobStagerCloseWindow) {{
                        window.jobStagerCloseWindow();
                    }} else {{
                        bar.remove();
                    }}
                }});

                document.addEventListener('submit', () => {{
                    setTimeout(doMarkApplied, 500);
                }}, true);
            }})();
            """
            await page.evaluate(banner_script)
        except Exception as e:
            logger.debug(f"Could not inject review banner: {e}")

    async def safe_fill(self, page: Page, selector: str, value: str) -> bool:
        """Safely fill an input element if found."""
        try:
            element = await page.wait_for_selector(selector, timeout=2000, state="visible")
            if element:
                await element.fill(value)
                return True
        except Exception:
            pass
        return False

    async def safe_upload(self, page: Page, selector: str, file_path: Path) -> bool:
        """Safely set input files for file upload elements."""
        try:
            element = await page.wait_for_selector(selector, timeout=2000, state="attached")
            if element and file_path.exists():
                await element.set_input_files(str(file_path))
                return True
        except Exception as e:
            logger.debug(f"Failed to upload file to {selector}: {e}")
        return False
