import os
import re
import json
import docx
import logging
import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from io import BytesIO
from openai import OpenAI

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# GPT-3.5 model selection
api_key = os.environ.get("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

def extract_text_from_pdf(file_path):
    text = ""
    try:
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
    except Exception as e:
        logger.error(f"Error reading PDF: {e}")
    return text.strip()

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
    except Exception as e:
        logger.error(f"Error reading DOCX: {e}")
        return ""

def extract_text_with_ocr(image_path):
    try:
        image = Image.open(image_path)
        return pytesseract.image_to_string(image)
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return ""

def extract_resume_sections(text):
    """
    Split resume text into identified sections.
    """
    section_patterns = {
        'personal_details': r'(?:Contact|Personal|Details)[\s\S]{0,300}',
        'summary': r'(?:Summary|Objective)[\s\S]{0,800}',
        'education': r'(?:Education|Academics)[\s\S]{0,1200}',
        'work_experience': r'(?:Experience|Employment|Work)[\s\S]{0,2000}',
        'skills': r'(?:Skills|Technologies)[\s\S]{0,1000}',
        'certifications': r'(?:Certifications|Licenses)[\s\S]{0,800}',
        'projects': r'(?:Projects|Portfolio)[\s\S]{0,1200}',
        'languages': r'(?:Languages|Spoken Languages)[\s\S]{0,300}',
        'achievements': r'(?:Achievements|Awards|Honors)[\s\S]{0,500}',
    }

    extracted = {}
    for key, pattern in section_patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            extracted[key] = match.group().strip()

    return extracted
def generate_section_content(prompt, model="gpt-3.5-turbo"):
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You're a resume expert trained to improve resumes using clean, professional language."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI error: {e}")
        return "Error generating content."

def generate_suggestions(resume_text):
    prompt = f"""Analyze the following resume text and give improvement suggestions. Keep the suggestions relevant to ATS optimization, clarity, grammar, and structure. Give bullet-point style suggestions (5–7 max).

Resume Text:
\"\"\"
{resume_text}
\"\"\"
"""
    return generate_section_content(prompt)

def fix_suggestion(resume_text, suggestion):
    prompt = f"""You are a resume improvement assistant.

Based on the following resume text, apply the suggested fix below to the appropriate section. Return the improved version of the section only, and also specify which section it belongs to (like education, skills, work_experience, etc).

Resume Text:
\"\"\"
{resume_text}
\"\"\"

Suggested Fix:
\"\"\"
{suggestion}
\"\"\"

Give JSON output like:
{{"section": "skills", "fixedContent": "updated content here"}}
"""
    result = generate_section_content(prompt)
    try:
        data = json.loads(result)
        return data
    except:
        logger.warning("Failed to parse AI response. Using fallback.")
        return {"section": "general", "fixedContent": result}

def analyze_ats_compatibility(resume_text):
    prompt = f"""Analyze this resume for ATS (Applicant Tracking System) compatibility. Check for presence of relevant sections, use of action verbs, measurable results, keyword matching, formatting, and overall professionalism.

Resume:
\"\"\"
{resume_text}
\"\"\"

Give a JSON with:
- score (out of 100)
- list of short bullet points (max 5–6) showing strengths/weaknesses

Output format:
{{
  "score": 78,
  "issues": [
    "✅ Includes key sections like education, experience, and skills",
    "❌ Missing measurable achievements",
    "✅ Good use of action verbs",
    "❌ No keywords matching common job descriptions",
    "✅ Simple and readable formatting"
  ]
}}
"""
    result = generate_section_content(prompt)
    try:
        data = json.loads(result)
        return data
    except:
        logger.warning("Failed to parse ATS response. Using fallback.")
        return {
            "score": 70,
            "issues": result.split("\n")[:5]
        }
def process_resume_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path)
    elif ext == ".docx":
        text = extract_text_from_docx(file_path)
    else:
        return None, "Unsupported file format"

    if not text.strip():
        return None, "No extractable text found in resume"

    return text, None

def prepare_final_resume(sections):
    try:
        name = sections.get("personal_details", "").split('\n')[0] if sections.get("personal_details") else "Your Name"
        title = sections.get("summary", "").split('\n')[0] if sections.get("summary") else "Professional Summary"

        html = f"""
        <html>
        <head><meta charset='utf-8'><style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        h1 {{ font-size: 28px; text-align:center; margin-bottom: 4px; }}
        h2 {{ font-size: 18px; text-align:center; color: #555; margin-top: 0; }}
        h3 {{ margin-top:20px; border-bottom:1px solid #ccc; padding-bottom:5px; font-size: 17px; }}
        ul {{ padding-left:20px; }}
        li {{ margin-bottom:6px; }}
        </style></head>
        <body>
        <h1>{name}</h1>
        <h2>{title}</h2>
        """

        ordered_sections = [
            "education", "skills", "work_experience", "certifications",
            "projects", "languages", "achievements"
        ]
        for key in ordered_sections:
            if sections.get(key):
                html += f"<h3>{key.replace('_', ' ').title()}</h3><ul>"
                for line in sections[key].split('\n'):
                    html += f"<li>{line}</li>"
                html += "</ul>"

        html += "</body></html>"
        return html
    except Exception as e:
        logger.error(f"Error generating final resume HTML: {e}")
        return "<html><body><p>Error generating resume.</p></body></html>"
def generate_resume_summary(text):
    prompt = f"""You are a professional resume writer. Read the following resume and write a professional summary of 3-4 lines suitable for the top of the resume.

Resume:
{text}

Summary:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            temperature=0.7,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        summary = response.choices[0].message.content.strip()
        return summary
    except Exception as e:
        logger.error(f"Error generating resume summary: {e}")
        return "Could not generate summary."
def analyze_resume_with_openai(resume_text, client=None, model="gpt-3.5-turbo"):
    if not resume_text.strip():
        return {"error": "Empty resume text provided."}

    try:
        if not client:
            api_key = os.environ.get("OPENAI_API_KEY")
            client = OpenAI(api_key=api_key)

        prompt = f"""
You are a professional resume expert. Analyze the following resume text and identify issues related to clarity, structure, grammar, and ATS optimization. Suggest specific improvements in bullet points.

Resume Text:
\"\"\"
{resume_text}
\"\"\"

Return the suggestions as bullet points.
"""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a resume expert."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
        )

        return {"suggestions": response.choices[0].message.content.strip()}

    except Exception as e:
        return {"error": str(e)}
def check_ats_compatibility(resume_text):
    issues = []
    score = 100

    # Example logic – customize as needed
    if "@" not in resume_text:
        issues.append("❌ Issue: Email address missing.")
        score -= 10
    else:
        issues.append("✅ Passed: Email address present.")

    if not any(keyword in resume_text.lower() for keyword in ["experience", "education", "skills"]):
        issues.append("❌ Issue: Standard resume section headings missing.")
        score -= 10
    else:
        issues.append("✅ Passed: Found key section headings: education, experience, skills.")

    if len(resume_text.split()) < 150:
        issues.append("❌ Issue: Resume appears too short. Add more detail.")
        score -= 10
    else:
        issues.append("✅ Passed: Resume has sufficient content length.")

    # Add more checks as needed

    return {"issues": issues, "score": max(score, 0)}
