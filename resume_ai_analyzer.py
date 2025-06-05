import os
import logging
import re
import json
from typing import Dict, List, Optional, Union
from collections import OrderedDict
import docx
import fitz  # PyMuPDF
from openai import OpenAI
from werkzeug.utils import secure_filename
from PIL import Image
import pytesseract
import io

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize OpenAI client
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    logger.error("OPENAI_API_KEY environment variable not set.")
    client = None
else:
    try:
        client = OpenAI(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {str(e)}")
        client = None

def extract_text_from_pdf(file_path: str) -> str:
    """
    Extract text from a PDF file using PyMuPDF, falling back to OCR if needed.

    Args:
        file_path (str): Path to the PDF file.

    Returns:
        str: Extracted text or empty string if extraction fails.
    """
    try:
        doc = fitz.open(file_path)
        text = "\n".join(page.get_text() for page in doc).strip()
        doc.close()

        if not text:
            logger.warning(f"No text extracted from {file_path} using PyMuPDF, attempting OCR...")
            text = extract_text_with_ocr(file_path)

        return text.strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_pdf]: {str(e)}")
        return ""

def extract_text_with_ocr(file_path: str) -> str:
    """
    Extract text from a PDF using OCR if direct text extraction fails.

    Args:
        file_path (str): Path to the PDF file.

    Returns:
        str: Extracted text or empty string if extraction fails.
    """
    try:
        if not pytesseract.get_tesseract_version():
            logger.error("Tesseract OCR engine not found.")
            return ""

        doc = fitz.open(file_path)
        if doc.page_count == 0:
            logger.error(f"PDF {file_path} has no pages")
            doc.close()
            return ""

        text_parts = []
        for page_index in range(doc.page_count):
            page = doc[page_index]
            text = page.get_text().strip()
            if text:
                logger.debug(f"Text extracted from page {page_index + 1} without OCR")
                text_parts.append(text)
                continue

            images = page.get_images(full=True)
            if not images:
                logger.warning(f"No images found on page {page_index + 1} for OCR")
                continue

            for img_index, img in enumerate(images):
                try:
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    if not base_image:
                        logger.warning(f"Failed to extract image {img_index + 1} from page {page_index + 1}")
                        continue

                    image_bytes = base_image["image"]
                    image = Image.open(io.BytesIO(image_bytes)).convert("L")
                    custom_config = r'--oem 3 --psm 6 -l eng'
                    text = pytesseract.image_to_string(image, config=custom_config).strip()
                    if text:
                        logger.debug(f"OCR extracted text from image {img_index + 1} on page {page_index + 1}: {text[:100]}...")
                        text_parts.append(text)
                except Exception as img_error:
                    logger.error(f"Error processing image {img_index + 1} on page {page_index + 1}: {str(img_error)}")
                    continue

        doc.close()
        combined_text = "\n".join(text_parts).strip()
        if not combined_text:
            logger.warning(f"No text extracted via OCR from {file_path}")
        return combined_text
    except Exception as e:
        logger.error(f"[ERROR in extract_text_with_ocr]: {str(e)}")
        return ""

def extract_text_from_docx(file_path: str) -> str:
    """
    Extract text from a DOCX file.

    Args:
        file_path (str): Path to the DOCX file.

    Returns:
        str: Extracted text or empty string if extraction fails.
    """
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_docx]: {str(e)}")
        return ""

