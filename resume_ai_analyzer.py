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
You are an ATS expert. Check the following resume and give up to 5 issues.

Also flag any unnecessary personal information such as:
- Marital Status
- Date of Birth
- Gender
- Nationality
- Religion

These are not required in a professional resume and should be removed for better ATS compatibility.

Resume:
{text[:6000]}

Return your output as a list like this:
["✅ Passed: ...", "❌ Issue: ..."]
Only include meaningful points.
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


def fix_ats_issue(issue_text, extracted_data):
    section = "misc"
    fixed_content = extracted_data # Initialize with the extracted data

    if "Summary/Objective section missing" in issue_text:
        section = "summary"
        fixed_content["summary"] = "A highly motivated professional with a passion for excellence."

    elif "Education section missing" in issue_text:
        section = "education"
        fixed_content["education"] = [{"degree": "B.Tech in Computer Science", "school": "ABC University", "duration": "YYYY -YYYY", "details": []}]

    elif "Experience section missing" in issue_text:
        section = "work_experience"
        fixed_content["work_experience"] = [{"title": "Software Engineer", "company": "XYZ Ltd", "duration": "YYYY - Present", "details": ["Developed and maintained web applications."]}]

    elif "Missing relevant keywords" in issue_text:
        section = "skills"
        # Assuming extracted_data['skills'] is a list of strings
        if not fixed_content.get("skills"):
            fixed_content["skills"] = ["Python", "Project Management"]
        else:
            fixed_content["skills"].extend(["Python", "Project Management"])
            fixed_content["skills"] = list(set(fixed_content["skills"])) # Remove duplicates

    elif "Contains personal info" in issue_text:
        section = "contact"
        # This fix implies altering the original contact info within extracted_data if it contains sensitive info.
        # This part might need more sophisticated AI fix or manual intervention.
        # For now, we'll just indicate the section.
        pass

    elif "grammar error" in issue_text:
        # For grammar, we need to know which section has the error.
        # This is a generic example, you'd need more specific logic from AI.
        section = "summary" # Assuming summary for now
        fixed_content["summary"] = fixed_content.get("summary", "").replace("responsible of", "responsible for")

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
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("cleaned_list", [])
    except Exception as e:
        logger.error(f"Could not refine section {section_name}: {e}")
        # Fallback to simple split if AI fails
        return [line.strip() for line in section_text.split('\n') if line.strip()]

def extract_resume_sections_safely(text):
    logger.info("Extracting resume sections with FINAL v9 (Simplified & Robust) AI strategy...")
    if not client:
        return {"error": "OpenAI client not initialized."}
    
    TOKEN_LIMIT_IN_CHARS = 40000
    if len(text) > TOKEN_LIMIT_IN_CHARS:
        logger.warning(f"Resume text is too long, truncating to {TOKEN_LIMIT_IN_CHARS} characters.")
        text = text[:TOKEN_LIMIT_IN_CHARS]

    prompt = f"""
    You are a world-class resume parsing system. The following text may be jumbled.
    Your task is to intelligently parse this text and reconstruct a perfectly structured JSON object.

    **Crucial Instructions:**
    1.  **Associate Details:** Correctly associate all details with their parent items.
    2.  **Map Certifications:** Look for headings like "Certifications", "Additional Courses", "Training", "Licenses", or "Professional Development" and map ALL of them to the `certifications` key. This is very important.
    3.  **Clean Output:** If a section is not found, its value must be null.

    **JSON STRUCTURE REQUIRED:**
    - "name": string
    - "job_title": string
    - "contact": string
    - "summary": string
    - "work_experience": list of objects `[{{"title": string, "company": string, "duration": string, "details": list of strings}}]`
    - "education": list of objects `[{{"degree": string, "school": string, "duration": string, "details": list of strings}}]`
    - "skills": list of strings
    - "languages": list of strings
    - "certifications": list of strings  <-- All course-related info should come here.
    - "projects": list of objects `[{{"title": string, "description": string, "details": list of strings}}]`

    **Resume Text to Parse:**
    ---
    {text[:8000]}
    ---
    Return ONLY the raw JSON object.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        final_data = json.loads(response.choices[0].message.content)
        
        all_possible_keys = ["name", "job_title", "contact", "summary", "work_experience", "education", "skills", "certifications", "languages", "projects", "awards", "volunteer_experience"]
        for key in all_possible_keys:
            if key not in final_data:
                final_data[key] = None

        logger.info(f"Final data extracted successfully. Keys: {list(final_data.keys())}")
        return final_data
    except Exception as e:
        logger.error(f"Context-aware AI parsing failed: {e}")
        return {"error": "The AI failed to parse the resume. The document format might be too complex."}

def generate_final_detailed_report(text, extracted_data):
    logger.info("Generating FINAL v16 Detailed Audit with Skills Check...")
    if not client:
        return {"error": "OpenAI client not initialized."}

    prompt = f"""
    You are a professional resume auditor. Analyze the provided resume text and return a detailed JSON report.
    For each check, provide a "status" ('pass', 'fail', or 'improve') and a "comment".

    **Required JSON Output Structure:**
    {{
        "contact_info_check": {{"status": "pass/fail", "comment": "Your comment here."}},
        "summary_check": {{"status": "pass/improve", "comment": "Your comment here."}},
        "experience_metrics_check": {{"status": "pass/fail", "comment": "e.g., The work experience lacks quantifiable results."}},
        "action_verbs_check": {{"status": "pass/fail", "comment": "e.g., Sentences use passive voice like 'Responsible for'."}},
        "skills_format_check": {{"status": "pass/fail", "comment": "e.g., The skills section should contain keywords, not long sentences."}},
        "spelling_check": {{"status": "pass", "comment": "No major spelling errors found."}},
        "grammar_check": {{"status": "pass/fail", "comment": "e.g., Found a minor grammatical error in the summary."}}
    }}
    
    - For 'skills_format_check', if the skills section contains long descriptive sentences instead of keywords, set status to 'fail'. Otherwise, 'pass'.
    - Be specific and professional in your comments.

    **Resume Text to Analyze:**
    ---
    {text[:6000]}
    ---
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a resume auditor that responds in perfect, structured JSON."}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        report_data = json.loads(response.choices[0].message.content)
        return report_data
    except Exception as e:
        logger.error(f"Error in detailed report generation: {e}")
        return {"error": "AI analysis failed to generate a detailed report."}

