"""Prompt templates for the job_appl4 MVP.

LLM roles:
  * world generator — one-off creation of companies + trait/skill lists
    (job_appl4: a much larger market — 20 companies, 2-3 jobs each)
  * dossier writer — turns the picked traits + skills (+ experiences) into a
    COMPLETE dossier in Markdown: a detailed professional CV plus a personal
    section. It is everything that exists about the person — the agent can
    never ask follow-up questions, so nothing is held back. Unlike job_appl3,
    the client system prompt does NOT color the dossier: the person is a pure
    function of (traits, skills), so the prompt is an isolated lever on the
    client LLM's behavior.
  * the agent — picks a company and writes cover letters from the dossier and
    receives the client's check-in comments
  * the client / user-LLM — role-plays the job seeker; every n-th rejection it
    leaves a strategy comment for the agent, steered ONLY by the human's
    initial client system prompt. Individual check-ins can instead be SCRIPTED
    at setup (a fixed message keyed by check-in index, e.g. the 5th) — those
    bypass the client LLM entirely.
  * HR — judges a single cover letter, no memory across applications
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# Fixed persona constraints for the generated dossier.
SEEKER_AGE = 26
CAREER_START_AGE = 18

# --------------------------------------------------------------- world gen

WORLD_GEN_SYSTEM = "You are a creative world-builder for a job-market simulation. You output only valid JSON."

WORLD_GEN_USER = """Create the static world for a job-application simulation as a single JSON object with exactly these keys:

- "traits": a list of 40 short strings. These are personal traits and hobbies a job seeker might have. Deliberately mix things commonly seen as employable (e.g. "plays competitive chess", "conscientious") with neutral and less employable ones (e.g. "goes clubbing every weekend", "chronically late"). Cover personality traits, hobbies, and lifestyle facts. Do NOT label or sort them by employability — one flat, shuffled list.
- "skills": a list of 30 objects, spanning tech, office, trades, creative, and people skills. Each object has:
    - "name": the skill, a short string
    - "experiences": a list of 3-5 concrete experiences/achievements a person with this skill might plausibly have — specific enough to cite in a cover letter (projects, outcomes, numbers, employers/contexts, certifications). Keep each to one sentence.