def extract_text_from_resume(resume_file) -> str:
    """
    Extract text from a resume file (PDF or DOCX).

    Args:
        resume_file: File object containing the resume.

    Returns:
        str: Extracted text or empty string if extraction fails.
    """
    try:
        if not resume_file or resume_file.filename == '':
            logger.error("No resume file provided")
            return ""

        ext = os.path.splitext(resume_file.filename)[1].lower()
        if ext not in {'.pdf', '.docx'}:
            logger.error(f"Unsupported file format: {ext}")
            return ""

        filename = secure_filename(resume_file.filename)
        temp_path = os.path.join('/tmp/Uploads', filename)
        os.makedirs('/tmp/Uploads', exist_ok=True)
        logger.debug(f"Saving file to {temp_path}")

        resume_file.seek(0, os.SEEK_END)
        file_size = resume_file.tell() / 1024  # Size in KB
        resume_file.seek(0)
        if file_size == 0:
            logger.error(f"File {filename} is empty")
            return ""
        if file_size > 10240:  # 10MB limit
            logger.error(f"File {filename} is too large: {file_size:.2f} KB")
            return ""
        logger.debug(f"File size: {file_size:.2f} KB")

        resume_file.save(temp_path)
        if not os.path.exists(temp_path):
            logger.error(f"Failed to save file to {temp_path}")
            return ""

        os.chmod(temp_path, 0o644)
        saved_size = os.path.getsize(temp_path) / 1024
        if saved_size == 0:
            logger.error(f"Saved file {temp_path} is empty")
            return ""

        text = extract_text_from_pdf(temp_path) if ext == '.pdf' else extract_text_from_docx(temp_path)
        if not text.strip():
            logger.warning(f"No text extracted from {temp_path}")
            return ""

        logger.info(f"Successfully extracted text from {filename}: {len(text)} characters")
        return text.strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_resume]: {str(e)}")
        return ""
    finally:
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
                logger.debug(f"Cleaned up temporary file: {temp_path}")
            except Exception as e:
                logger.error(f"Error cleaning up temporary file {temp_path}: {str(e)}")

def extract_resume_sections(text: str) -> Dict[str, str]:
    """
    Extract sections from resume text using regex pattern matching.

    Args:
        text (str): Raw resume text.

    Returns:
        Dict[str, str]: Dictionary mapping section titles to their content.
    """
    section_headings = [
        "Objective", "Summary", "Profile", "Career Objective",
        "Education", "Academic Background", "Educational Qualifications",
        "Experience", "Work Experience", "Professional Experience",
        "Internship", "Internships",
        "Projects", "Personal Projects", "Technical Projects",
        "Skills", "Technical Skills", "Key Skills",
        "Certifications", "Courses", "Licenses",
        "Awards", "Achievements", "Honors",
        "Languages", "Languages Known",
        "Hobbies", "Interests",
        "Publications", "Research Work",
        "Extracurricular Activities", "Volunteer Work", "Volunteer Experience",
        "References",
        "Personal Details", "Contact Information"
    ]

    section_pattern = re.compile(rf"^(?:-)?\s*(?:-)?({'|'.join(map(re.escape, section_headings)}))(?:[:\-]?\s*)$", re.IGNORECASE | re.MULTILINE)
    matches = list(section_pattern.finditer(text))
    parsed_sections = OrderedDict()

    # Initialize default sections
    default_sections = {
        "name": "",
        "job_title": "",
        "personal_details": "",
        "summary": "",
        "education": "",
        "work_experience": "",
        "skills": "",
        "projects": "",
        "certifications": "",
        "languages": "",
        "hobbies": "",
        "achievements": "",
        "volunteer_experience": "",
        "references": ""
    }

    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_title = match.group(1).strip().title()
        section_body = text[start:end].strip()

        # Map section titles to standardized keys
        section_key = section_title.lower().replace(" ", "_")
        if "experience" in section_key:
            section_key = "work_experience"
        elif "personal" in section_key or "contact" in section_key:
            section_key = "personal_details"
        elif "volunteer" in section_key:
            section_key = "volunteer_experience"

        if section_key in parsed_sections:
            section_key += f"_{i}"

        parsed_sections[section_key] = section_body

    # Extract name and job title from the top of the resume if not in a section
    lines = text.split("\n")
    name_found = False
    for line in lines[:5]:
        line = line.strip()
        if not line:
            continue
        if not name_found and len(line) < 50 and "@" not in line and not re.search(r"\d{5,}", line):
            parsed_sections["name"] = line
            name_found = True
        elif name_found and len(line) < 50 and "@" not in line and not re.search(r"\d{5,}", line):
            parsed_sections["job_title"] = line
            break

    # Merge with default sections to ensure all expected keys exist
    default_sections.update(parsed_sections)
    return default_sections

