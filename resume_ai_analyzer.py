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

# Initialize OpenAI client with error handling for missing API key
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    logger.error("OPENAI_API_KEY environment variable not set.")
    client = None  # Set client to None if API key is missing
else:
    try:
        client = OpenAI(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {str(e)}")
        client = None

def extract_text_from_pdf(file_path):
    try:
        # First, try to extract text directly using PyMuPDF
        doc = fitz.open(file_path)
        text = "\n".join(page.get_text() for page in doc).strip()
        doc.close()

        # If no text is extracted, try OCR
        if not text:
            logger.warning(f"No text extracted from {file_path} using PyMuPDF, attempting OCR...")
            try:
                text = extract_text_with_ocr(file_path)
            except Exception as ocr_error:
                logger.error(f"OCR failed for {file_path}: {str(ocr_error)}")
                return ""  # Return empty string if OCR fails

        return text if text.strip() else ""

    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_pdf]: {str(e)}")
        return ""

def extract_text_with_ocr(file_path):
    try:
        # Check if Tesseract is installed and accessible
        tesseract_version = pytesseract.get_tesseract_version()
        logger.info(f"Tesseract version: {tesseract_version}")
    except Exception as e:
        logger.error(f"Tesseract OCR engine not found: {str(e)}. Falling back to empty text.")
        return ""  # Fallback to empty text instead of raising error

    try:
        doc = fitz.open(file_path)
        if doc.page_count == 0:
            logger.error(f"PDF {file_path} has no pages")
            doc.close()
            return ""

        text_parts = []
        for page_index in range(len(doc)):
            page = doc[page_index]
            # Try to get text first
            text = page.get_text().strip()
            if text:
                logger.debug(f"Text extracted from page {page_index + 1} without OCR")
                text_parts.append(text)
                continue

            # If no text, extract images and run OCR
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
                    text = pytesseract.image_to_string(image, config=custom_config)
                    if text.strip():
                        logger.debug(f"OCR extracted text from image {img_index + 1} on page {page_index + 1}: {text[:100]}...")
                        text_parts.append(text.strip())
                    else:
                        logger.warning(f"No text extracted via OCR from image {img_index + 1} on page {page_index + 1}")
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
        return ""  # Fallback to empty text

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_docx]: {str(e)}")
        return ""

def extract_text_from_resume(resume_file):
    try:
        # Validate input
        if not resume_file or resume_file.filename == '':
            logger.error("No resume file provided")
            return ""
        
        ext = os.path.splitext(resume_file.filename)[1].lower()
        if ext not in {'.pdf', '.docx'}:
            logger.error(f"Unsupported file format: {ext}")
            return ""

        # Save the file temporarily
        filename = secure_filename(resume_file.filename)
        temp_path = os.path.join('/tmp/Uploads', filename)  # Use /tmp for Render compatibility
        os.makedirs('/tmp/Uploads', exist_ok=True)
        logger.debug(f"Saving file to {temp_path}")

        # Check file size before saving
        resume_file.seek(0, os.SEEK_END)
        file_size = resume_file.tell() / 1024  # Size in KB
        resume_file.seek(0)  # Reset file pointer
        if file_size == 0:
            logger.error(f"File {filename} is empty")
            return ""
        if file_size > 10240:  # 10MB limit
            logger.error(f"File {filename} is too large: {file_size:.2f} KB")
            return ""
        logger.debug(f"File size: {file_size:.2f} KB")

        # Save the file
        resume_file.save(temp_path)
        if not os.path.exists(temp_path):
            logger.error(f"Failed to save file to {temp_path}")
            return ""

        # Ensure file permissions are correct for Render
        os.chmod(temp_path, 0o644)

        # Verify file size after saving
        saved_size = os.path.getsize(temp_path) / 1024
        if saved_size == 0:
            logger.error(f"Saved file {temp_path} is empty")
            return ""

        # Extract text based on file type
        if ext == '.pdf':
            text = extract_text_from_pdf(temp_path)
        elif ext == '.docx':
            text = extract_text_from_docx(temp_path)

        if not text.strip():
            logger.warning(f"No text extracted from {temp_path}")
            return ""

        logger.info(f"Successfully extracted text from {filename}: {len(text)} characters")
        return text.strip()

    except Exception as e:
        logger.error(f"[ERROR in extract_text_from_resume]: {str(e)}")
        return ""
    finally:
        # Clean up the temporary file
        try:
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
                logger.debug(f"Cleaned up temporary file: {temp_path}")
        except Exception as e:
            logger.error(f"Error cleaning up temporary file {temp_path}: {str(e)}")

