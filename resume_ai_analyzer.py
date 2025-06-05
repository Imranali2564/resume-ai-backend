import os
import logging
import docx
import fitz  # PyMuPDF
from openai import OpenAI
from werkzeug.utils import secure_filename
from difflib import SequenceMatcher
from collections import Counter
import json
import re
from PIL import Image
import pytesseract
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
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

def extract_text_from_pdf(file_path):
    try:
        doc = fitz.open(file_path)
        text = "\n".join(page.get_text() for page in doc).strip()
        doc.close()
        if not text:
            logger.warning(f"No text extracted from {file_path}, attempting OCR...")
            text = extract_text_with_ocr(file_path)
        return text if text.strip() else ""
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_pdf]: {str(e)}")
        return ""

def extract_text_with_ocr(file_path):
    try:
        tesseract_version = pytesseract.get_tesseract_version()
        logger.info(f"Tesseract version: {tesseract_version}")
    except Exception as e:
        logger.error(f"Tesseract OCR engine not found: {str(e)}")
        return ""

    try:
        doc = fitz.open(file_path)
        if doc.page_count == 0:
            logger.error(f"PDF {file_path} has no pages")
            doc.close()
            return ""
        text_parts = []
        for page_index in range(len(doc)):
            page = doc[page_index]
            text = page.get_text().strip()
            if text:
                text_parts.append(text)
                continue
            images = page.get_images(full=True)
            if not images:
                continue
            for img_index, img in enumerate(images):
                try:
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    if not base_image:
                        continue
                    image_bytes = base_image["image"]
                    image = Image.open(io.BytesIO(image_bytes)).convert("L")
                    custom_config = r'--oem 3 --psm 6 -l eng'
                    text = pytesseract.image_to_string(image, config=custom_config)
                    if text.strip():
                        text_parts.append(text.strip())
                except Exception as img_error:
                    logger.error(f"Error processing image {img_index + 1} on page {page_index + 1}: {str(img_error)}")
        doc.close()
        return "\n".join(text_parts).strip() or ""
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
        filename = secure_filename(resume_file.filename)
        temp_path = os.path.join('/tmp/Uploads', filename)
        os.makedirs('/tmp/Uploads', exist_ok=True)
        resume_file.seek(0, os.SEEK_END)
        file_size = resume_file.tell() / 1024
        resume_file.seek(0)
        if file_size == 0:
            logger.error(f"File {filename} is empty")
            return ""
        if file_size > 10240:
            logger.error(f"File {filename} is too large: {file_size:.2f} KB")
            return ""
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

def analyze_resume_with_openai(resume_text, atsfix=False):
    if not client:
        return {"error": "OpenAI API key not set."}
    try:
        if not isinstance(resume_text, str) or not resume_text.strip():
            return {"error": "No readable text provided."}
        prompt = f"""
You are a professional resume analyzer.
Analyze the following resume and provide up to 7 specific, actionable suggestions to improve impact, clarity, and formatting.
Resume:
{resume_text[:6000]}
"""
        if atsfix:
            prompt = f"""
You are an ATS expert.
Analyze the following resume and provide up to 7 specific suggestions to improve ATS compatibility.
Resume:
{resume_text[:6000]}
"""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        suggestions = response.choices[0].message.content.strip()
        return {"text": resume_text, "suggestions": suggestions}
    except Exception as e:
        logger.error(f"[ERROR in analyze_resume_with_openai]: {str(e)}")
        return {"error": f"Failed to analyze resume: {str(e)}"}