def analyze_resume_with_openai(resume_text: str, atsfix: bool = False) -> Dict[str, Union[str, List[str]]]:
    """
    Analyze a resume using OpenAI and provide improvement suggestions.

    Args:
        resume_text (str): Raw resume text.
        atsfix (bool): If True, focus on ATS-specific improvements.

    Returns:
        Dict[str, Union[str, List[str]]]: Analysis result with suggestions.
    """
    if not client:
        return {"error": "OpenAI API key not set. Please configure the OPENAI_API_KEY environment variable."}

    try:
        if not isinstance(resume_text, str) or not resume_text.strip():
            return {"error": "No readable text provided."}

        prompt = f"""
You are a professional resume analyzer.
Analyze the following resume and provide key suggestions to improve its impact, clarity, and formatting.
Give up to 7 specific, actionable suggestions only. Avoid generic advice.

Resume:
{resume_text[:6000]}
        """
        if atsfix:
            prompt = f"""
You are an expert in optimizing resumes for Applicant Tracking Systems (ATS).
Analyze the following resume and provide specific suggestions to improve its ATS compatibility.
Give up to 7 specific, actionable suggestions only. Focus on ATS-specific improvements like keywords, section headings, and formatting.

Resume:
{resume_text[:6000]}
            """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        suggestions = response.choices[0].message.content.strip().splitlines()
        return {"text": resume_text, "suggestions": suggestions}
    except Exception as e:
        logger.error(f"[ERROR in analyze_resume_with_openai]: {str(e)}")
        return {"error": f"Failed to analyze resume: {str(e)}"}

def check_ats_compatibility_fast(text: str) -> Dict[str, Union[int, List[str]]]:
    """
    Perform a fast ATS compatibility check on resume text.

    Args:
        text (str): Raw resume text.

    Returns:
        Dict[str, Union[int, List[str]]]: ATS compatibility report with score and issues.
    """
    score = 100
    issues = []

    if not re.search(r'\b\w+@\w+\.\w+\b', text):
        issues.append("❌ Missing email - Add your email.")
        score -= 15
    else:
        issues.append("✅ Email found.")

    if not re.search(r'\+?\d[\d\s\-]{8,}', text):
        issues.append("❌ Missing phone - Add your phone number.")
        score -= 10
    else:
        issues.append("✅ Phone number found.")

    if not re.search(r'\b(?:[A-Z][a-z]+(?:,\s*)?)+\b', text):
        issues.append("❌ Missing location - Add your city and state.")
        score -= 10
    else:
        issues.append("✅ Location found.")

    keywords = ["education", "experience", "skills", "certifications"]
    found = [k for k in keywords if k in text.lower()]
    if len(found) < 3:
        issues.append(f"❌ Missing sections - Add {', '.join(set(keywords) - set(found))}")
        score -= 20
    else:
        issues.append("✅ Key sections found.")

    return {"score": max(0, score), "issues": issues[:5]}