def analyze_resume_with_openai(resume_text, atsfix=False):
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
        suggestions = response.choices[0].message.content.strip()
        return {"text": resume_text, "suggestions": suggestions}

    except Exception as e:
        logger.error(f"[ERROR in analyze_resume_with_openai]: {str(e)}")
        return {"error": f"Failed to analyze resume: {str(e)}"}

def check_ats_compatibility(file_path):
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

        # Initialize issues and score
        issues = []
        score = 100

        # Heuristic checks
        if not re.search(r'[\w\.-]+@[\w\.-]+', text, re.IGNORECASE):
            issues.append("❌ Issue: Missing email address - ATS systems require contact information.")
            score -= 15
        else:
            issues.append("✅ Passed: Email address present.")

        if not re.search(r'\+?\d[\d\s\-]{8,}', text):
            issues.append("❌ Issue: Missing phone number - ATS systems require contact information.")
            score -= 10
        else:
            issues.append("✅ Passed: Phone number present.")

        section_headings = ['education', 'experience', 'skills', 'certifications', 'projects']
        found_headings = [heading for heading in section_headings if heading in text.lower()]
        if len(found_headings) < 3:
            issues.append(f"❌ Issue: Insufficient section headings (found: {', '.join(found_headings)}) - ATS systems rely on clear sections like Education, Experience, Skills.")
            score -= 25
        else:
            issues.append(f"✅ Passed: Found key section headings: {', '.join(found_headings)}.")

        common_keywords = ['python', 'javascript', 'sql', 'project management', 'communication', 'teamwork', 'leadership']
        found_keywords = [kw for kw in common_keywords if kw in text.lower()]
        if len(found_keywords) < 3:
            issues.append(f"❌ Issue: Limited keywords (found: {', '.join(found_keywords)}) - ATS systems prioritize relevant keywords.")
            score -= 20
        else:
            issues.append(f"✅ Passed: Found relevant keywords: {', '.join(found_keywords)}.")

        # Check for complex formatting (e.g., headers/footers, tables)
        if ext == ".pdf":
            try:
                doc = fitz.open(file_path)
                for page in doc:
                    if page.get_text("dict")['blocks']:
                        blocks = page.get_text("dict")['blocks']
                        for block in blocks:
                            if block['type'] == 0:  # Text block
                                for line in block['lines']:
                                    for span in line['spans']:
                                        if span['size'] > 20 or span['flags'] & 16:  # Large font or header
                                            issues.append("❌ Issue: Possible header/footer detected - ATS may skip these.")
                                            score -= 10
                                            break
            except Exception as e:
                logger.warning(f"Failed to check PDF formatting: {str(e)}")

        # AI-based ATS check
        if client:
            prompt = f"""
You are an advanced ATS scanner. Review this resume and provide a detailed list of compatibility checks in this format:

✅ Passed: Proper section headings used  
❌ Issue: No mention of technical skills  
✅ Passed: Education section is clear

Focus on ATS-specific criteria:
- Presence of contact information (email, phone, location)
- Clear, standard section headings (Education, Experience, Skills, Certifications, Projects)
- Use of relevant, job-specific keywords (technical skills, soft skills, tools)
- Avoidance of complex formatting (headers, footers, tables, images, non-standard fonts)
- Proper date formats (e.g., MM/YYYY or Month YYYY)
- Quantifiable achievements (e.g., "Increased sales by 20%")
- Consistency in bullet points and structure

Assign a weight to each issue (1-10) to deduct from the score (e.g., missing email = 8, missing keywords = 6).
Return the checks as a list and a total score deduction.

Example output:
[
  "✅ Passed: Proper section headings used",
  "❌ Issue: No mention of technical skills (weight: 6)",
  "✅ Passed: Education section is clear"
]
Total score deduction: 6

Text:
{text[:6000]}
            """

            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            ai_output = response.choices[0].message.content.strip()

            # Parse AI output
            try:
                lines = ai_output.splitlines()
                ai_checks = [line.strip() for line in lines if line.strip() and not line.startswith("Total score deduction")]
                deduction_line = next((line for line in lines if line.startswith("Total score deduction")), "Total score deduction: 0")
                ai_deduction = int(re.search(r'\d+', deduction_line).group()) if re.search(r'\d+', deduction_line) else 0
                score -= ai_deduction
                issues.extend(ai_checks)
            except Exception as e:
                logger.error(f"Failed to parse AI ATS output: {ai_output}, error: {str(e)}")
                issues.append("❌ Issue: Unable to perform advanced AI ATS check.")
                score -= 5
        else:
            issues.append("❌ Issue: Cannot perform AI-based ATS check due to missing OpenAI API key.")
            score -= 10

        score = max(0, score)
        return {"issues": issues, "score": score}

    except Exception as e:
        logger.error(f"[ERROR in check_ats_compatibility]: {str(e)}")
        return {"error": f"Failed to generate ATS compatibility report: {str(e)}"}

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

    if not client:
        return {"error": "Cannot format resume: OpenAI API key not set."}

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

