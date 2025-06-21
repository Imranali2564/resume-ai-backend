# PASTE THIS ENTIRE CODE INTO YOUR resume_ai_analyzer.py FILE

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

        # Check 1: Contact Information (Email, Phone, Location)
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

        # Check 2: Required Sections
        section_headings = ['education', 'experience', 'skills', 'certifications', 'projects']
        found_headings = [heading for heading in section_headings if heading in text.lower()]
        if len(found_headings) < 3:
            missing_sections = list(set(section_headings) - set(found_headings))
            issues.append(f"❌ Missing key sections - Add {', '.join(missing_sections)}.")
            score -= 20
        else:
            issues.append("✅ Key sections found.")

        # Check 3: Keyword Density (Common ATS Keywords)
        common_keywords = [
            'communication', 'leadership', 'teamwork', 'project management', 'problem-solving',
            'python', 'javascript', 'sql', 'java', 'excel', 'data analysis', 'marketing',
            'sales', 'customer service', 'agile', 'scrum', 'cloud', 'aws', 'azure'
        ]
        found_keywords = [kw for kw in common_keywords if kw in text.lower()]
        keyword_density = len(found_keywords) / len(common_keywords)
        if keyword_density < 0.2:  # Less than 20% of common keywords found
            issues.append("❌ Low keyword density - Add more keywords like 'communication', 'leadership', or 'teamwork'.")
            score -= 15
        else:
            issues.append("✅ Sufficient keywords found.")

        # Check 4: Content Length (Too Short or Too Long)
        word_count = len(text.split())
        if word_count < 150:
            issues.append("❌ Resume too short - Add more details to your experience or skills.")
            score -= 10
        elif word_count > 1000:
            issues.append("❌ Resume too long - Shorten your resume to 1-2 pages.")
            score -= 10
        else:
            issues.append("✅ Appropriate content length.")

        # Check 5: Formatting Issues (e.g., Use of Headers, Fonts, Special Characters)
        if re.search(r'[^\x00-\x7F]', text):  # Non-ASCII characters
            issues.append("❌ Special characters detected - Use standard ASCII characters to ensure ATS compatibility.")
            score -= 10
        else:
            issues.append("✅ No special characters detected.")

        # Check 6: Quantifiable Achievements
        if not re.search(r'\d+\%|\d+\s*(hours|projects|clients|sales)', text, re.IGNORECASE):
            issues.append("❌ Missing quantifiable achievements - Add metrics like 'increased sales by 20%' or 'managed 5 projects'.")
            score -= 10
        else:
            issues.append("✅ Quantifiable achievements found.")

        # Check 7: Action Verbs
        action_verbs = ['led', 'developed', 'managed', 'improved', 'designed', 'implemented', 'analyzed', 'increased']
        found_verbs = [verb for verb in action_verbs if verb in text.lower()]
        if len(found_verbs) < 2:
            issues.append("❌ Limited use of action verbs - Start bullet points with verbs like 'led', 'developed', or 'improved'.")
            score -= 10
        else:
            issues.append("✅ Action verbs used effectively.")

        # Limit score to 0-100 range
        score = max(0, min(100, score))
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
        # 🧼 Clean personal info from inputs
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
    def list_items(text, section_type="other"):
        if not text:
            return ""
        lines = text.strip().split("\n")
        # For education, avoid bullet points for short entries unless they are clearly list items
        if section_type == "education":
            # Check if the content looks like a list (e.g., multiple degrees or details with specific patterns)
            if len(lines) > 1 and any(line.startswith(("-", "•")) for line in lines):
                return "".join(
                    f"<li>{line.strip().lstrip('-• ').strip()}</li>"
                    for line in lines
                    if line.strip()
                )
            # For single-line or short education entries, use a paragraph instead
            return f"<p>{text.strip()}</p>"
        # For other sections, keep bullet points
        return "".join(
            f"<li>{line.strip().lstrip('-• ').strip()}</li>"
            for line in lines
            if line.strip()
        )

    # Extract personal details more reliably
    name = "Your Name"
    phone = email = location = website = ""
    personal_lines = sections.get("personal_details", "").split("\n")

    # Enhanced parsing for personal details
    for line in personal_lines:
        line = line.strip()
        if not line:
            continue
        # First line without email, phone, or website is likely the name
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
        elif (
            "location" in line.lower()
            or "city" in line.lower()
            or "state" in line.lower()
            or "📍" in line
        ):
            location = line.replace("📍", "").strip()
        elif "website" in line.lower() or "www" in line.lower() or "🌐" in line:
            website = line.replace("🌐", "").strip()

    # Fallback for name if not found
    if name == "Your Name":
        for section in sections.values():
            if section:
                lines = section.split("\n")
                for line in lines:
                    line = line.strip()
                    if (
                        line
                        and len(line) < 50
                        and "@" not in line
                        and not re.search(r"\d{5,}", line)
                        and not any(x in line.lower() for x in ["www", ".com", "city", "state"])
                    ):
                        name = line
                        break
                if name != "Your Name":
                    break

    title = sections.get("summary", "").split("\n")[0] if sections.get("summary") else "Your Role"

    # Adjust width and padding to reduce left-right space
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

    # Limit to 5 issues max
    issues = issues[:5]
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
            issues.append("❌ Missing email - Add your email.")
            score -= 10

        if not re.search(r'\+?\d[\d\s\-]{8,}', text):
            issues.append("❌ Missing phone - Add your phone number.")
            score -= 10

        if len(text.split()) < 150:
            issues.append("❌ Resume too short")
            score -= 10

        # AI validation
        prompt = f"""
You are an ATS expert. Check the following resume and give up to 5 issues:
Resume:
{text[:6000]}

Also flag unnecessary personal information like Marital Status, Date of Birth, Gender, Nationality, or Religion as issues with a reason to remove them.
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
import re

def remove_unnecessary_personal_info(text):
    # Remove Date of Birth
    text = re.sub(r"(Date of Birth|DOB)[:\-]?\s*\d{1,2}[\/\-\.]?\d{1,2}[\/\-\.]?\d{2,4}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(Date of Birth|DOB)[:\-]?\s*[A-Za-z]+\s+\d{1,2},?\s+\d{4}", "", text, flags=re.IGNORECASE)

    # Remove Gender
    text = re.sub(r"Gender[:\-]?\s*(Male|Female|Other|Prefer not to say)", "", text, flags=re.IGNORECASE)

    # Remove Marital Status
    text = re.sub(r"Marital Status[:\-]?\s*(Single|Married|Divorced|Widowed)", "", text, flags=re.IGNORECASE)

    # Remove Nationality
    text = re.sub(r"Nationality[:\-]?\s*\w+", "", text, flags=re.IGNORECASE)

    # Remove Religion
    text = re.sub(r"Religion[:\-]?\s*\w+", "", text, flags=re.IGNORECASE)

    # Remove long address formats, retain only city and country
    # e.g. "T-602, Street No 12, Gautampuri, New Delhi 110053, India" → "New Delhi, India"
    text = re.sub(r'((Address|Location)[:\-]?)?\s*[\w\s\-\,\.\/]*?(\b[A-Z][a-z]+\b)[,\s]+(\b[A-Z][a-z]+\b)(?:\s*\d{5,6})?(,\s*India)?', r'\3, \4', text)

    return text



def fix_ats_issue(resume_text, issue_text):
    section = "misc"
    fixed_content = resume_text

    if "Summary/Objective section missing" in issue_text:
        section = "summary"
        fixed_content += "\n\nSummary:\nA highly motivated professional with a passion for excellence."

    elif "Education section missing" in issue_text:
        section = "education"
        fixed_content += "\n\nEducation:\nB.Tech in Computer Science, ABC University"

    elif "Experience section missing" in issue_text:
        section = "experience"
        fixed_content += "\n\nExperience:\nSoftware Engineer at XYZ Ltd (2020 - Present)"

    elif "Missing relevant keywords" in issue_text:
        section = "skills"
        fixed_content += "\n\nSkills:\nPython, Project Management"

    elif "Contains personal info" in issue_text:
        section = "contact"
        fixed_content = re.sub(r"(?i)(Date of Birth|DOB|Gender|Marital Status).*?\n", "", fixed_content)

    elif "grammar error" in issue_text:
        section = "summary"
        fixed_content = fixed_content.replace("responsible of", "responsible for")

    return {"section": section, "fixedContent": fixed_content}

# =====================================================================
# NEW STABLE AI FUNCTIONS
# =====================================================================

def refine_list_section(section_name, section_text):
    """
    AI HELPER: Cleans up list-based sections like 'Languages' to ensure only relevant items are included.
    """
    if not section_text or not client: return [line.strip() for line in section_text.split('\n') if line.strip()]
    
    logger.info(f"Refining list section: '{section_name}'...")
    prompt = f"""
    You are a data cleaning expert. The following text is from the "{section_name}" section of a resume.
    Your job is to clean this text and return only the relevant items as a JSON list of strings.

    For example, if the section is "Languages", only return actual languages.
    If the section is "Skills", only return actual skills.
    
    Remove any items that do not belong.

    Text to clean:
    ---
    {section_text}
    ---
    
    Return a single JSON object with one key, "cleaned_list", containing the list of strings.
    Example: {{"cleaned_list": ["Hindi", "English"]}}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("cleaned_list", [])
    except Exception as e:
        logger.error(f"Could not refine section {section_name}: {e}")
        # Fallback to simple split if AI fails
        return [line.strip() for line in section_text.split('\n') if line.strip()]