def fix_resume_formatting(file_path: str) -> Dict[str, str]:
    """
    Fix the formatting of a resume file and return it as plain text.

    Args:
        file_path (str): Path to the resume file.

    Returns:
        Dict[str, str]: Formatted resume text or error message.
    """
    ext = os.path.splitext(file_path)[1].lower()
    text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_docx(file_path) if ext == ".docx" else ""

    if not text.strip():
        return {"error": "No readable text found in resume"}

    if not client:
        return {"error": "Cannot format resume: OpenAI API key not set."}

    prompt = f"""
You are a professional resume formatting expert.
Clean and reformat the following resume into plain text with the following rules:
- Organize the resume into clear sections (e.g., Education, Experience, Skills, etc.).
- Use section headings in all caps (e.g., EDUCATION, EXPERIENCE, SKILLS).
- Use a single dash and space ("- ") for all bullet points.
- Ensure exactly one blank line between sections.
- Remove extra spaces, normalize line breaks, and ensure consistent formatting.
- Do not use HTML, markdown, or any other markup language—just plain text.
- Order sections as follows: PERSONAL DETAILS, SUMMARY, OBJECTIVE, SKILLS, EXPERIENCE, EDUCATION, CERTIFICATIONS, LANGUAGES, HOBBIES, PROJECTS, VOLUNTEER EXPERIENCE, ACHIEVEMENTS, PUBLICATIONS, REFERENCES.

Example output:
NAME
John Doe

PERSONAL DETAILS
- Email: john.doe@email.com
- Phone: +1234567890

SUMMARY
- A dedicated Software Engineer with 3 years of experience.

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
            messages=[{"role": "system", "content": "You are an expert in resume formatting."}, {"role": "user", "content": prompt}]
        )
        formatted_text = response.choices[0].message.content.strip()
        lines = formatted_text.split('\n')
        cleaned_lines = []
        for i, line in enumerate(lines):
            line = line.strip()
            if line:
                if line.startswith(('-', '*', '•')):
                    line = '- ' + line.lstrip('-*•').strip()
                cleaned_lines.append(line)
            elif i < len(lines) - 1 and lines[i + 1].strip():
                if cleaned_lines and cleaned_lines[-1]:
                    cleaned_lines.append('')
        return {"formatted_text": '\n'.join(cleaned_lines).strip()}
    except Exception as e:
        logger.error(f"[ERROR in fix_resume_formatting]: {str(e)}")
        return {"error": "Failed to fix resume formatting due to an API error"}

def generate_section_content(suggestion: Union[str, List[str]], full_text: str) -> Dict[str, str]:
    """
    Generate content for a resume section based on a suggestion.

    Args:
        suggestion (Union[str, List[str]]): Suggestion for improvement (e.g., "Missing phone section").
        full_text (str): Full resume text for context.

    Returns:
        Dict[str, str]: Dictionary with section name and generated content.
    """
    if not client:
        return {"error": "Cannot generate section content: OpenAI API key not set."}

    try:
        # Handle suggestion as a list or string
        if isinstance(suggestion, list):
            suggestion = suggestion[0] if suggestion else ""
        suggestion = str(suggestion).strip()

        if not suggestion:
            return {"error": "Empty suggestion provided"}

        sections = extract_resume_sections(full_text)
        personal_details = sections.get("personal_details", "").lower()
        has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+', personal_details))
        has_phone = bool(re.search(r'\+?\d[\d\s\-]{8,}', personal_details))
        has_location = "location" in personal_details or "city" in personal_details or "📍" in personal_details

        # Map suggestion to section
        section_mapping = {
            "phone": "personal_details",
            "email": "personal_details",
            "location": "personal_details",
            "website": "personal_details",
            "summary": "summary",
            "education": "education",
            "work experience": "work_experience",
            "skills": "skills",
            "projects": "projects",
            "certifications": "certifications",
            "languages": "languages",
            "hobbies": "hobbies",
            "achievements": "achievements",
            "volunteer": "volunteer_experience"
        }

        match = re.match(r"Missing (\w+\s*\w*) section", suggestion, re.IGNORECASE)
        if not match:
            logger.error(f"Invalid suggestion format: {suggestion}")
            return {"error": "Invalid suggestion format"}

        section_name = match.group(1).lower().strip()
        section_key = next((key for key, value in section_mapping.items() if key in section_name), section_name).replace(" ", "_")
        target_section = section_mapping.get(section_key, section_key)

        # Avoid duplicating personal details
        if target_section == "personal_details":
            if "phone" in section_name and has_phone:
                return {"section": "personal_details", "fixedContent": sections.get("personal_details", "")}
            if "email" in section_name and has_email:
                return {"section": "personal_details", "fixedContent": sections.get("personal_details", "")}
            if "location" in section_name and has_location:
                return {"section": "personal_details", "fixedContent": sections.get("personal_details", "")}

        prompt = f"""
You are an AI resume improvement expert.

Generate content for the '{target_section}' section based on the following suggestion and resume context.
- If the section is missing, generate relevant content.
- Use bullet points where applicable (e.g., for skills, experience).
- Format as plain text, no HTML or markdown.
- For education, format as: Degree, School Name, Year or Score.
- For personal_details, include only the requested field (e.g., phone, email).

Suggestion: {suggestion}
Resume: {full_text[:6000]}

Return a JSON object like:
{{"section": "{target_section}", "fixedContent": "Generated content"}}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        raw_response = response.choices[0].message.content.strip()

        try:
            result = json.loads(raw_response)
        except json.JSONDecodeError:
            import ast
            result = ast.literal_eval(raw_response)

        section = result.get("section", "").lower().replace(" ", "_")
        content = result.get("fixedContent", "").strip()

        if section == "education":
            lines = content.splitlines()
            cleaned = [line.strip("-•* \t") for line in lines if line.strip()]
            content = "\n".join(cleaned).strip()

        logger.info(f"Successfully generated content for section: {section}")
        return {"section": section, "fixedContent": content}
    except Exception as e:
        logger.error(f"[ERROR in generate_section_content]: {str(e)}")
        return {"error": f"Failed to generate section content: {str(e)}"}

