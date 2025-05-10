import os
import logging
import docx
import fitz  # PyMuPDF
from openai import OpenAI
from werkzeug.utils import secure_filename
from difflib import SequenceMatcher
from collections import Counter

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
logger = logging.getLogger(__name__)

def extract_text_from_pdf(file_path):
    try:
        doc = fitz.open(file_path)
        text = "\n".join(page.get_text() for page in doc)
        return text.strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_pdf]: {str(e)}")
        return ""

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_docx]: {str(e)}")
        return ""

def analyze_resume_with_openai(file_path, atsfix=False):
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(file_path)
        elif ext == ".docx":
            text = extract_text_from_docx(file_path)
        else:
            return {"error": "Unsupported file type."}

        if not text.strip():
            return {"error": "No readable text found in resume."}

        prompt = f"""
You are a professional resume analyzer.
Analyze the following resume and provide key suggestions to improve its impact, clarity, and formatting.
Give up to 7 suggestions only. Be specific.

Resume:
{text[:4000]}
        """

        if atsfix:
            prompt = f"""
You are an expert in optimizing resumes for Applicant Tracking Systems (ATS).
Analyze the following resume and provide specific suggestions to improve its ATS compatibility.
Give up to 7 suggestions only. Be practical.

Resume:
{text[:4000]}
            """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        suggestions = response.choices[0].message.content.strip()
        return {"text": text, "suggestions": suggestions}

    except Exception as e:
        logger.error(f"[ERROR in analyze_resume_with_openai]: {str(e)}")
        return {"error": "Failed to analyze resume."}

def check_ats_compatibility(text):
    try:
        prompt = f"""
You are an ATS scanner. Review this resume and provide a list of key compatibility checks in this format:

✅ Passed: Proper section headings used  
❌ Issue: No mention of technical skills  
✅ Passed: Education section is clear

Text:
{text[:4000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in check_ats_compatibility]: {str(e)}")
        return "❌ Failed to generate ATS compatibility report."

def fix_resume_formatting(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path)
    elif ext == ".docx":
        text = extract_text_from_docx(file_path)
    else:
        return {"error": "Unsupported file type"}

    if not text.strip():
        return {"error": "No readable text found in resume"}

    prompt = f"""
You are a professional resume formatting expert.
Clean and reformat the following resume:
- Fix alignment and spacing
- Properly indent bullet points
- Normalize fonts and remove extra lines
- Return clean plain text (not HTML or markdown)

Resume:
{text[:4000]}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert in resume formatting."},
                {"role": "user", "content": prompt}
            ]
        )
        return {"formatted_text": response.choices[0].message.content.strip()}
    except Exception as e:
        logger.error(f"[ERROR in fix_resume_formatting]: {str(e)}")
        return {"error": "Failed to fix resume formatting due to an API error"}

def generate_cover_letter(text):
    try:
        prompt = f"""
You are a professional career coach.
Write a short, professional cover letter based on the following resume text.
Make it job-agnostic and focused on showcasing strengths and tone.

Resume:
{text[:4000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in generate_cover_letter]: {str(e)}")
        return "❌ Failed to generate cover letter."

def extract_resume_sections(text):
    try:
        prompt = f"""
Split the following resume text into sections like:
- Objective
- Education
- Experience
- Skills
- Certifications
- Projects
- Achievements

Return each section with a heading, followed by relevant content.

Resume:
{text[:4000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in extract_resume_sections]: {str(e)}")
        return "❌ Failed to parse resume sections."

def extract_keywords_from_jd(jd_text):
    try:
        prompt = f"""
From the following job description, extract the most important keywords that should be reflected in a resume.

Job Description:
{jd_text[:3000]}
        """
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in extract_keywords_from_jd]: {str(e)}")
        return "❌ Failed to extract keywords from job description."

def compare_resume_with_keywords(resume_text, keywords_text):
    try:
        resume_words = set(resume_text.lower().split())
        keywords = [kw.strip().lower() for kw in keywords_text.split(",") if kw.strip()]
        present = [kw for kw in keywords if kw in resume_words]
        missing = [kw for kw in keywords if kw not in resume_words]
        suggested = Counter(missing).most_common(10)
        suggestions = [kw for kw, _ in suggested]

        return {
            "present_keywords": present,
            "missing_keywords": missing,
            "suggested_keywords": suggestions
        }
    except Exception as e:
        logger.error(f"[ERROR in compare_resume_with_keywords]: {str(e)}")
        return {
            "present_keywords": [],
            "missing_keywords": [],
            "suggested_keywords": []
        }