def fix_resume_issue(issue_text, extracted_data):
    """
    FIXED: This new function takes the entire resume data object for better context,
    making the AI's job easier and more reliable.
    """
    if not client: 
        logger.error("OpenAI client not configured.")
        return {"error": "OpenAI API key not set."}
        
    logger.info(f"Generating fix for issue '{issue_text}' with full data context...")

    # Convert the resume data to a JSON string for the prompt
    resume_context = json.dumps(extracted_data, indent=2)

    prompt = f"""
    You are an expert AI resume editor. Your task is to fix a specific issue in the provided resume JSON data.

    **Resume Data (JSON format):**
    ```json
    {resume_context}
    ```

    **Issue to Fix:**
    "{issue_text}"

    **Instructions:**
    1.  Analyze the 'Issue to Fix' and locate the relevant section within the 'Resume Data'.
    2.  Modify ONLY the content of that specific section to resolve the issue. For example:
        - If the issue is "lacks quantifiable_achievements", add numbers and percentages to the `details` of the `work_experience` section.
        - If the issue is "action_verbs_passive_language", rewrite the sentences in `work_experience` to start with strong verbs.
        - If the issue is "contact_information", ensure the `contact` string contains a clear email and phone number.
    3.  Return a JSON object containing ONLY the name of the section you changed and its new, updated content.

    **Required Output Format (JSON):**
    {{"section": "key_of_the_changed_section", "fixedContent": "the_new_content_for_that_section"}}

    Example Output:
    {{"section": "work_experience", "fixedContent": [{{"title": "Tele Caller", "company": "Paisely Advisory Pvt, Ltd", "duration": "Having 6 Month Experience", "details": ["Contacted over 100 potential customers daily to promote products.", "Achieved a 15% higher customer satisfaction rate based on feedback.", "Maintained detailed records of over 2000 calls and interactions." ]}}]}}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "system", "content": "You are a resume fixing assistant that responds in perfect JSON."}, {"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        fix_result = json.loads(response.choices[0].message.content)

        if "section" not in fix_result or "fixedContent" not in fix_result:
            raise ValueError("AI response was malformed.")
        
        return fix_result
    except Exception as e:
        logger.error(f"[ERROR in fix_resume_issue]: {e}")
        return {"error": "AI failed to generate a fix for this issue."}


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

def get_field_suggestions(extracted_data, resume_text):
    logger.info("Running FINAL v9 Field & Suggestion analysis...")
    if not client:
        return {"field": "Unknown", "suggestions": []}

    prompt = f"""
    You are an expert Indian career coach and resume analyst. Your task is to analyze a resume to identify its professional field and suggest critical missing sections for improvement.

    **Step 1: Identify the Professional Field**
    Based on the skills (e.g., "Tally", "Python", "AutoCAD"), job titles (e.g., "Accountant", "Software Developer", "Civil Engineer"), and summary in the resume text, determine the most accurate professional field. Use common Indian job market fields like: "IT / Software Development", "Finance & Accounting", "Mechanical/Civil/Electrical Engineering", "Sales & Marketing", "Human Resources (HR)", "Graphic Design", "Healthcare", or "General / Fresher".

    **Step 2: Suggest Important Missing Sections**
    Here are the sections that were already found in the resume: {list(k for k, v in extracted_data.items() if v)}
    
    Now, as a career coach, analyze the FULL resume text. Based on the field you identified, suggest **only the most important and relevant sections** that are genuinely missing and would add significant value. For example:
    - For an "IT / Software Development" resume, if there's no link to GitHub/GitLab or a Portfolio, you MUST suggest adding "Projects" or "Portfolio / GitHub".
    - For a "Graphic Design" resume, a "Portfolio Link" (like Behance/Dribbble) is CRITICAL.
    - For a "Finance & Accounting" resume, if not present, a "Certifications" section (like Tally, NISM) is very valuable.
    - For any "Fresher" resume, a "Projects" or "Internships" section is highly recommended to showcase practical skills.
    - For a "Sales" resume, a "Key Achievements" section with sales targets met or exceeded is very impactful.
    
    Do NOT suggest adding a section if its content is already mentioned somewhere else in the resume. Be smart and contextual.

    **Instructions:**
    Return a single JSON object with two keys:
    1.  "field": A string with the name of the Indian-context field you identified.
    2.  "suggestions": A list of objects. Each object should be {{"type": "Required" or "Recommended", "section": "Section Name to Add"}}. Only suggest 2-3 of the MOST CRITICAL missing sections. "Required" is for must-haves (like a portfolio for a designer). "Recommended" is for strong value-adds.

    **Resume Text to Analyze:**
    ---
    {resume_text[:7000]} 
    ---
    
    Return ONLY the JSON object.
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo", 
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        
        detected_field = result.get("field", "General / Fresher")
        suggestions = result.get("suggestions", [])
        
        if not isinstance(suggestions, list):
             suggestions = []

        return {"field": detected_field, "suggestions": suggestions}
    
    except Exception as e:
        logger.error(f"Could not get field suggestions: {e}")
        return {"field": "General / Fresher", "suggestions": [{"type": "Recommended", "section": "Projects"}]}

