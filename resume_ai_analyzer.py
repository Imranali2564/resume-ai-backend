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

        prompt = (
            "You are a professional resume analyzer.\n"
            "Analyze the following resume and provide key suggestions to improve its impact, clarity, and formatting.\n"
            "Give up to 7 specific, actionable suggestions only. Avoid generic advice.\n\n"
            f"Resume:\n{resume_text[:6000]}"
        )

        if atsfix:
            prompt = (
                "You are an expert in optimizing resumes for Applicant Tracking Systems (ATS).\n"
                "Analyze the following resume and provide specific suggestions to improve its ATS compatibility.\n"
                "Give up to 7 specific, actionable suggestions only. Focus on ATS-specific improvements like keywords, section headings, and formatting.\n\n"
                f"Resume:\n{resume_text[:6000]}"
            )

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

    # Define the example output format separately to avoid backslashes in f-string
    example_output = (
        "NAME\n"
        "John Doe\n\n"
        "PERSONAL DETAILS\n"
        "- Email: john.doe@email.com\n"
        "- Phone: +1234567890\n\n"
        "SUMMARY\n"
        "- A dedicated Software Engineer with 3 years of experience.\n\n"
        "EXPERIENCE\n"
        "- Software Engineer Intern, ABC Corp, June 2023 - August 2023\n"
        "- Developed web applications using React and Node.js\n\n"
        "SKILLS\n"
        "- Python\n"
        "- JavaScript\n"
        "- SQL"
    )

    prompt = (
        "You are a professional resume formatting expert.\n"
        "Clean and reformat the following resume into plain text with the following rules:\n"
        "- Organize the resume into clear sections (e.g., Education, Experience, Skills, etc.).\n"
        "- Use section headings in all caps (e.g., EDUCATION, EXPERIENCE, SKILLS).\n"
        "- Use a single dash and space ('- ') for all bullet points.\n"
        "- Ensure exactly one blank line between sections.\n"
        "- Remove extra spaces, normalize line breaks, and ensure consistent formatting.\n"
        "- Do not use HTML, markdown, or any other markup language—just plain text.\n"
        "- Order sections as follows: PERSONAL DETAILS, SUMMARY, OBJECTIVE, SKILLS, EXPERIENCE, EDUCATION, CERTIFICATIONS, LANGUAGES, HOBBIES, PROJECTS, VOLUNTEER EXPERIENCE, ACHIEVEMENTS, PUBLICATIONS, REFERENCES.\n\n"
        f"Example output:\n{example_output}\n\n"
        f"Resume:\n{text}"
    )

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