def check_ats_compatibility(file_path):
    try:
        ext = os.path.splitext(file_path)[1].lower()
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_docx(file_path)
        if not text.strip():
            return {"error": "No readable text found in resume."}
        issues = []
        score = 100
        if not re.search(r'[\w\.-]+@[\w\.-]+', text, re.IGNORECASE):
            issues.append("❌ Missing email - Add your email address.")
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
        section_headings = ['education', 'experience', 'skills', 'certifications', 'projects']
        found_headings = [heading for heading in section_headings if heading in text.lower()]
        if len(found_headings) < 3:
            missing_sections = list(set(section_headings) - set(found_headings))
            issues.append(f"❌ Missing key sections - Add {', '.join(missing_sections)}.")
            score -= 20
        else:
            issues.append("✅ Key sections found.")
        common_keywords = [
            'communication', 'leadership', 'teamwork', 'project management', 'problem-solving',
            'python', 'javascript', 'sql', 'java', 'excel', 'data analysis', 'marketing',
            'sales', 'customer service', 'agile', 'scrum', 'cloud', 'aws', 'azure'
        ]
        found_keywords = [kw for kw in common_keywords if kw in text.lower()]
        keyword_density = len(found_keywords) / len(common_keywords)
        if keyword_density < 0.2:
            issues.append("❌ Low keyword density - Add keywords like 'communication', 'leadership'.")
            score -= 15
        else:
            issues.append("✅ Sufficient keywords found.")
        word_count = len(text.split())
        if word_count < 150:
            issues.append("❌ Resume too short - Add more details.")
            score -= 10
        elif word_count > 1000:
            issues.append("❌ Resume too long - Shorten to 1-2 pages.")
            score -= 10
        else:
            issues.append("✅ Appropriate content length.")
        if re.search(r'[^\x00-\x7F]', text):
            issues.append("❌ Special characters detected - Use standard ASCII characters.")
            score -= 10
        else:
            issues.append("✅ No special characters detected.")
        if not re.search(r'\d+\%|\d+\s*(hours|projects|clients|sales)', text, re.IGNORECASE):
            issues.append("❌ Missing quantifiable achievements - Add metrics like 'increased sales by 20%'.")
            score -= 10
        else:
            issues.append("✅ Quantifiable achievements found.")
        action_verbs = ['led', 'developed', 'managed', 'improved', 'designed', 'implemented', 'analyzed']
        found_verbs = [verb for verb in action_verbs if verb in text.lower()]
        if len(found_verbs) < 2:
            issues.append("❌ Limited action verbs - Use verbs like 'led', 'developed'.")
            score -= 10
        else:
            issues.append("✅ Action verbs used effectively.")
        score = max(0, min(100, score))
        return {"issues": issues, "score": score}
    except Exception as e:
        logger.error(f"[ERROR in check_ats_compatibility]: {str(e)}")
        return {"error": f"Failed to generate ATS report: {str(e)}"}

def fix_resume_formatting(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_docx(file_path)
    if not text.strip():
        return {"error": "No readable text found in resume"}
    prompt = f"""
You are a resume formatting expert.
Clean and reformat the resume into plain text with:
- Clear sections (e.g., Education, Experience).
- Headings in all caps (e.g., EDUCATION).
- Single dash bullet points ("- ").
- One blank line between sections.
- Order: PERSONAL DETAILS, SUMMARY, SKILLS, EXPERIENCE, EDUCATION, CERTIFICATIONS, LANGUAGES, HOBBIES, PROJECTS.
Resume:
{text}
"""
    if not client:
        return {"error": "OpenAI API key not set."}
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "Expert in resume formatting."}, {"role": "user", "content": prompt}]
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
                cleaned_lines.append('')
        return {"formatted_text": '\n'.join(cleaned_lines).strip()}
    except Exception as e:
        logger.error(f"[ERROR in fix_resume_formatting]: {str(e)}")
        return {"error": "Failed to fix resume formatting"}

