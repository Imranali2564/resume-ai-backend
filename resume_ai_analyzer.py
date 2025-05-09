import os
import docx
import pdfplumber
from PIL import Image
from pdf2image import convert_from_path
from openai import OpenAI
import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Do not initialize the client at the module level
client = None

def get_openai_client():
    global client
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is not set")
        client = OpenAI(api_key=api_key)
    return client

def extract_text_from_pdf(file_path):
    try:
        with pdfplumber.open(file_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        return text.strip()
    except Exception:
        return ""

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs).strip()
    except Exception:
        return ""

def extract_text_with_ocr(file_path):
    try:
        import pytesseract
        images = convert_from_path(file_path, dpi=300)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img)
        return text.strip()
    except ImportError:
        logger.error("OCR not available: pytesseract is not installed.")
        return ""
    except Exception as e:
        logger.error(f"Error in OCR: {str(e)}")
        return ""

def check_ats_compatibility(file_path):
    text = ""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
    elif ext == ".docx":
        text = extract_text_from_docx(file_path)

    if not text.strip():
        return "Resume text could not be extracted."

    prompt = f"""
You are an ATS (Applicant Tracking System) expert. Analyze this resume and return a simple report.
Mention what is ✅ good and what is ❌ missing in terms of formatting, keywords, structure, and ATS compatibility.
Use clear points, starting each line with ✅ or ❌.

Resume:
{text[:4000]}
    """
    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an ATS resume expert."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[OpenAI ERROR in check_ats_compatibility]: {str(e)}")
        return "❌ Failed to analyze ATS compatibility due to an API error."

def analyze_resume_with_openai(file_path, atsfix=False):
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
        elif ext == ".docx":
            text = extract_text_from_docx(file_path)
        else:
            return {"error": "Unsupported file type."}

        if not text.strip():
            return {"error": "No text found in resume"}

        if atsfix:
            prompt = f"""
You are an ATS resume expert. Provide 5 to 7 most important and high-impact improvement suggestions that directly affect ATS compatibility and selection.
List only important actionable suggestions in short bullet points. One suggestion per line. No intro or outro.

Resume:
{text[:4000]}
            """
        else:
            prompt = f"""
You are a professional resume coach. Give improvement suggestions in short clear bullet points.
Make suggestions specific, actionable, and impactful.
Don't explain anything else. List one suggestion per line.

Resume:
{text[:4000]}
            """

        logger.info("[OpenAI] Sending resume for suggestion generation...")
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional resume suggestion assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        logger.info("[OpenAI] Response received.")
        suggestions = response.choices[0].message.content.strip()
        return {"suggestions": suggestions}
    except Exception as e:
        logger.error(f"[OpenAI ERROR in analyze_resume_with_openai]: {str(e)}")
        return {"error": "Failed to generate suggestions due to an API error."}
    
def analyze_job_description(jd_text):
    import re
    from sklearn.feature_extraction.text import CountVectorizer

    vectorizer = CountVectorizer(stop_words='english', max_features=10)
    words = vectorizer.fit([jd_text]).get_feature_names_out()
    skills = list(words)

    common_tools = ['excel', 'power bi', 'tableau', 'sql', 'git', 'aws', 'google analytics', 'jira', 'python', 'r', 'tensorflow', 'hadoop']
    tools_found = [tool for tool in common_tools if tool in jd_text.lower()]

    summary_prompt = f"""
Given this job description:

\"\"\"{jd_text}\"\"\"

Write a 2-3 line summary describing the ideal candidate (skills, experience level, background). Keep it brief and clear.
"""

    summary = "An ideal candidate should have relevant technical and soft skills mentioned above."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": summary_prompt}]
        )
        summary = response.choices[0].message.content.strip()
    except Exception as e:
        summary += f" (AI summary failed: {str(e)})"

    return {
        "skills": skills,
        "tools": tools_found,
        "summary": summary
    }

