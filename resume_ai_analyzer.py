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

from PIL import Image
import pytesseract
import io

def extract_text_with_ocr(file_path):
    try:
        doc = fitz.open(file_path)
        images = []
        for page_index in range(len(doc)):
            for img in doc[page_index].get_images(full=True):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image = Image.open(io.BytesIO(image_bytes)).convert("L")  # grayscale for OCR
                text = pytesseract.image_to_string(image)
                images.append(text)
        return "\n".join(images).strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_with_ocr]: {str(e)}")
        return ""

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_docx]: {str(e)}")
        return ""

def extract_text_from_resume(resume_file):
    try:
        if not resume_file or resume_file.filename == '':
            logger.error("No resume file provided")
            return ""
        
        ext = os.path.splitext(resume_file.filename)[1].lower()
        if ext not in {'.pdf', '.docx'}:
            logger.error(f"Unsupported file format: {ext}")
            return ""

        # Save the file temporarily
        filename = secure_filename(resume_file.filename)
        temp_path = os.path.join('uploads', filename)
        os.makedirs('Uploads', exist_ok=True)
        resume_file.save(temp_path)

        # Extract text based on file type
        if ext == '.pdf':
            text = extract_text_from_pdf(temp_path)
if not text.strip():
    logger.warning("PDF text empty — trying OCR fallback.")
    text = extract_text_with_ocr(temp_path)

        elif ext == '.docx':
            text = extract_text_from_docx(temp_path)

        # Clean up the temporary file
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
                logger.debug(f"Cleaned up temporary file: {temp_path}")
        except Exception as e:
            logger.error(f"Error cleaning up temporary file {temp_path}: {str(e)}")

        return text.strip() if text else ""

    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_resume]: {str(e)}")
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

def check_ats_compatibility(file_path):
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(file_path)
        elif ext == ".docx":
            text = extract_text_from_docx(file_path)
        else:
            return "❌ Unsupported file type."

        if not text.strip():
            return "❌ No readable text found in resume."

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
Clean and reformat the following resume into plain text with the following rules:
- Organize the resume into clear sections (e.g., Education, Experience, Skills, etc.).
- Use section headings in all caps (e.g., EDUCATION, EXPERIENCE, SKILLS).
- Use a single dash and space ("- ") for all bullet points.
- Ensure exactly one blank line between sections.
- Remove extra spaces, normalize line breaks, and ensure consistent formatting.
- Do not use HTML, markdown, or any other markup language—just plain text.

Example output:
NAME
John Doe

CONTACT
Email: john.doe@email.com
Phone: +1234567890

EDUCATION
- Bachelor of Science in Computer Science, XYZ University, 2020-2024

EXPERIENCE
- Software Engineer Intern, ABC Corp, June 2023 - August 2023
- Developed web applications using React and Node.js

SKILLS
- Python
- JavaScript
- SQL

Resume:
{text}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert in resume formatting."},
                {"role": "user", "content": prompt}
            ]
        )
        formatted_text = response.choices[0].message.content.strip()
        # Additional cleanup to ensure consistent formatting
        lines = formatted_text.split('\n')
        cleaned_lines = []
        for i, line in enumerate(lines):
            line = line.strip()
            if line:
                # Ensure bullet points start with "- "
                if line.startswith(('-', '*', '•')):
                    line = '- ' + line.lstrip('-*•').strip()
                cleaned_lines.append(line)
            elif i < len(lines) - 1 and lines[i + 1].strip():
                # Ensure exactly one blank line between sections
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append('')
        return {"formatted_text": '\n'.join(cleaned_lines).strip()}
    except Exception as e:
        logger.error(f"[ERROR in fix_resume_formatting]: {str(e)}")
        return {"error": "Failed to fix resume formatting due to an API error"}

def generate_section_content(suggestion, full_text):
    try:
        prompt = f"""
You are a professional resume writer.
Based on the suggestion and resume text provided, generate improved content for the relevant resume section.
Return the section name and the improved content in this format:
{{
  "section": "Section Name",
  "fixedContent": "Improved content here"
}}

Suggestion:
{suggestion}

Resume:
{full_text[:4000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return eval(response.choices[0].message.content.strip())

    except Exception as e:
        logger.error(f"[ERROR in generate_section_content]: {str(e)}")
        return {"error": "Failed to generate section content."}

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

Return a dictionary where each key is a section name (lowercase, underscore-separated, e.g., 'work_experience') and the value is the content of that section as a string. If a section is not present, exclude it from the dictionary. Ensure the output is a valid JSON string.

Resume:
{text[:4000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        sections = eval(response.choices[0].message.content.strip())
        return sections

    except Exception as e:
        logger.error(f"[ERROR in extract_resume_sections]: {str(e)}")
        return {}

def extract_keywords_from_jd(jd_text):
    try:
        prompt = f"""
From the following job description, extract the most important keywords that should be reflected in a resume.
Return the keywords as a comma-separated string.

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
        return ""

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

def analyze_job_description(jd_text):
    try:
        prompt = f"""
You are a job description analyzer. Analyze the following job description and extract:
- Required skills
- Preferred qualifications
- Key responsibilities
- Any specific keywords or phrases critical for resume alignment

Return the results in this JSON format:
{
  "required_skills": [],
  "preferred_qualifications": [],
  "key_responsibilities": [],
  "keywords": []
}

Job Description:
{jd_text[:3000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return eval(response.choices[0].message.content.strip())

    except Exception as e:
        logger.error(f"[ERROR in analyze_job_description]: {str(e)}")
        return {
            "required_skills": [],
            "preferred_qualifications": [],
            "key_responsibilities": [],
            "keywords": []
        }
def generate_resume_summary(name, role, experience, skills):
    prompt = f"""
    Write a concise and professional resume summary for the following candidate:

    Name: {name}
    Role: {role}
    Experience: {experience}
    Skills: {skills}

    Keep it within 3–5 lines. Focus on strengths, clarity, and professionalism.
    """

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{ "role": "user", "content": prompt }],
        max_tokens=250,
        temperature=0.7,
    )

    summary = response.choices[0].message.content.strip()
    return summary