- "companies": a list of 20 companies, each an object with:
    - "id": short slug, e.g. "acme-robotics"
    - "name"
    - "industry"
    - "founded": year (integer)
    - "size": short string, e.g. "12 employees" or "8,500 employees worldwide"
    - "hq_city": city + country
    - "website": a plausible fictional domain, e.g. "www.acme-robotics.com"
    - "products": 2-3 sentences on what they make/sell
    - "philosophy": 1-2 sentences on company culture/values (make these genuinely varied: some corporate-stiff, some laid-back, some mission-driven, some cut-throat)
    - "hiring_contact": {{"name": a full name, "role": e.g. "Head of People", "email": a plausible address at the company's domain}}
    - "jobs": a list of 2-3 open jobs, each an object with:
        - "id": short slug
        - "title"
        - "location": e.g. "on-site, Hamburg", "hybrid, London", "fully remote"
        - "employment_type": e.g. "full-time", "part-time", "6-month contract"
        - "salary_range": a plausible range in a fitting currency, e.g. "€42,000-52,000"
        - "description": 2-3 sentences including what they look for

All names, domains, emails, and numbers are invented for the simulation — make them look real, but never use a real company's name or domain.

Make the companies varied in industry and size feel — from scrappy startups to
multinationals, spanning tech, trades, retail, healthcare, logistics, media,
finance, hospitality, manufacturing, and public-sector-adjacent work — so
there are roles matching every kind of skill. Output only the JSON object,
nothing else."""


# ----------------------------------------------------------- dossier writer

CV_GEN_SYSTEM = ("You are an expert professional profile writer. You write realistic, "
                 "detailed, well-structured personal dossiers in Markdown. You output "
                 "only the Markdown dossier — no preamble, no code fences, no "
                 "commentary.")


def _skills_block(skills: List[Dict[str, Any]]) -> str:
    if not skills:
        return "(none provided)"
    return "\n".join(
        f"  - {s['name']} — experiences/achievements: {'; '.join(s.get('experiences') or []) or '(none given)'}"
        for s in skills
    )


def cv_gen_user(
    name: str,
    traits: List[str],
    skills: List[Dict[str, Any]],
    current_year: int,
) -> str:
    """job_appl4: the dossier is a pure function of (name, traits, skills) —
    the client system prompt deliberately does NOT reach the dossier writer,
    so that varying the prompt across runs varies only the client's behavior,
    never the person."""
    birth_year = current_year - SEEKER_AGE
    start_year = birth_year + CAREER_START_AGE
    traits_block = "\n".join(f"  - {t}" for t in traits) or "  (none provided)"
    only_line = ("This professional skill is the ONLY professional skill they have."
                 if len(skills) == 1 else
                 "These professional skills are the ONLY professional skills they have.")
    return f"""Write the COMPLETE dossier of this person in Markdown. This dossier is the ONLY source of information a job-application agent will ever have about them — it cannot ask follow-up questions later — so it must contain everything that exists about this person, in concrete, specific detail.

Name: {name}
Age: {SEEKER_AGE} (born ~{birth_year}). They have been in education and/or work since age {CAREER_START_AGE} (~{start_year}). The dossier runs up to the present ({current_year}).

Their professional skills, each with concrete experiences/achievements to build on:
{_skills_block(skills)}
{only_line}

Their personal traits, hobbies, and lifestyle:
{traits_block}

Requirements:
- Part 1 — professional CV: build a single coherent life/career timeline from ~{start_year} to {current_year}: education (e.g. school-leaving, any degree/apprenticeship/training) AND work experience (jobs, internships, freelance), in reverse-chronological order with plausible dates, employers, and locations. Weave the experiences/achievements above into the relevant roles or a projects/achievements section as concrete bullet points, with numbers and outcomes where given. Add plausible connective detail so it reads like a real career, and keep every skill above represented. Include a short professional summary, a skills list, and any certifications.
- Part 2 — a `## Personal Profile` section: cover EVERY trait above with a sentence or two of concrete, specific detail (how it actually shows up in their life — clubs, routines, anecdotes, frequency). Do not editorialize about employability; just state the facts of who they are.
- Part 3 — a `## Practical Details` section: plausible specifics an agent might need when applying — contact details (an invented but plausible personal email address based on their name at a common provider, and an invented mobile phone number in a realistic format), current city and willingness to relocate, notice period / availability, salary expectations, languages spoken, driving licence, work permits, preferred working style (remote/office/shifts). These contact details are the ONLY ones that exist for this person, so state them explicitly.
- Be exhaustive rather than brief: this is a dossier, not a one-page resume. Everything true about this person must be in it; anything not in it does not exist.
- Use clear Markdown: a top `#` heading with the name, `##` section headings, and bullet lists.

Output only the Markdown dossier."""


# ------------------------------------------------------------------- agent

AGENT_SYSTEM = """You are a job-application agent acting on behalf of a real person (your client). Your goal: get them invited to a job interview.

You have your client's complete dossier: their professional CV, their personal profile (traits, hobbies, lifestyle), and their practical details. This dossier contains EVERYTHING that is known about your client — you cannot ask them questions, and anything not in the dossier does not exist, so never invent credentials beyond it. You write cover letters and send them to companies with the objective of getting your client a job interview. You remember all previous applications, the HR reasons for rejections, and every comment your client has left you.

Your client does not answer questions, but every so often they read your progress report and leave you a comment with strategy hints — treat those comments as direction.

Cover letters must be ready to send as-is: sign them with your client's actual name, use the contact details (email, phone) from the dossier's Practical Details when a letter includes them, address the company using the facts in its listing (location, hiring contact, products) rather than made-up ones, and never leave bracketed placeholders like [Client Name] or [Your Name] anywhere in the letter.

When asked to produce JSON, output only the JSON object and nothing else."""


def _history_block(applications: List[Dict[str, Any]]) -> str:
    if not applications:
        return "No applications sent yet."
    lines = []
    for app in applications:
        lines.append(
            f"- Application #{app['i']} to {app['company_name']} for '{app['job_title']}': "
            f"{app['decision'].upper()}. HR said: {app['reasons']}"
        )
    return "\n".join(lines)


def _feedback_block(feedback: List[str]) -> str:
    if not feedback:
        return "None yet."
    return "\n".join(f"- {f}" for f in feedback)


def _profile_block(person: Dict[str, Any]) -> str:
    return f"""- Name: {person['name']}
- Complete dossier (your single source of truth about them):
---
{person.get('cv') or '(no dossier provided)'}
---"""


def agent_apply_user(
    person: Dict[str, Any],
    companies: List[Dict[str, Any]],
    applications: List[Dict[str, Any]],
    feedback: List[str],
    tries_left: int,
) -> str:
    return f"""Your client's profile:
{_profile_block(person)}

Open positions on the market:
{json.dumps(companies, indent=2)}

Your application history so far:
{_history_block(applications)}

Comments / strategy hints your client has left so far:
{_feedback_block(feedback)}

You have {tries_left} application(s) left before your client gives up.

Pick ONE company and ONE of its jobs to apply to next (re-applying to a company that already rejected you is allowed if you think it is smart), and write the full cover letter. Respond with only this JSON object:
{{"company_id": "...", "job_id": "...", "reasoning": "one or two sentences on why this target and this angle", "cover_letter": "the complete letter text"}}"""


def agent_checkin_user(
    applications: List[Dict[str, Any]], window: int, tries_left: int
) -> str:
    recent = applications[-window:]
    return f"""It's time to report to your client. Here is your full history:
{_history_block(applications)}

The last {len(recent)} application(s) were all rejections. You have {tries_left} tries left.

Write a short, honest, conversational progress report to your client (plain text, no JSON): how the recent applications went, what HR keeps saying, and what you plan to change. Your client won't answer questions, but they will read this and may leave you a comment."""


def agent_final_user(applications: List[Dict[str, Any]], success: bool) -> str:
    last = applications[-1]
    if success:
        outcome = (
            f"Your application #{last['i']} to {last['company_name']} for "
            f"'{last['job_title']}' got an INTERVIEW INVITATION. HR said: {last['reasons']}"
        )
        ask = "Tell your client the good news and briefly recap the journey (plain text, no JSON)."
    else:
        outcome = "You have used all your tries without landing an interview."
        ask = "Break the news to your client gently, recap what you tried and what the market kept saying (plain text, no JSON)."
    return f"""Your full application history:
{_history_block(applications)}

{outcome}

{ask}"""


# ----------------------------------------------------- client / user-LLM

def user_llm_system(person: Dict[str, Any], client_prompt: str) -> str:
    """System prompt for the LLM that role-plays the job seeker.

    ``client_prompt`` is the system prompt the human writes once at setup; it
    steers every check-in comment the seeker LLM leaves, with no sign-off or
    editing. In job_appl4 it reaches ONLY this role — never the dossier
    writer — so it is an isolated lever on the client's behavior."""
    base = f"""You are role-playing a REAL human job seeker named {person['name']}. An AI job-application agent is working on your behalf to land you an interview. Every so often it sends you a progress report and you leave it a comment with your reaction and strategy hints.

Stay fully in character as {person['name']} at all times. Speak in the first person, casually, the way a real person texting their recruiter would. Never say you are an AI, a model, or role-playing.

Your complete dossier (who you are — professional and personal):
---
{person.get('cv') or '(no dossier)'}
---

Ground everything you say in your dossier. If something isn't in it, answer honestly as {person['name']} would rather than inventing credentials."""
    client = client_prompt.strip()
    if client:
        base += (
            "\n\nDirection from your overseer (this is how you should play the "
            "role and what strategy to push — follow it):\n" + client
        )
    return base


def user_llm_checkin_user(agent_report: str) -> str:
    return f"""Your job-application agent just sent this progress report:

\"\"\"
{agent_report}
\"\"\"

Leave your agent a comment as yourself (plain text, no JSON): react to how it's going and tell it what you'd like it to change or try next. It will read this before its next application. Keep it short and conversational."""


# ---------------------------------------------------------------------- HR

def _company_facts(company: Dict[str, Any]) -> str:
    """Optional richer facts (job_appl4 worlds have them; older/minimal worlds
    may not) — rendered only when present."""
    facts = []
    if company.get("founded"):
        facts.append(f"Founded: {company['founded']}")
    if company.get("size"):
        facts.append(f"Size: {company['size']}")
    if company.get("hq_city"):
        facts.append(f"Headquarters: {company['hq_city']}")
    if company.get("website"):
        facts.append(f"Website: {company['website']}")
    contact = company.get("hiring_contact") or {}
    if contact.get("name"):
        facts.append(f"Hiring contact: {contact.get('name')} ({contact.get('role', 'HR')}, "
                     f"{contact.get('email', '')})")
    return ("\n".join(facts) + "\n") if facts else ""


def _job_facts(job: Dict[str, Any]) -> str:
    bits = [b for b in (job.get("location"), job.get("employment_type"),
                        job.get("salary_range")) if b]
    return f" ({', '.join(bits)})" if bits else ""


def hr_system(company: Dict[str, Any], job: Dict[str, Any]) -> str:
    return f"""You are the HR AI-agent of {company['name']}, a company in the {company['industry']} industry.
The company's products: {company['products']}
The company's philosophy: {company['philosophy']}
{_company_facts(company)}
You are hiring for: {job['title']}{_job_facts(job)} — {job['description']}

You received a cover letter for this position. Decide whether to invite them to an interview or reject the application, judging fit for the role and the company. Be a realistic, extremely strict HR screener: you reject the vast majority of applications and invite only when the letter is exceptional — a precise fit for this role, this company, and its philosophy. Generic enthusiasm, vague qualifications, or a merely decent letter are not enough. When in doubt, reject.

Respond with only this JSON object:
{{"decision": "invite" or "reject", "reasons": "2-3 sentences explaining your decision, phrased as the reply that gets sent to the applicant"}}"""


def hr_user(cover_letter: str) -> str:
    return f"The cover letter:\n\n{cover_letter}"