# ✅ Updated generate_section_content with grammar/spell check + missing section handler
def generate_section_content(suggestion, full_text):
    if not client:
        return {"error": "Cannot generate section content: OpenAI API key not set."}

    try:
        sections = extract_resume_sections(full_text)
        existing_sections = list(sections.keys())

        prompt = f"""
You are an AI resume improvement expert.

Given the suggestion and full resume text, return a JSON with:
- 'section': which section to update (e.g. skills, summary, education)
- 'fixedContent': fixed or new content based on the suggestion
- Add grammar/spelling correction where needed
- If the section is missing, generate it with relevant content
- Use bullet points where applicable
- Respond in this format:
{{"section": "skills", "fixedContent": "- Python\\n- Communication\\n- Teamwork"}}

Suggestion:
{suggestion}

Resume:
{full_text[:6000]}
        """

        try:
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
                    return {"error": f"Failed to generate section content: {str(e)}"}

        except Exception as e:
            logger.error(f"[ERROR in OpenAI response]: {str(e)}")
            return {"error": f"Failed to contact OpenAI: {str(e)}"}

        # Clean and normalize
        result["section"] = result["section"].lower().replace(" ", "_")
        return result

    except Exception as e:
        logger.error(f"[ERROR in generate_section_content]: {str(e)}")
        return {"error": f"Failed to generate section content: {str(e)}"}

def extract_resume_sections(text):
    if not client:
        return {"error": "Cannot extract resume sections: OpenAI API key not set."}

    try:
        prompt = f"""
Split the following resume text into structured sections. Return a dictionary in JSON format where each key is a lowercase, underscore-separated section name (e.g., 'work_experience') and the value is the content as a string.

Include common sections like:
- personal_details
- summary
- objective
- education
- work_experience (also map 'experience' to this)
- internship
- projects
- technical_skills (also map 'skills' to this)
- soft_skills
- certifications
- courses
- achievements
- awards
- languages
- hobbies
- extracurricular
- volunteering (also map 'volunteer_experience' to this)
- publications
- research_projects
- strengths
- references

Instructions:
- Normalize section names (e.g., map 'experience' to 'work_experience', 'skills' to 'technical_skills', 'volunteer_experience' to 'volunteering').
- Avoid duplicate sections by merging content under the normalized section name.
- Exclude any section that is not found.
- Ensure the output is valid JSON.
- Order sections as follows: personal_details, summary, objective, technical_skills, work_experience, education, certifications, languages, hobbies, projects, volunteering, achievements, publications, references.

Resume:
{text[:6000]}
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )

        raw_output = response.choices[0].message.content.strip()

        # Replace null with empty string
        clean_output = raw_output.replace("null", "\"\"")

        sections = json.loads(clean_output)
        
        # Ensure content is a string, not a list
        for key in sections:
            if isinstance(sections[key], list):
                sections[key] = '\n'.join(sections[key]).strip()

        return sections

    except json.JSONDecodeError as e:
        logger.error(f"[JSON Decode Error in extract_resume_sections]: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"[ERROR in extract_resume_sections]: {str(e)}")
        return {}

def extract_keywords_from_jd(jd_text):
    if not client:
        return "Cannot extract keywords: OpenAI API key not set."

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
        return "Failed to extract keywords from job description."

def generate_resume_summary(name, role, experience, skills):
    if not client:
        return "OpenAI API key not set. Cannot generate summary."

    try:
        prompt = f"""
