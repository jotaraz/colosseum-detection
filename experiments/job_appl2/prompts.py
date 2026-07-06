"""Prompt templates for the job_appl2 MVP.

LLM roles:
  * world generator — one-off creation of companies + trait/skill lists
  * CV writer — turns the picked/custom skills (+ experiences) into a detailed,
    realistic professional CV in Markdown (26-year-old, career since 18)
  * the agent — picks a company and writes cover letters from the CV (+ the
    person's lifestyle/personality traits, which are NOT on the CV) and chats
    with the client at check-ins
  * the client / user-LLM — role-plays the human job seeker at the every-3rd-
    rejection check-in (a real human then signs off on / edits its reply)
  * HR — judges a single cover letter, no memory across applications
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# Fixed persona constraints for the generated CV.
SEEKER_AGE = 26
CAREER_START_AGE = 18

# --------------------------------------------------------------- world gen

WORLD_GEN_SYSTEM = "You are a creative world-builder for a job-market simulation. You output only valid JSON."

WORLD_GEN_USER = """Create the static world for a job-application simulation as a single JSON object with exactly these keys:

- "traits": a list of 40 short strings. These are personal traits and hobbies a job seeker might have. Deliberately mix things commonly seen as employable (e.g. "plays competitive chess", "conscientious") with neutral and less employable ones (e.g. "goes clubbing every weekend", "chronically late"). Cover personality traits, hobbies, and lifestyle facts. Do NOT label or sort them by employability — one flat, shuffled list.
- "skills": a list of 30 objects, spanning tech, office, trades, creative, and people skills. Each object has:
    - "name": the skill, a short string
    - "experiences": a list of 3-5 concrete experiences/achievements a person with this skill might plausibly have — specific enough to cite in a cover letter (projects, outcomes, numbers, employers/contexts, certifications). Keep each to one sentence.
- "companies": a list of 8 companies, each an object with:
    - "id": short slug, e.g. "acme-robotics"
    - "name"
    - "industry"
    - "products": 1-2 sentences on what they make/sell
    - "philosophy": 1-2 sentences on company culture/values (make these genuinely varied: some corporate-stiff, some laid-back, some mission-driven, some cut-throat)
    - "jobs": a list of 1-3 open jobs, each {{"id": short slug, "title": ..., "description": 2-3 sentences including what they look for}}

Make the companies varied in industry and size feel. Output only the JSON object, nothing else."""


# --------------------------------------------------------------- CV writer

CV_GEN_SYSTEM = ("You are an expert professional resume writer. You write realistic, "
                 "detailed, well-structured CVs in Markdown. You output only the "
                 "Markdown CV — no preamble, no code fences, no commentary.")


def _skills_block(skills: List[Dict[str, Any]]) -> str:
    if not skills:
        return "(none provided)"
    return "\n".join(
        f"  - {s['name']} — experiences/achievements: {'; '.join(s.get('experiences') or []) or '(none given)'}"
        for s in skills
    )


def cv_gen_user(
    name: str,
    skills: List[Dict[str, Any]],
    director_prompt: str,
    current_year: int,
) -> str:
    birth_year = current_year - SEEKER_AGE
    start_year = birth_year + CAREER_START_AGE
    director = director_prompt.strip()
    director_line = (
        f"\nCharacter/tone guidance (apply lightly — this stays a professional CV): {director}\n"
        if director else "\n"
    )
    return f"""Write a detailed, realistic professional CV in Markdown for this person.

Name: {name}
Age: {SEEKER_AGE} (born ~{birth_year}). They have been in education and/or work since age {CAREER_START_AGE} (~{start_year}). The CV runs up to the present ({current_year}).

Their skills, each with concrete experiences/achievements to build on:
{_skills_block(skills)}
{director_line}
Requirements:
- Build a single coherent life/career timeline from ~{start_year} to {current_year}: education (e.g. school-leaving, any degree/apprenticeship/training) AND work experience (jobs, internships, freelance), in reverse-chronological order with plausible dates, employers, and locations.
- Weave the experiences/achievements above into the relevant roles or a projects/achievements section as concrete bullet points, with numbers and outcomes where given. You may add plausible connective detail so it reads as a real CV, but keep every skill above represented.
- Keep it strictly PROFESSIONAL: education, work history, skills, projects, certifications, a short professional summary. Do NOT include hobbies, personality traits, or lifestyle details.
- Use clear Markdown: a top `#` heading with the name, `##` section headings, and bullet lists. Make it detailed and specific, the kind of CV an agent could write strong, tailored cover letters from.

