# User guide

[← Back to the README](../README.md) · [CV editor](editor.md) · [Job sources](sources.md) · [Screenshots](screenshots.md)

What each part of CVMaker does, in the order you would normally use it.

- [Profile](#profile)
- [Profile interview](#profile-interview)
- [Search](#search)
- [Salary](#salary)
- [Automatic search](#automatic-search)
- [Applications](#applications)
- [Background tasks](#background-tasks)
- [The job page and tailored CVs](#the-job-page-and-tailored-cvs)
- [Rate the CV](#rate-the-cv)
- [Cover letter](#cover-letter)
- [Interview prep](#interview-prep)
- [Insights](#insights)
- [Jobs list density](#jobs-list-density)
- [Multiple people](#multiple-people)
- [Add a job by link or text](#add-a-job-by-link-or-text)
- [Posting checks](#posting-checks)
- [Outreach](#outreach)
- [Interview stage](#interview-stage)
- [Offer review](#offer-review)
- [Skill check](#skill-check)
- [Chat](#chat)
- [Notifications](#notifications)
- [Backup](#backup)
- [Company logos](#company-logos)

## Profile

Where: `/profile`.

The only source of facts for your CVs. Three ways to start:

- **Import your CV**: a PDF, Word file (.docx), text file or a photo of it, in any layout. Claude maps it onto the profile; sections the profile has no field for (certifications, awards, volunteering, publications, courses, references…) are kept as **custom sections** with the CV's own headings, and extra contact lines (website, address…) are kept too.
- **Import from LinkedIn**: LinkedIn doesn't let apps read profiles, but it gives you your own data. On LinkedIn, go to **Me → Settings & Privacy → Data privacy → Get a copy of your data** and tick Profile, Positions, Education, Skills, Languages, Certifications, Projects and Job seeker preferences (or take the whole archive, up to 100 MB). Upload the zip LinkedIn emails you as it is. It's read as written, without Claude, so it costs nothing. With a profile already there, it only adds: empty fields are filled in, jobs, schools, projects, skills, languages and sections you don't have yet are added (new jobs go in date order), and your own wording is kept. Your "open to work" settings (job titles, locations, job types, industries) go into **Preferences**, replacing ones from an earlier LinkedIn import. A restore point is saved first, and the card lists what changed. LinkedIn has no skill groups, so new skills go under "Skills" for you to sort in the CV editor. The card then offers to use those settings in your job search: tick the job titles, countries and remote setting you want added to the search you use, and click **Apply**. It also offers to stop excluding a word, such as "manager", that would hide a title you asked for. Or **Save all as a new search** called "From LinkedIn". Nothing changes in your searches until you click. Locations in countries the search doesn't know are listed for you to add by hand. If your search took any job title before, adding titles limits it to those titles, and the card says so.
- **Start a blank CV for a kind of job**: software developer, Java, frontend, Python/data, DevOps/cloud, QA/manual tester, automation tester, project manager, product manager, business analyst, data analyst, UI/UX designer, IT support, or other. The category only sets the structure: its skill groups (e.g. *Automation frameworks*, *API testing*, *CI/CD* for an automation tester), extra sections such as *Certifications*, and headings. Nothing is written for you: every field is empty and labelled in the editor (*Job title*, *Company*, *What you did and the result it had*…), and you can rename, add or remove anything.
- Or edit `data/profile.json` directly.

## Profile interview

Claude asks about gaps in your profile, either in general or for a specific job. Your answers are added to the profile.

## Search

Where: `/`.

Pick one or more **kinds of job** (software developer, Java, frontend, Python/data, DevOps/cloud, QA/manual tester, automation tester, project manager, product manager, business analyst, data analyst, UI/UX designer, IT support). Each adds its usual job titles, its skills for ranking, and its own phrases for searching the job boards (e.g. "qa automation engineer", "scrum master"); an excluded word a picked kind needs ("manager" for project managers) is not applied to it. The list lives in `cvmaker/categories.py` and is the same one a blank CV starts from. Add your own title keywords, skills and countries (type and press Enter to add a chip; suggestions come from your profile) and whether remote jobs count. Exclusions, remote regions, posting age, minimum keyword score, sources and the schedule are under "Advanced". Save several **searches**, each with its own name, filters and schedule. A search runs in the background: each source is ticked off live as it answers, and the list updates when it's done. The list can be sorted by Claude fit, keyword score, job title, newly found, posted date, salary or company (click the Fit, Keywords, Job or Salary column header; click again to reverse), and exported to CSV or XLSX in the same order.

- **Claude fit rating**: after each search, Claude rates the top 40 new jobs (configurable) for fit 1-10, in batches of 5, with a one-line "✓ strong · ✗ missing" summary in the list. "Rate N with Claude" rates the unrated jobs in the current view. The full fit check on a job page replaces the quick rating.
- **Learning from your feedback**: the ✕ on a job asks why you're hiding it (too senior, too junior, wrong tech stack, wrong location, salary, not this company, not relevant). Those reasons and the jobs you like or move into the pipeline become a "taste" score per job: the **for you** sort combines Claude's fit, the keyword score and that taste, and each job shows why it moved ("↓ senior roles", "↑ Sofia"). Claude's fit ratings get the same feedback, and say when a job looks like ones you've hidden. "Not this company" hides all its jobs and blocks it from future searches (unblock on the jobs page). When a pattern repeats, e.g. 2+ jobs hidden as too senior, the jobs page offers a one-click filter change for your search.
- **Dashboard strip**: new jobs this week, applications, response rate, follow-ups due, and the median salary seen (converted to ≈ € per month: yearly/hourly amounts and other currencies are converted with fixed rates, so salaries can be compared and sorted; net and gross are not converted).

## Salary

Shown whenever a posting states one. It comes from the source's salary fields where they exist (Ashby, Lever, Recruitee, Himalayas, Jobicy, dev.bg, Adzuna, Remotive, RemoteOK, JSearch); otherwise it is picked out of the posting text (e.g. "€62,000 to €87,200", "4 500 - 6 000 лв. бруто").

## Automatic search

In a search's "Advanced" section, turn on "run it automatically every N hours". Newly found jobs are marked **NEW** until you open them or press "Mark all as seen"; the header shows how many there are, and a desktop notification is sent (`notify-send`, desktop mode). Schedules run while the app is open (server mode: in the worker). To search while it is closed, add a cron job, e.g. `0 */6 * * * cd /path/to/CVMaker && uv run python -m cvmaker.auto` (runs every scheduled search, then rates the new jobs).

- **Application packs**: after each search (scheduled or not), the jobs with a keyword score of at least 80 that have no pack yet get one, up to 3 per search. A pack is a fit check, a GitHub review with project ideas to build for that job, a tailored one-page CV, a matching cover letter and interview prep, made in that order (about 5 Claude calls). Each is saved in the app as usual and copied to `data/packs/<company>_<title>/` (`CV.pdf`, `CoverLetter.pdf`, `PACK.md` with the fit, gaps, projects to build, action plan and interview prep). Change the score, the number per search or switch it off under "Schedules & Claude rating" (stored in `data/prefs.json` as `pack_min_score`, `pack_max`, `pack_after_search`). Any job can get one with **Make application pack** on its page. Ready packs are listed on the jobs page.
- **Telegram** (desktop mode): set it up under Settings → Telegram by creating a bot with @BotFather, pasting its token and tapping Start on the one-time link. Each day at your chosen time the bot sends new jobs at or above your chosen Claude rating (default 7/10) and any follow-ups due, each job with Open, Hide, Tailor CV and Suggest changes buttons. Tailor CV sends the PDF with what changed and anything the checks couldn't back up. Write to the bot like the chat page, e.g. "shorten my summary": a profile change is only saved when you tap Apply, and can be undone. Jobs rated 9+ (your choice) are sent as soon as they're rated, silently between 22:00 and 08:00. A Cover letter button sends a letter that matches the job's latest CV. You can also send a PDF, Word file, photo or text file, with your question as the caption (same limits as the chat page). Commands: `/jobs`, `/suggest`, `/new`, `/unlink`. The bot asks Telegram for messages, so no port is opened, and it answers only the linked account. It runs only while the app is running (a summary missed while it was closed goes out when it next starts that day; on Linux, Settings → Start at login runs the app in the background from login, at `http://127.0.0.1:8000`, and `./run.sh` stops that copy first), and jobs only arrive if a saved search runs on a schedule. Job details, messages, CVs and PDFs pass through Telegram, and bot chats aren't end-to-end encrypted.

## Applications

Where: `/pipeline` and the job page.

Move a job through To apply → Applied → Screening → Interview → Offer / Rejected / Ghosted, with a date and a note; each move is kept on the job's timeline. Each job has notes, a recruiter/contact, a next date, and which CV and cover letter you sent ("I sent this CV" on a CV page). The pipeline board supports drag and drop. Jobs still at "Applied" after 10 days (`CVMAKER_FOLLOW_UP_DAYS`) show as follow-ups due, with a desktop notification. The pipeline compares response rates with and without a tailored CV sent. Jobs at a company you applied to before are flagged ("applied 2× · last: rejected").

## Background tasks

Searches, ratings and everything Claude writes (fit check, CV, letter, prep, CV rating, interview questions, CV import) run in the background, up to 4 at a time (`CVMAKER_TASK_WORKERS`). You can keep using the app; the header shows what's running, a notice appears when something finishes, and **My profile → Activity** lists every task with its job and a link to the result (**My documents** lists every CV and letter). Tasks survive a restart: one that was running when the app stopped is started again (at once on the desktop; in server mode when its worker's lease runs out), up to 3 times (`CVMAKER_TASK_ATTEMPTS`). Only a task left over from before the queue existed is marked "interrupted".

## The job page and tailored CVs

The job page is split into tabs (Overview, Fit & gaps, CV & letter, Application, Interview prep, GitHub), with a **Next step** box at the top worked out from what's done so far. The Overview shows the posting (headings and bullets recovered from the plain text) next to your progress and fit summary. Check your fit, then **generate a tailored CV**. Claude reorders and rewords your profile for the posting and renders a PDF in your CV's design. Requirements you don't meet are listed separately and are never put into the CV. With "Must fit on one page" (on by default), a CV that still runs over after the font shrinks is shortened by Claude. You can also pick the font size (8.4-10.8 pt). On the CV page:

**Deep dive into a suggested project.** Every project the fit check (*Projects that look strong on a CV*) or the GitHub check (*Project idea*) suggests has a **Deep dive** button. Pick a size in Claude Code working time (one session of about an hour, an afternoon of 3-4 hours, or a 1-2 day flagship; you review each milestone as it lands) and how you'll build it (*Claude Code builds it*, or *I build it, Claude guides* for a mentor that explains and reviews while you write the code). Claude works the project out in full: why it fits the job, scope, stack, architecture, data model, interfaces, milestones with "done when" checks, tests, CI and deployment, README and demo, what to understand for the interview and CV bullets with [placeholders] for numbers you measure. The page gives it as one prompt to copy into Claude, or as a `.md` file to put in the new project's folder. Each deep dive is one Claude call; earlier ones stay linked under the project.

- The CV page has tabs too (Preview, What changed, Edit, Rating) and its own **Next step** (check unsupported facts, fit one page, rate it, apply, record it as sent).
- **What changed from your base CV**: every bullet marked kept, reworded (word diff), new or dropped, plus added/removed skills. Skills and numbers that can't be found anywhere in your profile are flagged at the top.
- **Edit this CV**: change the headline, summary, bullets and skills (or the whole CV as JSON); saving re-renders the PDF.
- **Layout**: change the font size or the one-page setting at any time.

## Rate the CV

Where: on the CV page.

Scores the tailored CV and your original (or another CV for the same job) on the same scale: overall chance of an interview, six categories, what improved and what got worse. Two checks are measured, not judged by Claude: which of the posting's keywords each version contains, and an ATS check of the PDF's text layer (name, email, phone, job titles and keywords readable by screening software). Scores vary a little between runs, so compare the two versions within one rating.

## Cover letter

Where: job page.

In English, Bulgarian or German, choice of tone and length, based on your profile or a tailored CV. Edit the text on the letter page; saving re-renders the PDF. Requirements you don't meet are listed as "not claimed".

## Interview prep

Where: job page.

Likely questions by category with answer outlines from your own experience, STAR stories, topics to revise by priority, honest answers for your gaps, and questions to ask them.

## Insights

Where: `/insights`.

- **Your gaps**: Claude groups the gaps from all your fit checks (full and quick) into a ranked list: how many jobs each one affects, whether it's something to learn or something you have but need to prove on your CV, how to close it, and what to do next. It also lists the tech terms your jobs ask for that your profile never mentions (counted from posting text, no Claude needed).
- **Market**: from the collected jobs (last 30/90 days or everything, hidden ones included): the most requested skills with whether you have each and whether it's rising or falling over the last 4 weeks, salaries by seniority and place (each job a dot, with the median), and postings per week.

## Jobs list density

Comfortable shows the fit notes and feedback chips under each job; Compact shows one line per job. The choice is remembered in your browser.

## Multiple people

Where: `/people`, or the person menu in the top bar.

Run job searches for a friend or partner from the same app. Each person has their own profile, photo, jobs, CVs, cover letters, applications, saved searches and schedules, role profiles and learned preferences. The company list (`companies.yaml`) and the API keys in Settings are shared by the people of one account. Your original data stays in `data/` as the default person; everyone else lives in `data/people/<id>/`. The browser remembers who is selected, and the person menu turns orange while you're working for someone else. Background tasks and scheduled searches always work for the person who started them, and their notifications start with that person's name. GitHub checks use the GitHub link in each person's profile (the GitHub username in Settings only applies to the default person). Removing a person moves their folder to `data/people/.removed/` instead of deleting it.

## Add a job by link or text

Where: `/add`, or **+ Add job** in the top bar.

Paste a posting's link or its text. Claude reads it, the job is saved as "To apply", and then (your choice) your fit is checked, a one-page CV is tailored, or a full application pack is made. LinkedIn and jobs.bg pages are not loaded; paste their text instead.

## Posting checks

Where: the *Posting check* box on a job's overview.

- **Still open?** Asked through the job board's own API (Greenhouse, Lever, Ashby, SmartRecruiters, Workable), otherwise by loading the posting and looking for 404/410, expired redirects or "no longer accepting applications" wording.
- **Closed postings** leave the jobs list and go to a **Closed** tab; jobs you applied to stay in the pipeline. A job that shows up again in a search is reopened.
- **Automatic checks:** after each search, the jobs you haven't applied to are checked (an option in *Schedules & Claude rating*). The **Check they're still open** button above the list checks them on demand.
- **Warning signs, found without Claude:** reposts (same title at the same company, refreshed posting dates, roles open for months), work-permit wording ("must be authorized to work in…", "no visa sponsorship", citizenship, clearance), compared with your location, and scam signs (fees, bank details, chat-app interviews).
- **Deeper check with Claude:** weighs it all and gives a 1-10 trust score.

## Outreach

Where: job page → Outreach.

Names and e-mails found in the posting, the people worth finding (with links that open LinkedIn's own people search), and drafts of a LinkedIn connection note (300 characters at most, with a counter), a message for after they accept, an application e-mail and a follow-up e-mail. Copy them or open them in your e-mail app. The app never sends anything.

## Interview stage

Where: job page → Interview, and **Story bank** in the top bar.

- **Story bank** (`/stories`): STAR+R stories (situation, task, action, result, reflection) drafted by Claude from your profile, with [placeholders] and questions where only you know the details. Interview prep and practice use them.
- **Practice** (`/practice/<job>`): answer a question from your prep, a new one from Claude, or one you were asked. You get a 1-10 grade, what worked, what to fix, a stronger answer built from your own facts, and the likely follow-up question.
- **Debriefs:** after each interview, note the round, interviewers, questions and how it went. Claude says what to improve, what the next round will dig into, suggests new stories for the bank, and drafts a thank-you note. Each debrief is added to the job's timeline.

## Offer review

Where: job page → Offer.

Paste or upload (PDF) an offer or contract. Claude lists the terms, flags clauses red/amber/green with what to ask, notes what is missing, compares the pay with salaries the app has seen for similar titles, suggests what to negotiate (with wording), and drafts a reply. It's a careful second read, not legal advice.

## Skill check

Measured, no AI. Every technology a tailored CV, cover letter, outreach draft, thank-you note or offer reply mentions must be in your profile (skills, experience, projects, interview facts) or your GitHub repos (language, topics, README, files such as `Dockerfile` or `pom.xml`). Anything else is flagged with the sentence it's in, on the CV's *Changes* tab, the letter page and under each draft. Saying you haven't used something ("I have not yet worked with Angular") counts as honest. Naming the job's title or company isn't a claim. If a flagged skill is true, **It's true, add to my skills** adds it to your profile so it stops being flagged.

## Chat

Where: *Chat* in the top bar, or *Ask Claude* on a job page.

Ask Claude anything about your CV, your job search or one job. For a job it also sees the posting, your stage and notes, the fit check, and the latest CV and letter made for it. Ask it to change your profile (your original CV) and it suggests the change, showing each line that would be added or removed. Nothing is saved until you click **Apply to my profile**, and **Undo** puts it back. If you edited your profile in the meantime, an older suggestion is refused rather than overwriting your edits. You can attach files (PDF, Word, images, text: a CV, a posting, an offer, a screenshot) with the clip button, by dragging them in, or by pasting a screenshot. Claude reads them, and earlier attachments stay readable later in the same chat. Each message is one Claude call on your plan.

## Notifications

Where: the bell in the top bar.

New jobs from scheduled searches, follow-ups due, postings found closed, finished or failed background tasks and ready packs, kept read/unread for 60 days. Long tasks also pop up on the desktop.

## Backup

Where: *My profile → History & backup*.

One zip of everything of the current person (profile, editor design and images, jobs database, CVs, letters, packs, chats, history); restoring one makes a backup of the current state first. The last 8 are kept in `data/backups/`.

## Company logos

On job cards, the job page and the pipeline: the company's own site icon (from the job link when it's the company's site, your company list, or a domain guessed from the name, used only when that site's own title names the company, so a company never gets the logo of an unrelated site with a similar name), cached in `data/logos/`; initials when none is found. Only the domain is sent out.

## CV editor

The editor has its own page: [CV editor](editor.md).
