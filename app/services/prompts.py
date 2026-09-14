"""All LLM prompt templates used across the app, kept in one place so
wording/output-schema changes don't require hunting through each service
module. Each constant is formatted with `.format(...)` by its caller
(app.services.job_llm_extractor / app.services.resume_llm) — the
`{placeholder}` names below must match what those callers pass in.
"""

EXTRACTION_PROMPT = """You are extracting structured fields from the text of a job posting page.

Read the page text below and respond with ONLY a single JSON object (no \
markdown fences, no commentary) with exactly these keys:

{{
  "title": string or null,
  "company_name": string or null,
  "location": string or null,
  "workplace_type": one of "onsite", "remote", "hybrid", "unknown",
  "employment_type": one of "full_time", "part_time", "contract", "internship", "temporary", "unknown",
  "salary_min": integer or null,
  "salary_max": integer or null,
  "salary_currency": ISO 4217 currency code string or null,
  "posted_at": "YYYY-MM-DD" or null
}}

Use null (or "unknown" for the two enum fields) when the page does not say. \
Do not invent salary figures that aren't explicitly stated on the page.

Page text:
\"\"\"
{page_text}
\"\"\"
"""

REVIEW_PROMPT = """You are an expert resume reviewer and career coach.

Read the resume text below and respond with ONLY a single JSON object (no \
markdown fences, no commentary) with exactly these keys:

{{
  "strengths": [string, ...],
  "weaknesses": [string, ...],
  "suggestions": [string, ...],
  "summary": string
}}

Be specific and actionable — reference actual content from the resume \
rather than generic advice.

Resume text:
\"\"\"
{resume_text}
\"\"\"
"""

SCORE_PROMPT = """You are an ATS (applicant tracking system) matching engine.

Compare the resume text against the job description below and respond with \
ONLY a single JSON object (no markdown fences, no commentary) with exactly \
these keys:

{{
  "overall_score": integer from 0 to 100,
  "matched_keywords": [string, ...],
  "missing_keywords": [string, ...],
  "summary": string
}}

"overall_score" reflects how well the candidate's actual experience matches \
the job's stated requirements. "matched_keywords"/"missing_keywords" are \
specific skills/tools/qualifications from the job description, not generic \
words.

Resume text:
\"\"\"
{resume_text}
\"\"\"

Job description:
\"\"\"
{job_description}
\"\"\"
"""

TAILOR_PROMPT = """You are an expert resume writer specializing in ATS-optimized resumes.

Rewrite the candidate's resume below to target the specific job description \
that follows — emphasize and reword experience that matches the job's \
stated requirements, incorporate the job's own key terms where genuinely \
supported by the candidate's real experience (never invent experience they \
don't have).

Respond with ONLY a single JSON object (no markdown fences, no commentary) \
with exactly this key:

{{
  "html": string
}}

"html" is the full resume body as an HTML fragment (no <html>/<head>/<body> \
wrapper, no <style> blocks or inline CSS, no tables/columns/images) built \
only from these tags: h1 (candidate name, once), h2 (section headings — \
typically "Summary", "Experience", "Skills", "Education"), p, ul/li, \
strong, em. Keep it single-column and ATS-scannable — this gets converted \
straight to a plain .docx.

Resume text:
\"\"\"
{resume_text}
\"\"\"

Job description:
\"\"\"
{job_description}
\"\"\"
"""

COVER_LETTER_PROMPT = """You are an expert cover letter writer.

Write a concise, specific cover letter for the candidate below, targeting the \
job description that follows — reference real experience from the resume that \
matches what the job asks for (never invent experience they don't have), and \
avoid generic filler phrases.

Respond with ONLY a single JSON object (no markdown fences, no commentary) \
with exactly this key:

{{
  "html": string
}}

"html" is the full cover letter body as an HTML fragment (no \
<html>/<head>/<body> wrapper, no <style> blocks or inline CSS, no \
tables/columns/images) built only from these tags: p, strong, em. Include \
a short salutation paragraph (e.g. "Dear Hiring Manager,"), 2-4 body \
paragraphs making the case for this candidate for this specific role, and \
a short sign-off paragraph (e.g. "Sincerely, {{candidate_name}}" if a name \
is inferable from the resume, otherwise just "Sincerely,"). This gets \
converted straight to a plain .docx.

Resume text:
\"\"\"
{resume_text}
\"\"\"

Job description:
\"\"\"
{job_description}
\"\"\"
"""