You are a professional resume expert.

Write a concise 2–3 line professional summary for the following person:
- Name: {name}
- Role: {role}
- Experience: {experience}
- Skills: {skills}

Make it ATS-friendly, use action words, and highlight strengths. Do not include heading or labels.
        """

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in generate_resume_summary]: {str(e)}")
        return "Failed to generate summary due to AI error."

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
        return "OpenAI API key not set. Cannot analyze job description."

    try:
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

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in analyze_job_description]: {str(e)}")
        return "Failed to analyze job description."
def generate_michelle_template_html(sections):
    def list_items(text):
        return ''.join(f"<li>{line.strip()}</li>" for line in text.strip().split('\n') if line.strip())

    name = sections.get("personal_details", "").split('\n')[0] if sections.get("personal_details") else "Your Name"
    title = sections.get("summary", "").split('\n')[0] if sections.get("summary") else "Your Role"

    phone = email = location = website = ""
    details = sections.get("personal_details", "").split('\n')[2:]
    if len(details) > 0: phone = details[0]
    if len(details) > 1: email = details[1]
    if len(details) > 2: location = details[2]
    if len(details) > 3: website = details[3]

    return f"""
    <div class='resume-wrapper' style='max-width:850px;margin:auto;background:#fff;border:1px solid #ccc;box-shadow:0 0 10px rgba(0,0,0,0.1);'>
      <div class='header' style='background:#d3d3d3;padding:30px;text-align:center;'>
        <h1>{name}</h1>
        <h2>{title}</h2>
      </div>
      <div class='content' style='display:flex;padding:30px;'>
        <div class='left-panel' style='width:30%;background:#f5f5f5;padding-right:20px;border-right:1px solid #ccc;box-sizing:border-box;'>
          <h3>Contact</h3>
          <div class='contact-item'>📞 {phone}</div>
          <div class='contact-item'>✉️ {email}</div>
          <div class='contact-item'>📍 {location}</div>
          <div class='contact-item'>🌐 {website}</div>
          <h3>Education</h3>
          <ul>{list_items(sections.get('education', ''))}</ul>
          <h3>Skills</h3>
          <ul>{list_items(sections.get('skills', ''))}</ul>
        </div>
        <div class='right-panel' style='width:70%;padding-left:30px;box-sizing:border-box;'>
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
# ✅ Updated resume_ai_analyzer.py (Add this to bottom of file)

def check_ats_compatibility_fast(text):
    score = 100
    issues = []

    if not re.search(r'\b\w+@\w+\.\w+\b', text):
        issues.append("❌ Missing email address")
        score -= 15
    else:
        issues.append("✅ Email address found")

    if not re.search(r'\+?\d[\d\s\-]{8,}', text):
        issues.append("❌ Missing phone number")
        score -= 10
    else:
        issues.append("✅ Phone number present")

    keywords = ["education", "experience", "skills", "certifications"]
    found = [k for k in keywords if k in text.lower()]
    if len(found) < 3:
        issues.append(f"❌ Weak section headings (found: {', '.join(found)})")
        score -= 20
    else:
        issues.append(f"✅ Found section headings: {', '.join(found)}")

    return {"score": max(0, score), "issues": issues}


def check_ats_compatibility_deep(file_path):
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(file_path)
        elif ext == ".docx":
            text = extract_text_from_docx(file_path)
        else:
            return {"error": "Unsupported file type"}

        if not text.strip():
            return {"error": "No readable text found in resume"}

        # Basic Checks
        score = 100
        issues = []

        if not re.search(r'\b\w+@\w+\.\w+\b', text):
            issues.append("❌ Missing email address")
            score -= 10

        if not re.search(r'\+?\d[\d\s\-]{8,}', text):
            issues.append("❌ Missing phone number")
            score -= 10

        if len(text.split()) < 150:
            issues.append("❌ Resume too short")
            score -= 10

        # AI validation
        prompt = f"""
You are an ATS expert. Check the following resume and give up to 5 issues:
Resume:
{text[:6000]}
Return in this format:
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