# 'resume_ai_analyzer.py' में इस फंक्शन को पूरी तरह से बदलें
# 'resume_ai_analyzer.py' में पुराने extract_resume_sections_safely को हटाकर यह नया संस्करण डालें

def extract_resume_sections_safely(text):
    """
    Extracts resume sections using a more reliable, multi-step process.
    This single function contains all the necessary logic and helper functions inside it.
    """
    logger.info("Extracting sections with FINAL v12 (Self-Contained Step-by-Step)...")
    if not client: return {"error": "OpenAI client not initialized."}

    # --- Helper functions are now defined inside for simplicity ---

    def _split_resume_into_raw_sections(text_to_split):
        prompt = f"""
        You are a document layout analyzer. Your only job is to split the following resume text into a JSON object based on its section headings.
        The keys should be the section headings (e.g., "PROFILE", "WORK EXPERIENCE").
        The values should be the raw text content under each heading.
        Include a special key "header" for all text BEFORE the first major section heading.

        Resume Text: --- {text_to_split[:8000]} ---
        Return ONLY the JSON object.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Helper function _split_resume_into_raw_sections FAILED: {e}")
            return None

    def _parse_resume_header(header_text_to_parse):
        prompt = f"""
        From the following text block, extract these details into a JSON object:
        1. "name": The person's full name.
        2. "job_title": The professional title (e.g., "Accounting Executive").
        3. "contact": All contact info (email, phone, address, links) as a single string.
        4. "summary": The professional summary or profile text as a single string.
        Extract text VERBATIM.
        
        Text to parse: --- {header_text_to_parse} ---
        Return ONLY the JSON object.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Helper function _parse_resume_header FAILED: {e}")
            return {}

    def _parse_structured_list_section(section_name, section_text):
        if not section_text: return []
        structure_map = {
            'work_experience': '[{"title": "string", "company": "string", "duration": "string", "details": ["list of strings"]}]',
            'education': '[{"degree": "string", "school": "string", "duration": "string", "details": ["list of strings"]}]',
            'projects': '[{"title": "string", "description": "string"}]'
        }
        json_structure = structure_map.get(section_name, '[]')
        prompt = f"""
        Parse the following text from a "{section_name}" section into a JSON list using this format: {json_structure}.
        Extract all text VERBATIM.
        
        Text to parse: --- {section_text} ---
        Return a JSON object with one key "{section_name}" containing the list.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content).get(section_name, [])
        except Exception as e:
            logger.error(f"Helper function _parse_structured_list_section for '{section_name}' FAILED: {e}")
            return []

    def _parse_simple_list_section(section_name, section_text):
        if not section_text: return []
        prompt = f"""
        Parse the text from the '{section_name}' section into a simple JSON list of strings. Extract each item VERBATIM.
        Text to parse: --- {section_text} ---
        Return a JSON object with one key "{section_name}" containing the list of strings.
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], response_format={"type": "json_object"}
            )
            # Handle both string and list outputs for skills gracefully
            result = json.loads(response.choices[0].message.content).get(section_name)
            if isinstance(result, str):
                return result.split('\n')
            return result or []
        except Exception as e:
            logger.error(f"Helper function _parse_simple_list_section for '{section_name}' FAILED: {e}")
            return []

    # --- Main Logic Starts Here ---

    # Step 1: Split resume into raw sections
    raw_sections = _split_resume_into_raw_sections(text)
    if not raw_sections:
        return {"error": "AI failed to split the resume into sections."}

    final_data = {}
    normalized_sections = {key.strip().lower().replace(' ', '_'): value for key, value in raw_sections.items()}

    # Step 2: Parse header and summary
    header_text = normalized_sections.pop('header', '')
    for key in ['summary', 'profile', 'objective']:
        if key in normalized_sections:
            header_text += "\n\n" + normalized_sections.pop(key, '')
    if header_text:
        final_data.update(_parse_resume_header(header_text))

    # Step 3: Parse all other known sections
    final_data['work_experience'] = _parse_structured_list_section('work_experience', normalized_sections.get('work_experience'))
    final_data['education'] = _parse_structured_list_section('education', normalized_sections.get('education'))
    final_data['projects'] = _parse_structured_list_section('projects', normalized_sections.get('projects'))
    final_data['skills'] = _parse_simple_list_section('skills', normalized_sections.get('skills'))
    final_data['languages'] = _parse_simple_list_section('languages', normalized_sections.get('languages'))
    final_data['certifications'] = _parse_simple_list_section('certifications', normalized_sections.get('certification/short_courses') or normalized_sections.get('certifications'))

    # Step 4: Combine remaining sections into 'additional_information'
    processed_keys = {'header', 'summary', 'profile', 'objective', 'work_experience', 'education', 'projects', 'skills', 'languages', 'certifications', 'certification/short_courses', 'contact'}
    remaining_keys = set(normalized_sections.keys()) - processed_keys
    additional_info_list = [f"{key.replace('_', ' ').title()}:\n{normalized_sections[key]}" for key in remaining_keys if normalized_sections[key]]
    final_data['additional_information'] = "\n\n".join(additional_info_list)

    # Step 5: Ensure all expected keys exist for the frontend
    all_expected_keys = ["name", "job_title", "contact", "summary", "work_experience", "education", "skills", "certifications", "languages", "projects", "additional_information", "references"]
    for key in all_expected_keys:
        if key not in final_data:
            final_data[key] = None
    
    logger.info("Step-by-step extraction completed successfully.")
    return final_data