def generate_section_content(suggestion, full_text):
    if not client:
        return {"error": "OpenAI API key not set."}
    try:
        sections = extract_resume_sections(full_text)
        has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+', sections.get("personal_details", "").lower()))
        has_phone = bool(re.search(r'\+?\d[\d\s\-]{8,}', sections.get("personal_details", "").lower()))
        has_location = "location" in sections.get("personal_details", "").lower() or "city" in sections.get("personal_details", "").lower()
        prompt = f"""
You are a resume improvement expert.
Given the suggestion and resume text, return JSON with:
- 'section': section to update (e.g., skills, summary)
- 'fixedContent': fixed or new content
- Add grammar/spelling correction
- Use bullet points where applicable
Format:
{{"section": "skills", "fixedContent": "- Python\\n- Communication"}}
Email present: {has_email}
Phone present: {has_phone}
Location present: {has_location}
Suggestion:
{suggestion}
Resume:
{full_text[:6000]}
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
            try:
                result = ast.literal_eval(raw_response)
            except Exception as e:
                logger.error(f"[ERROR in generate_section_content]: {str(e)}")
                return {"error": str(e)}
        section = result.get("section", "").lower().replace(" ", "_")
        content = result.get("fixedContent", "").strip()
        if section == "education":
            lines = content.splitlines()
            cleaned = [line.strip("-* ").strip() for line in lines if line.strip()]
            content = "\n".join(cleaned).strip()
        return {"section": section, "fixedContent": content}
    except Exception as e:
        logger.error(f"[ERROR in generate_section_content]: {str(e)}")
        return {"error": f"Failed to generate section content: {str(e)}"}

def extract_resume_sections(text):
    try:
        # Define section headings and their normalized names
        section_mappings = {
            r'\b(Objective|Career Objective|Professional Summary|Summary|Profile)\b': 'summary',
            r'\b(Education|Academic Background|Educational Qualifications)\b': 'education',
            r'\b(Experience|Work Experience|Professional Experience|Employment History)\b': 'work_experience',
            r'\b(Internship|Internships)\b': 'internships',
            r'\b(Projects|Personal Projects|Technical Projects)\b': 'projects',
            r'\b(Skills|Technical Skills|Key Skills|Core Competencies)\b': 'skills',
            r'\b(Certifications|Courses|Licenses)\b': 'certifications',
            r'\b(Awards|Achievements|Honors)\b': 'achievements',
            r'\b(Languages|Languages Known)\b': 'languages',
            r'\b(Hobbies|Interests)\b': 'hobbies',
            r'\b(Publications|Research Work)\b': 'publications',
            r'\b(Extracurricular Activities|Volunteer Work|Community Involvement)\b': 'volunteer_experience',
            r'\b(References|Referees)\b': 'references'
        }

        # Initialize output
        parsed_sections = {
            'name': '',
            'job_title': '',
            'phone': '',
            'email': '',
            'location': '',
            'website': '',
            'summary': '',
            'education': '',
            'work_experience': '',
            'internships': '',
            'projects': '',
            'skills': '',
            'certifications': '',
            'achievements': '',
            'languages': '',
            'hobbies': '',
            'publications': '',
            'volunteer_experience': '',
            'references': ''
        }

        # Extract personal details
        lines = text.split('\n')
        name = ''
        contact_lines = []
        content_lines = []
        in_header = True

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if in_header:
                if not name and len(line) < 50 and not re.search(r'[\w\.-]+@[\w\.-]+|\+?\d[\d\s\-]{8,}|www\.|linkedin\.com', line, re.IGNORECASE):
                    name = line
                    continue
                if re.search(r'[\w\.-]+@[\w\.-]+|\+?\d[\d\s\-]{8,}|www\.|linkedin\.com|[A-Z][a-z]+,\s*[A-Z]{2}', line, re.IGNORECASE):
                    contact_lines.append(line)
                    continue
                in_header = False
            content_lines.append(line)

        parsed_sections['name'] = name
        for line in contact_lines:
            if re.search(r'[\w\.-]+@[\w\.-]+', line, re.IGNORECASE):
                parsed_sections['email'] = line
            elif re.search(r'\+?\d[\d\s\-]{8,}', line):
                parsed_sections['phone'] = line
            elif re.search(r'[A-Z][a-z]+,\s*[A-Z]{2}|[A-Z][a-z]+\s*(?:City|State)', line, re.IGNORECASE):
                parsed_sections['location'] = line
            elif re.search(r'www\.|linkedin\.com', line, re.IGNORECASE):
                parsed_sections['website'] = line

        # Extract job title (often after name or in summary)
        for line in content_lines[:10]:
            if re.search(r'\b(Engineer|Developer|Manager|Analyst|Consultant|Specialist|Coordinator|Director)\b', line, re.IGNORECASE) and len(line) < 50:
                parsed_sections['job_title'] = line
                break

        # Section extraction
        current_section = None
        section_content = []
        section_pattern = '|'.join(section_mappings.keys())

        for line in content_lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(section_pattern, line, re.IGNORECASE)
            if match:
                if current_section:
                    parsed_sections[current_section] = '\n'.join(section_content).strip()
                    section_content = []
                for pattern, normalized in section_mappings.items():
                    if re.match(pattern, line, re.IGNORECASE):
                        current_section = normalized
                        break
                continue
            if current_section:
                section_content.append(line)

        if current_section and section_content:
            parsed_sections[current_section] = '\n'.join(section_content).strip()

        logger.info(f"Extracted sections: {list(parsed_sections.keys())}")
        return parsed_sections
    except Exception as e:
        logger.error(f"[ERROR in extract_resume_sections]: {str(e)}")
        return {}

def extract_keywords_from_jd(jd_text):
    if not client:
        return "OpenAI API key not set."
    try:
        prompt = f"""
Extract key keywords from the job description as a comma-separated string.
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
        return "Failed to extract keywords."

def generate_resume_summary(name, role, experience, skills):
    if not client:
        return "OpenAI API key not set."
    try:
        prompt = f"""
Write a 2–3 line ATS-friendly professional summary for:
- Name: {name}
- Role: {role}
- Experience: {experience}
- Skills: {skills}
Use action words, highlight strengths, no heading.
"""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[ERROR in generate_resume_summary]: {str(e)}")
        return "Failed to generate summary."