def generate_smart_resume_from_keywords(data: dict) -> dict:
    """
    This function retrieves professionally rewritten content from AI for each resume section.
    It provides a response based on the fields filled by the user.
    """

    from openai import OpenAI
    import os

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    smart_resume = {}

    # <<< IMPROVED PROMPT FOR SKILLS SECTION >>>
    sections = {
        "summary": "Write a concise, impactful 2-3 line professional summary for a resume. Focus on key skills, experience, and career goals. If input is empty or insufficient, return ONLY an empty string. DO NOT use headings like 'Summary:'.",
        "experience": """For each work experience entry, convert the raw input into a list of 3-5 *very concise, action-verb-driven bullet points* for a resume. Each bullet point should be a single line, start with an action verb, and focus on quantifiable achievements and key responsibilities. Do NOT include job titles, companies, or dates in this output; ONLY the bullet points. If input is empty or insufficient, return ONLY an empty string.""",
        "education": """Reformat these education details into a standard resume education format. For each entry, provide:
        - Degree Name (on one line)
        - University/Institute, City, State/Country (on the next line, comma separated)
        - Graduation/Completion Year (on the same line as university, right-aligned, or clearly separated)
        If there are relevant bullet points (e.g., GPA, specializations, honors), list them concisely below the main entry, each starting with a bullet. If input is empty or insufficient, return ONLY an empty string.
Example Output Format:
B.Tech in Computer Science
Delhi University, Delhi, India
2019
• Relevant coursework: Data Structures, Algorithms, Machine Learning
• GPA: 3.8/4.0""",
        "skills": """From the following text, extract ONLY the individual skills. List each skill on a new line. If there are categories like 'Technical Skills', list the category on its own line ending with a colon.
        **Crucial Rule: DO NOT combine skills on one line. Never output something like \"JavaScript* React\".**
        Correct Example:        
        JavaScript
        React
        Python
        """,
        "projects": """For each project entry, provide the concise project title on one line, followed by a list of 2-4 *very concise, action-verb-driven bullet points*. Each bullet should highlight technologies used, your role, and *key achievements/outcomes, especially quantifiable results*. Do NOT include numbering (1., 2., 3.) or labels like 'Project Description:' or 'Outcome/Result:'. The project title should be clearly distinguishable (e.g., by being on its own line). If input is empty or insufficient, return ONLY an empty string.""",
        "certifications": """List each certification clearly, one per line. Include certification name, issuing body, and year if available. If input is empty or insufficient, return ONLY an empty string.
Example:
AWS Certified Solutions Architect, Amazon Web Services, 2023
Certified Kubernetes Administrator (CKA), Linux Foundation, 2022""",
        "languages": """List each language clearly, one per line, along with proficiency level (e.g., English: Fluent, French: Intermediate). If input is empty or insufficient, return ONLY an empty string.""",
        "awards": "List each award on a new line in a professional resume format (e.g., 'Employee of the Year, ABC Corp, 2023'). If input is empty or insufficient, return ONLY an empty string.",
        "volunteering": "Describe volunteering activities and contributions concisely, using bullet points. If input is empty or insufficient, return ONLY an empty string.",
        "interests": "Convert these interests into professional sounding phrases suitable for a resume. List them concisely. If input is empty or insufficient, return ONLY an empty string.",
        "publications": "Expand these publication titles and give a brief context suitable for a resume, using bullet points if multiple. If input is empty or insufficient, return ONLY an empty string.",
        "patents": "Describe patents briefly and professionally, using bullet points if multiple. If input is empty or insufficient, return ONLY an empty string.",
        "achievements": "List each achievement, award, or notable success concisely, one per line, suitable for a professional resume. If input is empty or insufficient, return ONLY an empty string.",
        "extraCurricular": "List extra-curricular activities and relevant contributions concisely, using bullet points or short phrases. Highlight leadership, teamwork, or organizational skills. If input is empty or insufficient, return ONLY an empty string."
    }

    # NEW TEMPORARY DEBUGGING LINE: Check what data is received for fresher_check, jobTitle, and skills
    print(f"DEBUG_FRESHER_INPUT: fresher_check: {data.get('fresher_check', False)}, jobTitle: '{data.get('jobTitle', '')}', skills: '{data.get('skills', '')}'") #

    # Smart fresher handling logic - Updated to directly set 'experience' and skip AI call
    is_fresher = data.get("fresher_check", False) in [True, "true", "on", "1"] #
    
    if is_fresher: #
        job_title = data.get("jobTitle", "entry-level role") #
        skills_raw = data.get("skills", "") #
        skills = ", ".join([s.strip() for s in skills_raw.split(",") if s.strip()]) or "my field" #
        dynamic_experience_line = f"As a fresher in {job_title}, I am eager to apply my skills in {skills} and grow professionally." #
        
        # Add directly to smart_resume, no need to send for AI processing
        smart_resume["experience"] = dynamic_experience_line #
        print(f"DEBUG_FRESHER_OUTPUT: Dynamic experience line generated: '{dynamic_experience_line}'") #
            
    # <<< REST OF THE FUNCTION REMAINS THE SAME >>>
    for key, instruction in sections.items(): #
        # If we have already handled fresher experience, skip AI call for this key
        if is_fresher and key == "experience": #
            continue #

        value = data.get(key, "").strip() #

        # Skip AI call if input is empty to save resources
        if not value: #
            smart_resume[key] = "" #
            continue #

        prompt = f""" #
You are an expert resume writer. {instruction}
Input: {value}
Output:
"""

        try:
            response = client.chat.completions.create( #
                model="gpt-3.5-turbo", #
                messages=[{"role": "user", "content": prompt}], #
                timeout=20 #
            )
            result = response.choices[0].message.content.strip() #

            if result.lower() in [ #
                "no skills provided.", "no certifications found.", "no education details provided.",
                "no projects found.", "no languages provided.", "invalid input.",
                "i'm sorry, but i cannot generate certifications without any input.",
                "the input provided seems to be incomplete.",
                "kindly provide the education details that need to be reformatted in a standard resume format.",
                "empty"
            ]: #
                smart_resume[key] = "" #
            else: #
                smart_resume[key] = result #

        except Exception as e: #
            smart_resume[key] = value #

    return smart_resume #