def generate_section_content(suggestion, full_text):
    if not client:
        return {"error": "Cannot generate section content: OpenAI API key not set."}

    try:
        sections = extract_resume_sections(full_text)
        existing_sections = list(sections.keys())

        # Check for personal details to avoid incorrect suggestions
        personal_details = sections.get("personal_details", "").lower()
        has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+', personal_details))
        has_phone = bool(re.search(r'\+?\d[\d\s\-]{8,}', personal_details))
        has_location = "location" in personal_details or "city" in personal_details or "📍" in personal_details

        # Define the example JSON string separately to avoid backslash in f-string
        example_json = '{"section": "skills", "fixedContent": "- Python\n- Communication\n- Teamwork"}'

        prompt = (
            "You are an AI resume improvement expert.\n\n"
            "Given the suggestion and full resume text, return a JSON with:\n"
            "- 'section': which section to update (e.g. skills, summary, education)\n"
            "- 'fixedContent': fixed or new content based on the suggestion\n"
            "- Add grammar/spelling correction where needed\n"
            "- If the section is missing, generate it with relevant content\n"
            "- Use bullet points where applicable\n"
            "- Respond in this format:\n"
            f"{example_json}\n\n"
            "Additional Instructions:\n"
            "- If the suggestion is about adding an email, phone, or location, and these already exist in the personal_details section, do not suggest adding them again. Instead, confirm they are present.\n"
            "- For the 'technical_skills' section:\n"
            "  - If there are more than 10 skills, reduce to the top 8 most relevant skills.\n"
            "  - If there are fewer than 5 skills, expand by adding 3-5 relevant skills based on the resume context.\n"
            "- Do not suggest adding personal details if they already exist:\n"
            f"  - Email present: {has_email}\n"
            f"  - Phone present: {has_phone}\n"
            f"  - Location present: {has_location}\n\n"
            f"Suggestion:\n{suggestion}\n\n"
            f"Resume:\n{full_text[:6000]}"
        )

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
    lines = text.splitlines()
    sections = {
        "personal_details": "",
        "summary": "",
        "education": "",
        "skills": "",
        "work_experience": "",
        "projects": "",
        "certifications": "",
        "languages": "",
        "achievements": "",
        "hobbies": ""
    }

    current_section = None
    buffer = []

    # Define possible section headings
    section_keywords = {
        "summary": ["summary", "objective", "career summary", "profile"],
        "education": ["education", "academics", "qualifications"],
        "skills": ["skills", "technical skills", "tools", "technologies"],
        "work_experience": ["experience", "employment", "professional experience", "work"],
        "projects": ["projects", "project work", "academic projects"],
        "certifications": ["certifications", "certificates", "courses"],
        "languages": ["languages", "language proficiency"],
        "achievements": ["achievements", "accomplishments", "awards"],
        "hobbies": ["hobbies", "interests", "extracurricular"]
    }

    def save_buffer_to_section(section):
        if section and buffer:
            content = "\n".join(buffer).strip()
            if content:
                sections[section] += content + "\n"
            buffer.clear()

    for line in lines:
        line_clean = line.strip().lower()

        matched_section = None
        for key, keywords in section_keywords.items():
            for keyword in keywords:
                if line_clean.startswith(keyword):
                    matched_section = key
                    break
            if matched_section:
                break

        if matched_section:
            save_buffer_to_section(current_section)
            current_section = matched_section
        else:
            buffer.append(line)

    save_buffer_to_section(current_section)

    # ✨ Auto-detect personal details
    personal_lines = []
    for line in lines:
        if '@' in line and 'email' in line.lower():
            personal_lines.append(line.strip())
        elif re.search(r'\+?\d[\d\s\-]{6,}', line):
            personal_lines.append(line.strip())
        elif re.search(r'\b(location|address|city|state|country)\b', line.lower()):
            personal_lines.append(line.strip())
        elif re.search(r'(linkedin|github|portfolio|www\.|http)', line.lower()):
            personal_lines.append(line.strip())
        elif len(line.strip()) <= 40 and not any(x in line.lower() for x in ['objective', 'summary', 'experience']):
            personal_lines.insert(0, line.strip())

    sections["personal_details"] = "\n".join(personal_lines[:5])

    return sections

def extract_keywords_from_jd(jd_text):
    if not client:
        return "Cannot extract keywords: OpenAI API key not set."

    try:
        prompt = (
            "From the following job description, extract the most important keywords that should be reflected in a resume.\n"
            "Return the keywords as a comma-separated string.\n\n"
            f"Job Description:\n{jd_text[:3000]}"
        )
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
        prompt = (
            "You are a professional resume expert.\n\n"
            f"Write a concise 2–3 line professional summary for the following person:\n"
            f"- Name: {name}\n"
            f"- Role: {role}\n"
            f"- Experience: {experience}\n"
            f"- Skills: {skills}\n\n"
            "Make it ATS-friendly, use action words, and highlight strengths. Do not include heading or labels."
        )

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
        prompt = (
            "You are an expert resume reviewer.\n\n"
            "Analyze the following job description and extract the most relevant:\n"
            "1. Key Skills\n"
            "2. Required Qualifications\n"
            "3. Recommended Action Verbs\n\n"
            "Format the result clearly in 3 sections with headings.\n\n"
            f"Job Description:\n{jd_text}"
        )

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ERROR in analyze_job_description]: {str(e)}")
        return "Failed to analyze job description."