# 'resume_ai_analyzer.py' में इस फंक्शन को भी बदलें
def generate_field_aware_ats_report(extracted_data, job_field="General"):
    """
    Generates suggestions based on the detected field and found sections.
    """
    logger.info(f"Generating field-aware ATS report for: {job_field}")
    if not client: 
        return {"error": "OpenAI client not initialized."}

    # Create a summary of sections that were actually found in the resume
    found_sections_str = ", ".join(extracted_data.keys())

    # Define which sections are important for which field, based on the user's document
    field_specific_sections = {
        'Technology / IT': 'GitHub Projects, Technical Stack, Cloud Certifications',
        'Design / UI-UX': 'Portfolio Link, Design Tools, User Research Projects',
        'Legal / Law': 'Bar Membership, Case Portfolio, Legal Publications',
        'Healthcare / Medical': 'Medical License, Clinical Rotations, Procedures Handled',
        'Business / Marketing': 'KPIs Achieved, Campaigns Handled, Marketing Tools',
        'Academia / Education': 'Publications, Research Interests, Conferences Attended'
    }
    # Get the relevant sections for the detected field, or use a default
    relevant_extra_sections = field_specific_sections.get(job_field, 'Projects, Certifications')

    prompt = f"""
    You are an expert career coach for the **{job_field}** field.
    An AI has extracted the following sections from a candidate's resume: **{found_sections_str}**.

    Based on the standards for a strong **{job_field}** resume, please provide feedback.

    **TASKS:**
    1.  **Check for Missing Field-Specific Sections:** For a **{job_field}** role, important sections include: **{relevant_extra_sections}**. Is the most critical of these missing from the found sections? If so, create an "issue_to_fix" suggesting to add it.
    2.  **Check for Quantifiable Achievements:** Does the "Work Experience" section contain numbers, percentages, or dollar amounts to show impact? If not, this is an issue.
    3.  **Check Formatting:** Briefly check if the resume seems to use bullet points for experience and has a clear format.
    4.  **Provide General Feedback:** Generate a few "passed_checks" for things the resume does well.

    **INSTRUCTIONS:**
    - Return a JSON object with two keys: "passed_checks" and "issues_to_fix".
    - Create a maximum of 3 "issues_to_fix".
    - Start every item with an emoji (✅ for passed, ❌ for issue).
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        report_data = json.loads(response.choices[0].message.content)
        score = max(40, 100 - (len(report_data.get("issues_to_fix", [])) * 7))
        report_data["score"] = score
        return report_data
    except Exception as e:
        logger.error(f"Field-aware ATS report generation failed: {e}")
        return {"score": 0, "passed_checks": [], "issues_to_fix": ["❌ AI analysis failed."]}

# 'resume_ai_analyzer.py' में इस फंक्शन को भी बदलें
def generate_targeted_fix(suggestion, full_text):
    if not client: return {"error": "OpenAI API key not set."}
    logger.info(f"Generating TARGETED fix for suggestion: {suggestion}")
    
    # ### PROMPT IMPROVEMENT ###
    # Added explicit instruction to match the original data structure for the section being fixed.
    prompt = f"""
    You are an AI resume expert. Your task is to fix ONE specific issue in a resume based on the given suggestion.

    SUGGESTION:
    {suggestion}

    FULL RESUME TEXT (for context):
    {full_text[:8000]}

    INSTRUCTIONS:
    - Focus ONLY on the suggestion. Do not fix anything else.
    - Based on the suggestion, identify which section of the resume needs to be changed (e.g., "skills", "work_experience", "projects").
    - Generate the improved content for that section.
    - **CRITICAL:** The format of your "fixedContent" MUST match the likely original format of that section. For example:
        - If fixing 'skills', 'languages', or 'certifications', 'fixedContent' should be a list of strings.
        - If fixing 'work_experience', 'fixedContent' should be a list of objects.
        - If fixing 'summary', 'fixedContent' should be a single string.
    
    Respond with a single JSON object with two keys:
    1. "section": The name of the section you fixed (e.g., "skills", "projects").
    2. "fixedContent": The new, improved text or list for that section, in the correct format.
    
    Example Response for a skills fix: {{"section": "skills", "fixedContent": ["Python", "JavaScript", "Project Management", "QuickBooks", "SAP"]}}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a resume fixing assistant that responds in perfect JSON."}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        fix_result = json.loads(response.choices[0].message.content)
        
        if "section" not in fix_result or "fixedContent" not in fix_result:
            raise ValueError("AI response did not contain 'section' or 'fixedContent'.")
            
        return fix_result
    except Exception as e:
        logger.error(f"[ERROR in generate_targeted_fix]: {e}")
        return {"error": "AI failed to generate the targeted fix."}