def compare_resume_with_keywords(resume_text, job_keywords):
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

def analyze_job_description(jd_text):
    if not client:
        return "OpenAI API key not set."
    try:
        prompt = f"""
Analyze the job description and extract:
1. Key Skills
2. Required Qualifications
3. Recommended Action Verbs
Format in 3 sections with headings.
Job Description:
{jd_text}
"""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[ERROR in analyze_job_description]: {str(e)}")
        return "Failed to analyze job description."

def generate_michelle_template_html(sections):
    def list_items(text, section_type="other"):
        if not text:
            return ""
        lines = text.strip().split("\n")
        if section_type == "education":
            if len(lines) > 1 and any(line.startswith(("-", "•")) for line in lines):
                return "".join(f"<li>{line.strip().lstrip('-• ').strip()}</li>" for line in lines if line.strip())
            return f"<p>{text.strip()}</p>"
        return "".join(f"<li>{line.strip().lstrip('-• ').strip()}</li>" for line in lines if line.strip())

    name = sections.get("name", "Your Name")
    phone = sections.get("phone", "Not Provided")
    email = sections.get("email", "Not Provided")
    location = sections.get("location", "Not Provided")
    website = sections.get("website", "Not Provided")
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
          <div class='contact-item'>📞 {phone}</div>
          <div class='contact-item'>✉️ {email}</div>
          <div class='contact-item'>📍 {location}</div>
          <div class='contact-item'>🌐 {website}</div>
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

def check_ats_compatibility_fast(text):
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
    keywords = ["education", "experience", "skills", "certifications"]
    found = [k for k in keywords if k in text.lower()]
    if len(found) < 3:
        issues.append(f"❌ Missing sections - Add {', '.join(set(keywords) - set(found))}")
        score -= 20
    else:
        issues.append("✅ Key sections found.")
    return {"score": max(0, score), "issues": issues[:5]}

def check_ats_compatibility_deep(file_path):
    try:
        ext = os.path.splitext(file_path)[1].lower()
        text = extract_text_from_pdf(file_path) if ext == ".pdf" else extract_text_from_docx(file_path)
        if not text.strip():
            return {"error": "No readable text found in resume"}
        score = 100
        issues = []
        if not re.search(r'\b\w+@\w+\.\w+\b', text):
            issues.append("❌ Missing email - Add your email.")
            score -= 10
        if not re.search(r'\+?\d[\d\s\-]{8,}', text):
            issues.append("❌ Missing phone - Add your phone number.")
            score -= 10
        if len(text.split()) < 150:
            issues.append("❌ Resume too short")
            score -= 10
        prompt = f"""
You are an ATS expert. Check the resume and give up to 5 issues:
Resume:
{text[:6000]}
Flag unnecessary personal info like Marital Status, Date of Birth, Gender, Nationality, or Religion.
Return:
["✅ Passed: ...", "❌ Issue: ..."]
"""
        ai_resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        ai_lines = ai_resp.choices[0].message.content.strip().splitlines()
        issues += [line for line in ai_lines if line.strip()]
        score -= sum(5 for line in ai_lines if line.startswith("❌"))
        return {"score": max(score, 0), "issues": issues}
    except Exception as e:
        return {"error": str(e)}

def remove_unnecessary_personal_info(text):
    text = re.sub(r"(Date of Birth|DOB)[:\-]?\s*\d{1,2}[\/\-\.]?\d{1,2}[\/\-\.]?\d{2,4}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(Date of Birth|DOB)[:\-]?\s*[A-Za-z]+\s+\d{1,2},?\s+\d{4}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Gender[:\-]?\s*(Male|Female|Other|Prefer not to say)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Marital Status[:\-]?\s*(Single|Married|Divorced|Widowed)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Nationality[:\-]?\s*\w+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Religion[:\-]?\s*\w+", "", text, flags=re.IGNORECASE)
    text = re.sub(r'((Address|Location)[:\-]?)?\s*[\w\s\-\,\.\/]*?(\b[A-Z][a-z]+\b)[,\s]+(\b[A-Z][a-z]+\b)(?:\s*\d{5,6})?(,\s*India)?', r'\3, \4', text)
    return text

def generate_ats_report(text):
    return check_ats_compatibility_fast(text)

def calculate_resume_score(summary, ats_issues):
    score = 70
    if summary:
        score += 10
    if ats_issues:
        issue_penalty = sum(10 for issue in ats_issues if issue.startswith("❌"))
        score -= issue_penalty
    return max(0, min(score, 100))