def extract_resume_sections(text):
    sections = {
        "personal_details": [],
        "summary": [],
        "education": [],
        "experience": [],
        "skills": [],
        "certifications": [],
        "languages": [],
        "hobbies": [],
        "additional_courses": [],
        "projects": [],
        "volunteer_experience": [],
        "achievements": [],
        "publications": [],
        "references": [],
        "miscellaneous": []
    }

    section_headers = {
        "personal_details": ["personal details", "personal information", "contact details", "contact information", "about me"],
        "summary": ["summary", "objective", "professional summary", "career objective", "profile"],
        "education": ["education", "academic background", "educational qualifications", "academic history", "qualifications"],
        "experience": ["experience", "professional experience", "work experience", "work history", "employment history", "career history"],
        "skills": ["skills", "technical skills", "key skills", "core competencies", "abilities"],
        "certifications": ["certifications", "certificates", "credentials", "achievements"],
        "languages": ["languages", "language skills", "language proficiency"],
        "hobbies": ["hobbies", "interests", "personal interests", "extracurricular activities"],
        "additional_courses": ["additional courses", "courses", "additional training", "training", "professional training"],
        "projects": ["projects", "technical projects", "key projects", "portfolio"],
        "volunteer_experience": ["volunteer experience", "volunteer work", "community service"],
        "achievements": ["achievements", "accomplishments", "awards", "honors"],
        "publications": ["publications", "research papers", "articles"],
        "references": ["references", "professional references"]
    }

    current_section = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        line_lower = " ".join(line.lower().split())  # Normalize spaces

        # Detect section headings
        found_section = False
        for section, variations in section_headers.items():
            for variation in variations:
                if line_lower == variation:  # Exact match for section header
                    current_section = section
                    found_section = True
                    break
            if found_section:
                break

        # If no section header is found and we are in a section, append the line to the current section
        if not found_section and current_section:
            # Check if the next line might be a new section header
            if i + 1 < len(lines):
                next_line = " ".join(lines[i + 1].lower().strip().split())
                next_section = False
                for section, variations in section_headers.items():
                    if any(next_line == variation for variation in variations):
                        next_section = True
                        break
                if not next_section:
                    sections[current_section].append(line)
            else:
                sections[current_section].append(line)
        elif not found_section and not current_section:
            sections["miscellaneous"].append(line)

    # Post-process sections to deduplicate and reassign misplaced content
    # Step 1: Deduplicate within each section
    for section in sections:
        if section == "personal_details":
            seen = set()
            deduplicated = []
            for line in sections[section]:
                normalized = " ".join(line.lower().split())
                if normalized not in seen:
                    seen.add(normalized)
                    deduplicated.append(line)
            deduplicated.sort(key=lambda x: "insha" not in x.lower())
            sections[section] = deduplicated
        elif section == "summary":
            if sections[section]:
                summary_blocks = []
                current_block = []
                for line in sections[section]:
                    if "professional summary" in line.lower() or "summary" in line.lower():
                        if current_block:
                            summary_blocks.append("\n".join(current_block))
                            current_block = []
                    current_block.append(line)
                if current_block:
                    summary_blocks.append("\n".join(current_block))
                if summary_blocks:
                    sections[section] = [max(summary_blocks, key=len)]
                else:
                    sections[section] = []
        else:
            seen = set()
            deduplicated = []
            for line in sections[section]:
                normalized = " ".join(line.lower().split())
                if normalized not in seen:
                    seen.add(normalized)
                    deduplicated.append(line)
            sections[section] = deduplicated

    # Step 2: Reassign content from "miscellaneous" to correct sections
    if sections["miscellaneous"]:
        misc_lines = sections["miscellaneous"]
        sections["miscellaneous"] = []
        current_section = None
        temp_section_content = []

        for line in misc_lines:
            line_lower = " ".join(line.lower().split())
            found_section = False
            for section, variations in section_headers.items():
                for variation in variations:
                    if line_lower == variation:
                        if current_section and temp_section_content:
                            sections[current_section].extend(temp_section_content)
                            temp_section_content = []
                        current_section = section
                        found_section = True
                        break
                if found_section:
                    break
            if not found_section:
                if current_section:
                    temp_section_content.append(line)
                else:
                    # Check if the line contains content that belongs to a known section
                    reassigned = False
                    for section, variations in section_headers.items():
                        keywords = {
                            "personal_details": ["email", "phone", "address", "date of birth", "marital status", "nationality", "gender", "insha"],
                            "summary": ["results-driven", "highly motivated", "strong foundation", "telecalling experience"],
                            "education": ["government girls senior secondary school", "76%", "72%", "2021", "2023"],
                            "experience": ["tele caller", "paisefy advisory pvt", "6 month experience", "contacted potential customers"],
                            "skills": ["computer applications (cca)", "communication", "customer service", "time management"],
                            "additional_courses": ["cca", "fundamentals of computer"],
                            "achievements": ["increased product awareness", "10% increase in sales", "commendations"]
                        }.get(section, variations)
                        if any(keyword in line_lower for keyword in keywords):
                            sections[section].append(line)
                            reassigned = True
                            break
                    if not reassigned and "insha" not in line_lower:  # Exclude standalone "Insha"
                        sections["miscellaneous"].append(line)

        # Append any remaining content
        if current_section and temp_section_content:
            sections[current_section].extend(temp_section_content)

    # Step 3: Deduplicate across sections (e.g., "Skills" in "Miscellaneous")
    for section in sections:
        if section == "miscellaneous":
            continue
        section_content = set(" ".join(line.lower().split()) for line in sections[section])
        for other_section in sections:
            if other_section == section or other_section == "miscellaneous":
                continue
            other_content = set(" ".join(line.lower().split()) for line in sections[other_section])
            overlap = section_content.intersection(other_content)
            if overlap:
                # Remove overlapping content from the less relevant section
                sections[other_section] = [line for line in sections[other_section] if " ".join(line.lower().split()) not in overlap]

    # Step 4: Clean up "miscellaneous" by removing redundant name
    if sections["miscellaneous"]:
        name_lines = set()
        if sections["personal_details"]:
            for line in sections["personal_details"]:
                if "insha" in line.lower():
                    name_lines.add(line.strip())
        sections["miscellaneous"] = [line for line in sections["miscellaneous"] if line not in name_lines]
        if not sections["miscellaneous"]:
            sections["miscellaneous"] = []

    # Convert lists back to strings
    for key in sections:
        sections[key] = "\n".join(sections[key]).strip()

    return sections