def generate_full_ai_resume_html(user_info: dict, smart_content: dict) -> str:
    """
    Ye function AI-generated resume content ko ek proper HTML resume format me convert karta hai.
    Left section: contact, skills, languages, certifications, etc.
    Right section: summary, experience, education, projects, etc.
    """
    import re

    def list_to_html(items_string):
        """
        Always render lines as list items, with or without subheadings.
        """
        if not items_string:
            return ""

        if isinstance(items_string, list):
            lines = [str(item).strip() for item in items_string if str(item).strip()]
        elif isinstance(items_string, str):
            lines = [line.strip() for line in items_string.split('\n') if line.strip()]
        else:
            return ""

        html_parts = []
        current_subheading = None
        list_open = False

        for line in lines:
            if line.lower() in [
                "input is empty or insufficient.", "no skills provided.", "no certifications found.",
                "no education details provided.", "no projects found.", "no languages provided.",
                "not provided.", "empty"
            ] or "i'm sorry, but i cannot" in line.lower() or "kindly provide the" in line.lower():
                continue

            if line.strip().endswith(':'):
                if list_open:
                    html_parts.append("</ul>")
                    list_open = False

                current_subheading = line.strip()
                html_parts.append(f"<h3 contenteditable=\"true\">{current_subheading}</h3><ul>")
                list_open = True
            else:
                if not list_open:
                    html_parts.append("<ul>")
                    list_open = True

                clean_line = line.strip().lstrip('-• ').strip()
                html_parts.append(f"<li contenteditable=\"true\">{clean_line}</li>")

        if list_open:
            html_parts.append("</ul>")

        return "".join(html_parts)

    def parse_complex_section_html(section_data, is_education=False):
        if not section_data:
            return ""

        html_output = ""

        if isinstance(section_data, str):
            lower_section_data = section_data.lower().strip()
            if lower_section_data in [
                "sorry, but the input provided is insufficient.",
                "input is empty or insufficient.",
                "not provided.",
                "empty"
            ]:
                return ""

            lines = [line.strip() for line in section_data.split('\n') if line.strip()]
            current_item = {"title": "", "details": []}
            all_items = []
            
            for line in lines:
                if not line.strip().lstrip('-• ').strip():
                    continue

                if line.strip().startswith(('-', '•')):
                    current_item["details"].append(line.strip().lstrip('-• ').strip())
                elif not current_item["title"] and not current_item["details"]:
                    current_item["title"] = line.strip()
                elif current_item["title"] and not current_item["details"]:
                    current_item["details"].append(line.strip())
                else:
                    all_items.append(current_item)
                    current_item = {"title": line.strip(), "details": []}
            if current_item["title"] or current_item["details"]:
                all_items.append(current_item)

            section_data_processed = all_items

            if not section_data_processed:
                if section_data.strip():
                    return f"<div class='experience-item'><p contenteditable=\"true\">{section_data.strip()}</p></div>"
                else:
                    return ""

        elif isinstance(section_data, list):
            section_data_processed = section_data
        else:
            return ""

        for item in section_data_processed:
            title_text = item.get("title", '')
            company_text = item.get("company", '')
            duration_text = item.get("duration", '')
            details_list = item.get("details", [])
            description_text = item.get("description", '')

            item_html = ""

            item_html += f"<div class='item-header'>"
            item_html += f"<h4 contenteditable=\"true\">{title_text}</h4>"
            
            item_html += f"<p class='item-meta'>"
            if company_text:
                item_html += f"<span contenteditable=\"true\">{company_text}</span>"
            if duration_text:
                item_html += f"<span class='duration' contenteditable=\"true\">{duration_text}</span>"
            item_html += "</p>"
            item_html += "</div>"

            if description_text:
                # FIXED TYPO: changed 'classt' to 'class'
                item_html += f"<p class='item-description' contenteditable=\"true\">{description_text}</p>"

            if details_list:
                if isinstance(details_list, str):
                    details_list_final = [d.strip().lstrip('-• ').strip() for d in details_list.split('\n') if d.strip()]
                elif isinstance(details_list, list):
                    details_list_final = [d.strip().lstrip('-• ').strip() for d in details_list if d.strip()]
                else:
                    details_list_final = []

                if details_list_final:
                    item_html += "<ul>"
                    for detail in details_list_final:
                        item_html += f"<li contenteditable=\"true\">{detail}</li>"
                    item_html += "</ul>"
            
            html_output += f"<div class='experience-item'>{item_html}</div>"
        
        return html_output

    # Extract personal details more reliably
    name = user_info.get("name", "Your Name")
    phone = user_info.get("phone", "")
    email = user_info.get("email", "")
    location = user_info.get("location", "")
    linkedin = user_info.get("linkedin", "")
    jobTitle = user_info.get("jobTitle", "")

    # Pre-render conditional HTML blocks to simplify the main f-string
    # Skills Section
    skills_content = list_to_html(smart_content.get('skills', ''))
    skills_section_html = ""
    if skills_content.strip():
        skills_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Skills</h2>
                    {skills_content}
                </div>
        """

    # Languages Section
    languages_content = list_to_html(smart_content.get('languages', ''))
    languages_section_html = ""
    if languages_content.strip():
        languages_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Languages</h2>
                    {languages_content}
                </div>
        """

    # Certifications Section
    certifications_content = list_to_html(smart_content.get('certifications', ''))
    certifications_section_html = ""
    if certifications_content.strip():
        certifications_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Certifications</h2>
                    {certifications_content}
                </div>
        """

    # Profile Summary Section
    summary_text = smart_content.get('summary', '')
    summary_section_html = ""
    if summary_text.strip():
        summary_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Profile Summary</h2>
                    <p contenteditable="true">{summary_text}</p>
                </div>
        """


    # NEW TEMPORARY DEBUGGING LINE: Check content for experience section before rendering
    print(f"DEBUG_HTML_GEN: Experience content received by HTML generator: '{smart_content.get('experience', '')}'")
    # Work Experience Section
    experience_content = parse_complex_section_html(smart_content.get('experience', ''), is_education=False)
    experience_section_html = ""
    if experience_content.strip():
        experience_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Work Experience</h2>
                    {experience_content}
                </div>
        """

    # Education Section
    education_content = parse_complex_section_html(smart_content.get('education', ''), is_education=True)
    education_section_html = ""
    if education_content.strip():
        education_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Education</h2>
                    {education_content}
                </div>
        """

    # Projects Section
    projects_content = parse_complex_section_html(smart_content.get('projects', ''), is_education=False)
    projects_section_html = ""
    if projects_content.strip():
        projects_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Projects</h2>
                    {projects_content}
                </div>
        """
    # Achievements Section
    achievements_content = list_to_html(smart_content.get('achievements', ''))
    achievements_section_html = ""
    if achievements_content.strip():
        achievements_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Awards & Achievements</h2>
                    {achievements_content}
                </div>
        """

    # Extra-curricular Activities Section
    extra_curricular_content = list_to_html(smart_content.get('extraCurricular', ''))
    extra_curricular_section_html = ""
    if extra_curricular_content.strip():
        extra_curricular_section_html = f"""
                <div class="resume-section preview-section">
                    <h2 contenteditable="true">Extra-curricular Activities</h2>
                    {extra_curricular_content}
                </div>
        """

    return f"""
    <div class="resume-container">
        <div class="content-wrapper">
            <aside class="resume-sidebar">
                <div class="contact-info-sidebar preview-section">
                    <h3 contenteditable="true">Contact</h3>
                    {phone.strip() and f"<p contenteditable='true'><i class='fas fa-phone-alt'></i> {phone}</p>" or ""}
                    {email.strip() and f"<p contenteditable='true'><i class='fas fa-envelope'></i> {email}</p>" or ""}
                    {location.strip() and f"<p contenteditable='true'><i class='fas fa-map-marker-alt'></i> {location}</p>" or ""}
                    {linkedin.strip() and f"<p contenteditable='true'><i class='fab fa-linkedin'></i> <a href='{linkedin}' target='_blank'>{linkedin}</a></p>" or ""}
                </div>
                {skills_section_html}
                {languages_section_html}
                {certifications_section_html}
            </aside>
            <main class="main-content">
                <div class="name-title-header">
                    <h1 contenteditable="true" id="preview-name">{name}</h1>
                    {jobTitle.strip() and f"<p class='job-title' contenteditable='true' id='preview-title'>{jobTitle}</p>" or ""}
                </div>
                {summary_section_html}
                {experience_section_html}
                {education_section_html}
                {projects_section_html}
                {achievements_section_html}
                {extra_curricular_section_html}
            </main>
        </div>
    </div>
    """