def generate_michelle_template_html(sections):
    # Utility function to format section content
    def format_content(text, section_type="other"):
        if not text:
            return ""
        lines = text.strip().split("\n")
        if section_type == "education" and len(lines) == 1:
            # For single-line education entries, use a paragraph
            return f"<p style='font-size: 10pt;'>{lines[0].strip()}</p>"
        # For other cases, use a list
        return "".join(
            f"<p style='font-size: 10pt; margin: 0;'>{line.strip().lstrip('-• ').strip()}</p>"
            for line in lines
            if line.strip()
        )

    # Extract personal details for sidebar
    name = sections.get("name", "Your Name")
    email = sections.get("email", "your.email@example.com")
    phone = sections.get("phone", "Phone Number")
    location = sections.get("location", "City, Country")

    # If personal_details exists, parse it for email, phone, location
    personal_details = sections.get("personal_details", "")
    if personal_details:
        for line in personal_details.split("\n"):
            line = line.strip()
            if not line:
                continue
            if '@' in line or 'email' in line.lower():
                email = line
            elif re.search(r'\+?\d[\d\s\-]{8,}', line) or 'phone' in line.lower():
                phone = line
            elif re.search(r'\b(location|city|state|country)\b', line.lower()):
                location = line
            elif len(line) < 50 and '@' not in line and not re.search(r'\d{5,}', line):
                name = line

    # Define sections with proper conditional logic
    education_section = (
        '<h3 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Education</h3>'
        f"{format_content(sections.get('education', ''), 'education')}"
    ) if sections.get('education') else ''

    hobbies_section = (
        '<h3 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Hobbies</h3>'
        f"{format_content(sections.get('hobbies', ''))}"
    ) if sections.get('hobbies') else ''

    summary_section = ''
    if sections.get('summary'):
        summary_text = sections['summary'].replace('\n', '<br>')
        summary_section = (
            '<h2 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Professional Summary</h2>'
            f"<p style='font-size: 11pt; margin-bottom: 15pt;'>{summary_text}</p>"
        )

    work_experience_section = (
        '<h2 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Work Experience</h2>'
        f"{format_content(sections.get('work_experience', ''))}"
    ) if sections.get('work_experience') else ''

    projects_section = (
        '<h2 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Projects</h2>'
        f"{format_content(sections.get('projects', ''))}"
    ) if sections.get('projects') else ''

    skills_section = (
        '<h2 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Skills</h2>'
        f"{format_content(sections.get('skills', ''))}"
    ) if sections.get('skills') else ''

    certifications_section = (
        '<h2 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-top: 15pt; margin-bottom: 10pt;">Certifications</h2>'
        f"{format_content(sections.get('certifications', ''))}"
    ) if sections.get('certifications') else ''

    html_template = (
        '<div style="font-family: Arial, sans-serif; line-height: 1.5; width: 100%; max-width: 595px; margin: 0 auto;">'
        '    <!-- Container for two-column layout -->'
        '    <div style="display: flex; flex-direction: row;">'
        '        <!-- Sidebar (30% width) -->'
        '        <div style="width: 30%; background-color: #f0f4f8; padding: 15pt; color: #333;">'
        '            <!-- Contact Details -->'
        '            <h3 style="font-size: 14pt; color: #1e40af; border-bottom: 1pt solid #1e40af; margin-bottom: 10pt;">Contact Details</h3>'
        f'            <p style="font-size: 10pt; margin-bottom: 5pt;"><strong>Email:</strong> {email}<br>'
        f'            <strong>Phone:</strong> {phone}<br>'
        f'            <strong>Location:</strong> {location}</p>'
        f'            {education_section}'
        f'            {hobbies_section}'
        '        </div>'
        '        <!-- Main Content (70% width) -->'
        '        <div style="width: 70%; padding: 15pt;">'
        f'            <h1 style="font-size: 26pt; font-weight: bold; color: #1e40af; margin-bottom: 5pt; text-align: center;">{name}</h1>'
        f'            {summary_section}'
        f'            {work_experience_section}'
        f'            {projects_section}'
        f'            {skills_section}'
        f'            {certifications_section}'
        '        </div>'
        '    </div>'
        '</div>'
    )

    return html_template

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
        prompt = (
            "You are an ATS expert. Check the following resume and give up to 5 issues:\n"
            f"Resume:\n{text[:6000]}\n"
            'Return in this format:\n["✅ Passed: ...", "❌ Issue: ..."]'
        )

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