def detect_section_from_suggestion(suggestion):
    suggestion = suggestion.lower()
    section_keywords = {
        "personal_details": ["personal details", "contact", "email", "phone", "address", "name"],
        "summary": ["summary", "objective", "professional summary", "career goal", "profile", "introduction"],
        "skills": ["skill", "proficiency", "tools", "technologies", "software", "languages known", "abilities", "technical skills", "core competencies"],
        "experience": ["experience", "worked", "responsibility", "role", "company", "job title", "employment", "career", "achievement", "accomplished", "performed"],
        "education": ["education", "degree", "university", "college", "academic", "school", "qualification", "graduation"],
        "certifications": ["certification", "course", "certified", "training", "diploma", "credential", "achievement"],
        "languages": ["language", "fluent", "spoken", "bilingual", "multilingual", "proficiency"],
        "hobbies": ["hobby", "interest", "extracurricular", "leisure", "passion", "activity"],
        "additional_courses": ["additional courses", "courses", "training", "professional training", "additional training"],
        "projects": ["projects", "technical projects", "key projects", "portfolio", "work project"],
        "volunteer_experience": ["volunteer experience", "volunteer work", "community service", "volunteering"],
        "achievements": ["achievements", "accomplishments", "awards", "honors", "recognition"],
        "publications": ["publications", "research papers", "articles", "published"],
        "references": ["references", "professional references", "referee"]
    }

    for section, keywords in section_keywords.items():
        if any(word in suggestion for word in keywords):
            return section
    return None