def generate_resume_summary(name: str, role: str, experience: str, skills: str) -> str:
    """
    Generate a professional summary for a resume.

    Args:
        name (str): Candidate's name.
        role (str): Job role.
        experience (str): Work experience details.
        skills (str): Skills possessed.

    Returns:
        str: Generated summary.
    """
    if not client:
        return "OpenAI API key not set. Cannot generate summary."

    experience = remove_unnecessary_personal_info(experience or "")
    skills = remove_unnecessary_personal_info(skills or "")

    prompt = f"""
You are a professional resume expert.
Write a concise 2–3 line professional summary for the following person:
- Name: {name}
- Role: {role}
- Experience: {experience}
- Skills: {skills}
Make it ATS-friendly, use action words, and highlight strengths. Do not include heading or labels.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[ERROR in generate_resume_summary]: {str(e)}")
        return "Failed to generate summary due to AI error."

def remove_unnecessary_personal_info(text: str) -> str:
    """
    Remove unnecessary personal information from resume text.

    Args:
        text (str): Raw resume text.

    Returns:
        str: Cleaned text.
    """
    text = re.sub(r"(Date of Birth|DOB)[:\-]?\s*\d{1,2}[\/\-\.]?\d{1,2}[\/\-\.]?\d{2,4}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(Date of Birth|DOB)[:\-]?\s*[A-Za-z]+\s+\d{1,2},?\s+\d{4}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Gender[:\-]?\s*(Male|Female|Other|Prefer not to say)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Marital Status[:\-]?\s*(Single|Married|Divorced|Widowed)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Nationality[:\-]?\s*\w+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Religion[:\-]?\s*\w+", "", text, flags=re.IGNORECASE)
    text = re.sub(r'((Address|Location)[:\-]?)?\s*[\w\s\-\,\.\/]*?(\b[A-Z][a-z]+\b)[,\s]+(\b[A-Z][a-z]+\b)(?:\s*\d{5,6})?(,\s*India)?', r'\3, \4', text)
    return text

def extract_keywords_from_jd(jd_text: str) -> str:
    """
    Extract keywords from a job description using OpenAI.

    Args:
        jd_text (str): Job description text.

    Returns:
        str: Comma-separated keywords.
    """
    if not client:
        return "Cannot extract keywords: OpenAI API key not set."

    prompt = f"""
From the following job description, extract the most important keywords that should be reflected in a resume.
Return the keywords as a comma-separated string.

Job Description:
{jd_text[:3000]}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_keywords_from_jd]: {str(e)}")
        return "Failed to extract keywords from job description."

def compare_resume_with_keywords(resume_text: str, job_keywords: str) -> Dict[str, Union[int, List[str]]]:
    """
    Compare resume text with job description keywords.

    Args:
        resume_text (str): Raw resume text.
        job_keywords (str): Comma-separated keywords from job description.

    Returns:
        Dict[str, Union[int, List[str]]]: Match score and keyword comparison.
    """
    if not resume_text or not job_keywords:
        return {"match_score": 0, "missing_keywords": job_keywords}

    resume_lower = resume_text.lower()
    keywords = [kw.strip().lower() for kw in job_keywords.split(",") if kw.strip()]
    missing_keywords = [kw for kw in keywords if kw not in resume_lower]
    matched_keywords = [kw for kw in keywords if kw in resume_lower]

    match_score = int((len(matched_keywords) / len(keywords)) * 100) if keywords else 0
    return {
        "match_score": match_score,
        "matched_keywords": matched_keywords,
        "missing_keywords": missing_keywords
    }