def calculate_new_score(current_score, issue_text):
    """
    NEW: A predictable, rule-based function to update the score.
    """
    logger.info(f"Calculating new score. Current: {current_score}")
    increment = 0
    issue_text = issue_text.lower()
    
    if "missing" in issue_text:
        increment = 10  # Critical fix
    elif "achievements" in issue_text or "quantifiable" in issue_text:
        increment = 8   # Major fix
    elif "wordiness" in issue_text or "formatting" in issue_text:
        increment = 6   # Medium fix
    else:
        increment = 5   # Minor fix
        
    new_score = min(100, current_score + increment)
    logger.info(f"Score incremented by {increment}. New score: {new_score}")
    return new_score

def detect_resume_field(text):
    """
    NEW: Detects the professional field from the resume text.
    """
    logger.info("Detecting resume's professional field...")
    if not client: return "General"
    
    prompt = f"""
    Based on the content of the following resume, identify the most likely professional field.
    Choose from one of these options: 'Technology / IT', 'Design / UI-UX', 'Legal / Law', 'Healthcare / Medical', 'Business / Marketing', 'Academia / Education', 'Engineering', 'Hospitality / Travel', 'General'.
    Respond with ONLY the name of the field.

    Resume Text:
    ---
    {text[:2000]}
    ---
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        field = response.choices[0].message.content.strip()
        logger.info(f"Detected Field: {field}")
        return field
    except Exception as e:
        logger.error(f"Could not detect resume field: {e}")
        return "General"

def extract_resume_data_dynamically(text, job_field):
    """
    NEW: Dynamically extracts all sections based on the resume's content and detected field.
    """
    logger.info(f"Dynamically extracting all sections for field: {job_field}...")
    if not client: return {"error": "OpenAI client not initialized."}

    prompt = f"""
    You are a resume parsing expert. Your task is to read the entire resume text and extract ALL sections you can find.
    The resume is for the **{job_field}** field. Pay attention to field-specific sections (e.g., 'GitHub' for Tech, 'Portfolio' for Design).

    **INSTRUCTIONS:**
    1.  **Identify ALL Sections:** Find every section with a clear heading.
    2.  **Extract VERBATIM:** Extract the content under each heading EXACTLY as it appears. DO NOT change or summarize the text.
    3.  **Create JSON:** Return a single JSON object where each key is the section heading (e.g., "Work Experience", "Technical Skills") and the value is the text content of that section.
    4.  **Header Info:** Group all text before the first main heading under a special key named "Header".

    Resume Text:
    ---
    {text[:8000]}
    ---
    Return ONLY the raw JSON object.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": "You are a robot that perfectly converts resume text into a JSON object of its sections."},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"Dynamic extraction failed: {e}")
        return {"error": "AI failed to extract sections from this resume."}