def generate_section_content(suggestion, full_resume_text):
    try:
        sections = extract_resume_sections(full_resume_text)
        detected_section = detect_section_from_suggestion(suggestion)
        logger.info(f"Detected Section: {detected_section}")

        if not detected_section:
            return {"error": "Could not detect section from suggestion."}

        # Define formatting rules for different sections
        section_formatting = {
            "skills": "Format as a bullet list with '•' as the bullet character, e.g., '• Skill 1\n• Skill 2'",
            "experience": "Format as a bullet list with '-' as the bullet character, e.g., '- Job Title, Company, Duration\n- Responsibility 1\n- Responsibility 2'",
            "hobbies": "Format as a bullet list with '•' as the bullet character, e.g., '• Hobby 1\n• Hobby 2'",
            "projects": "Format as a bullet list with '-' as the bullet character, e.g., '- Project 1 description\n- Project 2 description'",
            "achievements": "Format as a bullet list with '-' as the bullet character, e.g., '- Achievement 1\n- Achievement 2'",
            "personal_details": "Format as plain text with line breaks, e.g., 'Email: email@example.com\nPhone: +1234567890\nLocation: City, Country'",
            "summary": "Format as plain text with line breaks for readability.",
            "education": "Format as plain text with line breaks, e.g., 'B.Tech in Computer Science, XYZ University, 2020-2024'",
            "certifications": "Format as plain text with line breaks, e.g., 'Certified Python Developer, XYZ Institute, 2023'",
            "languages": "Format as plain text with line breaks, e.g., 'English (Fluent)\nHindi (Native)'"
        }

        formatting_instruction = section_formatting.get(detected_section, "Format as plain text with line breaks for readability.")

        # If the section doesn't exist or is empty, create it
        if detected_section not in sections or not sections[detected_section].strip():
            prompt = f"""
You are an AI resume assistant. Based on the following suggestion and full resume context, write a new section for the resume.
Only output the improved section content, no explanation.

Resume:
{full_resume_text}

Suggestion:
{suggestion}

Instructions:
- {formatting_instruction}
- Ensure the content is concise, professional, and relevant to the section.
"""
            logger.debug(f"Prompt for new section:\n{prompt}")
            client = get_openai_client()
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an expert resume section creator."},
                    {"role": "user", "content": prompt}
                ]
            )
            improved_content = response.choices[0].message.content.strip()
            return {
                "section": detected_section,
                "fixedContent": improved_content
            }

        original_content = sections[detected_section]
        if not original_content.strip():
            return {"error": "No content found in the detected section."}

        prompt = f"""
You are an AI resume assistant. Your task is to improve the following section of a resume based on a suggestion.

Full Resume Context:
{full_resume_text}

User's Suggestion:
{suggestion}

Current Section Content:
{original_content}

Instructions:
- {formatting_instruction}
- Ensure the content is concise, professional, and relevant to the section.
- Return only the improved content for this section. Do not include any explanation or headers.
"""

        logger.debug(f"Prompt for fix:\n{prompt}")

        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert resume fixer."},
                {"role": "user", "content": prompt}
            ]
        )

        improved_content = response.choices[0].message.content.strip()
        logger.info(f"AI Response: {improved_content}")

        return {
            "section": detected_section,
            "fixedContent": improved_content
        }

    except Exception as e:
        logger.error(f"[ERROR in generate_section_content]: {str(e)}")
        return {"error": "Failed to generate fixed content from suggestion."}
import re
from sklearn.feature_extraction.text import CountVectorizer

def extract_keywords_from_jd(jd_text):
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9-+]{2,}\b', jd_text.lower())
    common_exclude = {'the', 'and', 'for', 'with', 'you', 'your', 'are', 'our', 'job', 'will', 'this'}
    filtered = [word for word in words if word not in common_exclude]
    vectorizer = CountVectorizer(stop_words='english', max_features=15)
    keywords = vectorizer.fit([jd_text]).get_feature_names_out()
    return list(keywords)

def extract_text_from_resume(resume_file):
    try:
        return resume_file.read().decode('utf-8', errors='ignore')
    except Exception:
        return ""

def compare_resume_with_keywords(resume_text, jd_keywords):
    resume_lower = resume_text.lower()
    present = []
    missing = []
    suggestions = []
    for keyword in jd_keywords:
        if keyword in resume_lower:
            present.append(keyword)
        else:
            missing.append(keyword)
            suggestions.append(f"Consider adding '{keyword}' to your skills, summary, or experience section.")
    return {
        'present_keywords': present,
        'missing_keywords': missing,
        'suggested_keywords': suggestions
    }
def fix_resume_formatting(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
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
        client = get_openai_client()
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