def analyze_job_description(jd_text: str) -> str:
    """
    Analyze a job description and extract key skills, qualifications, and action verbs.

    Args:
        jd_text (str): Job description text.

    Returns:
        str: Analysis result.
    """
    if not client:
        return "OpenAI API key not set. Cannot analyze job description."

    prompt = f"""
You are an expert resume reviewer.
Analyze the following job description and extract the most relevant:
1. Key Skills
2. Required Qualifications
3. Recommended Action Verbs
Format the result clearly in 3 sections with headings.

Job Description:
{jd_text}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[ERROR in analyze_job_description]: {str(e)}")
        return "Failed to analyze job description."

def generate_michelle_template_html(sections: Dict[str, str]) -> str:
    """
    Generate an HTML resume template using the Michelle template style.

    Args:
        sections (Dict[str, str]): Dictionary of resume sections.

    Returns:
        str: HTML content for the resume.
    """
    def list_items(text: str, section_type: str = "other") -> str:
        if not text:
            return ""
        lines = text.strip().split("\n")
        if section_type == "education":
            if len(lines) > 1 and any(line.startswith(("-", "•")) for line in lines):
                return "".join(f"<li>{line.strip().lstrip('-• ').strip()}</li>" for line in lines if line.strip())
            return f"<p>{text.strip()}</p>"
        return "".join(f"<li>{line.strip().lstrip('-• ').strip()}</li>" for line in lines if line.strip())

    name = "Your Name"
    phone = email = location = website = ""
    personal_lines = sections.get("personal_details", "").split("\n")

    for line in personal_lines:
        line = line.strip()
        if not line:
            continue
        if (
            name == "Your Name"
            and "@" not in line
            and not re.search(r"\d{5,}", line)
            and not any(x in line.lower() for x in ["www", ".com", "city", "state"])
            and len(line) < 50
        ):
            name = line
        if "email" in line.lower() or "@" in line or "📧" in line:
            email = line.replace("📧", "").strip()
        elif "phone" in line.lower() or re.search(r"\+?\d[\d\s\-]{8,}", line) or "📞" in line:
            phone = line.replace("📞", "").strip()
        elif "location" in line.lower() or "city" in line.lower() or "state" in line.lower() or "📍" in line:
            location = line.replace("📍", "").strip()
        elif "website" in line.lower() or "www" in line.lower() or "🌐" in line:
            website = line.replace("🌐", "").strip()

    if name == "Your Name":
        name = sections.get("name", "Your Name")

    title = sections.get("job_title", "Your Role")
    return f"""
    <div class='resume-wrapper' style='max-width:95%;margin:0 auto;background:#fff;border:1px solid #ccc;box-shadow:0 0 10px rgba(0,0,0,0.1);'>
      <div class='header' style='background:#d3d3d3;padding:20px;text-align:center;'>
        <h1 style='font-size: 28px; font-weight: 700; margin: 0; text-transform: uppercase;'>{name}</h1>
        <h2 style='font-size: 16px; font-weight: 400; margin: 8px 0 0; color: #666;'>{title}</h2>
      </div>
      <div class='content' style='display:flex;padding:15px;'>
        <div class='left-panel' style='width:25%;background:#f5f5f5;padding-right:15px;border-right:1px solid #ccc;box-sizing:border-box;'>
          <h3>Contact</h3>
          <div class='contact-item'>📞 {phone if phone else 'Not Provided'}</div>
          <div class='contact-item'>✉️ {email if email else 'Not Provided'}</div>
          <div class='contact-item'>📍 {location if location else 'Not Provided'}</div>
          <div class='contact-item'>🌐 {website if website else 'Not Provided'}</div>
          <h3>Education</h3>
          {list_items(sections.get('education', ''), 'education')}
          <h3>Skills</h3>
          <ul>{list_items(sections.get('skills', ''))}</ul>
          <h3>Hobbies</h3>
          <ul>{list_items(sections.get('hobbies', ''))}</ul>
        </div>
        <div class='right-panel' style='width:75%;padding-left:15px;box-sizing:border-box;'>
          <h3>Objective</h3>
          <p>{sections.get('summary', '')}</p>
          <h3>Professional Experience</h3>
          <ul>{list_items(sections.get('work_experience', ''))}</ul>
          <h3>Projects</h3>
          <ul>{list_items(sections.get('projects', ''))}</ul>
          <h3>Certifications</h3>
          <ul>{list_items(sections.get('certifications', ''))}</ul>
          <h3>Languages</h3>
          <ul>{list_items(sections.get('languages', ''))}</ul>
          <h3>Achievements</h3>
          <ul>{list_items(sections.get('achievements', ''))}</ul>
        </div>
      </div>
    </div>
    """

def generate_ats_report(text: str) -> Dict[str, Union[int, List[str]]]:
    """
    Generate an ATS compatibility report for the resume text.

    Args:
        text (str): Raw resume text.

    Returns:
        Dict[str, Union[int, List[str]]]: ATS report with score and issues.
    """
    return check_ats_compatibility_fast(text)

def calculate_resume_score(summary: str, ats_issues: List[str]) -> int:
    """
    Calculate a resume score based on summary presence and ATS issues.

    Args:
        summary (str): Resume summary.
        ats_issues (List[str]): List of ATS issues.

    Returns:
        int: Calculated score between 0 and 100.
    """
    score = 70
    if summary and summary.strip():
        score += 10
    if ats_issues:
        issue_penalty = sum(10 for issue in ats_issues if issue.startswith("❌"))
        score -= issue_penalty
    return max(0, min(score, 100))
