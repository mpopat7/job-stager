# JobStager

JobStager fills out job applications for you and then stops, so you can look everything over
and press Submit yourself. It never submits on its own.

It works on Greenhouse, Ashby, Lever and Workday, which cover most tech internship and
new-grad applications.

## What using it looks like

1. **Sign in.**
2. **Fill in Settings once**: your school and graduation year, links (LinkedIn, GitHub,
   portfolio), a resume for each graduation year you're applying under, and your usual
   answers (start date, work model, preferred locations).
3. **Pick a job.** The **Jobs** tab lists open postings from hundreds of companies, and you can
   filter by platform and role. You can also paste any posting's link.
4. **Press Stage Application.** A browser window opens on the employer's real application
   page with every field already filled and the right resume attached.
5. **Review it and press Submit.** Anything JobStager wasn't sure about is highlighted in
   red. It never quietly guesses.
6. **It's tracked.** The application appears in your **Tracker**, and the job leaves your
   Jobs list.

## Getting started

JobStager runs on your own computer. You need [uv](https://docs.astral.sh/uv/) installed.

```bash
git clone https://github.com/mpopat7/job-stager.git
cd job-stager
uv run playwright install chromium
uv run uvicorn web.server:app --host 127.0.0.1 --port 8000
```

Then open <http://localhost:8000> and create an account. On a Mac you can double-click
`JobStager.command` instead, which starts the server and opens the page.

## The four tabs

| Tab | What it's for |
|---|---|
| **Jobs** | Open postings to apply to. Jobs you've already applied to are hidden. |
| **Matches** | Jobs that look like something already in your Google Sheet, if you connected one. A confirmed match leaves Jobs; a possible match stays in Jobs with a warning so you can decide. |
| **Tracker** | Every application you made through JobStager. |
| **Settings** | Your details, links, resumes and standard answers. |

## Things worth knowing

- **One resume per graduation year.** If a posting says "graduating 2028" or "Class of 2029",
  JobStager attaches the resume you uploaded for that year.
- **Links can be limited by year.** In Settings → Application Links you can, for example,
  send your portfolio only with 2029 applications, and choose which link goes in a generic
  "Website" box.
- **Blank beats wrong.** When a question has no answer in your Settings (a demographic
  question you skipped, a start date you didn't set), JobStager fills what it can and
  highlights the rest in red instead of making something up.
- **Your data stays with you.** Everything is stored on your machine. Self-identification
  answers are encrypted when a server key is set.
- **Google Sheets is optional.** If you already track applications in a spreadsheet,
  JobStager can add a row to it for each application.

## Coming soon

- **A Chrome extension.** You'll fill forms directly in your normal browser, with nothing to
  install on your computer beyond the extension.
- **Written answers.** Drafts for free-response questions, generated only from facts in your
  profile.

## Trouble?

- **A field was left blank or red:** add the answer in Settings and stage the job again.
- **The page didn't change after an update:** stop the server and start it again. It doesn't
  reload on its own.

For hosting JobStager for other people, see [docs/deploying.md](docs/deploying.md). The
design history and architecture are in [docs/design.md](docs/design.md).