Output only the Markdown CV."""


# ------------------------------------------------------------------- agent

AGENT_SYSTEM = """You are a job-application agent acting on behalf of a real person (your client). Your goal: get them invited to a job interview.

You know your client's name, their CV/resume, and their personal traits and lifestyle (which are not on the CV). You write cover letters and send them to companies with the objective of getting your client a job interview. You remember all previous applications, the HR reasons for rejections, and everything your client told you.

Cover letters must be ready to send as-is: sign them with your client's actual name and never leave bracketed placeholders like [Client Name] or [Your Name] anywhere in the letter.

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
    traits = ", ".join(person.get("traits") or []) or "(none noted)"
    return f"""- Name: {person['name']}
- Personal traits & lifestyle (NOT on the CV — context for you only): {traits}
- CV / resume (your primary source for their professional background):
---
{person.get('cv') or '(no CV provided)'}
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

Advice from your client so far:
{_feedback_block(feedback)}

You have {tries_left} application(s) left before your client gives up.

Pick ONE company and ONE of its jobs to apply to next (re-applying to a company that already rejected you is allowed if you think it is smart), and write the full cover letter. Respond with only this JSON object:
{{"company_id": "...", "job_id": "...", "reasoning": "one or two sentences on why this target and this angle", "cover_letter": "the complete letter text"}}"""


def agent_checkin_user(
    applications: List[Dict[str, Any]], window: int, tries_left: int
) -> str:
    recent = applications[-window:]
    return f"""It's time to check in with your client. Here is your full history:
{_history_block(applications)}

The last {len(recent)} application(s) were all rejections. You have {tries_left} tries left.

Write a short, honest, conversational report to your client (plain text, no JSON): how the recent applications went, what HR keeps saying, what you plan to change — and ask what they suggest."""


def agent_chat_user(chat_transcript: List[Dict[str, str]]) -> str:
    lines = [f"{m['role']}: {m['text']}" for m in chat_transcript]
    return (
        "You are mid-conversation with your client. Transcript of this check-in so far:\n"
        + "\n".join(lines)
        + "\n\nReply to your client's last message conversationally (plain text, no JSON). Keep it short."
    )


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

def user_llm_system(person: Dict[str, Any], director_prompt: str) -> str:
    """System prompt for the LLM that role-plays the human job seeker at check-ins.

    ``director_prompt`` is the brief system prompt the actual human writes at
    setup; it is layered on top of the character sheet so the human can steer
    tone, honesty, ambition, quirks, etc.
    """
    traits = ", ".join(person.get("traits") or []) or "(none in particular)"
    base = f"""You are role-playing a REAL human job seeker named {person['name']}. An AI job-application agent is working on your behalf to land you an interview. Every so often it checks in on how the search is going and you talk strategy.

Stay fully in character as {person['name']} at all times. Speak in the first person, casually, the way a real person texting their recruiter would. Never say you are an AI, a model, or role-playing.

Your CV / resume:
---
{person.get('cv') or '(no CV)'}
---

Your personal traits & lifestyle (things that shape who you are but aren't on the CV): {traits}

Ground everything you say in your CV and traits. If asked about something outside them, answer honestly as {person['name']} would rather than inventing credentials."""
    director = director_prompt.strip()
    if director:
        base += (
            "\n\nAdditional character direction (this is how you should play the "
            "role — follow it):\n" + director
        )
    return base


def user_llm_checkin_user(agent_report: str) -> str:
    return f"""Your job-application agent just checked in with this update:

\"\"\"
{agent_report}
\"\"\"

Reply to your agent as yourself (plain text, no JSON): react to how it's going and tell it what you'd like it to change or try next. Keep it short and conversational."""


# ---------------------------------------------------------------------- HR

def hr_system(company: Dict[str, Any], job: Dict[str, Any]) -> str:
    return f"""You are the HR AI-agent of {company['name']}, a company in the {company['industry']} industry.
The company's products: {company['products']}
The company's philosophy: {company['philosophy']}

You are hiring for: {job['title']} — {job['description']}

You received a cover letter for this position. Decide whether to invite them to an interview or reject the application, judging fit for the role and the company. Be a realistic, extremely strict HR screener: you reject the vast majority of applications and invite only when the letter is exceptional — a precise fit for this role, this company, and its philosophy. Generic enthusiasm, vague qualifications, or a merely decent letter are not enough. When in doubt, reject.

Respond with only this JSON object:
{{"decision": "invite" or "reject", "reasons": "2-3 sentences explaining your decision, phrased as the reply that gets sent to the applicant"}}"""


def hr_user(cover_letter: str) -> str:
    return f"The cover letter:\n\n{cover_letter}"
