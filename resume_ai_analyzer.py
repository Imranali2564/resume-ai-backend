import os
import docx
import pdfplumber
from PIL import Image
from pdf2image import convert_from_path
from openai import OpenAI

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
        print("OCR not available: pytesseract is not installed.")
        return ""
    except Exception as e:
        print(f"Error in OCR: {str(e)}")
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
        print(f"❌ [OpenAI ERROR in check_ats_compatibility]: {str(e)}")
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

        print("✅ [OpenAI] Sending resume for suggestion generation...")
        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional resume suggestion assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        print("✅ [OpenAI] Response received.")
        suggestions = response.choices[0].message.content.strip()
        return {"suggestions": suggestions}
    except Exception as e:
        print(f"❌ [OpenAI ERROR in analyze_resume_with_openai]: {str(e)}")
        return {"error": "Failed to generate suggestions due to an API error."}

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

    # Post-process sections to remove duplicates and merge content
    for section in sections:
        if section == "personal_details":
            # Normalize and deduplicate personal details
            seen = set()
            deduplicated = []
            for line in sections[section]:
                # Normalize the line for comparison (e.g., "Email : inshaansari844@gmail.com | Phone : 9654031233 | Insha")
                normalized = " ".join(line.lower().split())
                if normalized not in seen:
                    seen.add(normalized)
                    deduplicated.append(line)
            # Sort to ensure consistent order (e.g., name at the end)
            deduplicated.sort(key=lambda x: "insha" not in x.lower())  # Put "Insha" at the end
            sections[section] = deduplicated
        elif section == "summary":
            # Merge summaries, keeping the most complete content
            if sections[section]:
                # Join all lines into a single block for each summary instance
                summary_blocks = []
                current_block = []
                for line in sections[section]:
                    if "professional summary" in line.lower():
                        if current_block:
                            summary_blocks.append("\n".join(current_block))
                            current_block = []
                    current_block.append(line)
                if current_block:
                    summary_blocks.append("\n".join(current_block))
                # Keep the longest (most complete) summary block
                if summary_blocks:
                    sections[section] = [max(summary_blocks, key=len)]
                else:
                    sections[section] = []
        else:
            # For other sections, just deduplicate lines
            seen = set()
            deduplicated = []
            for line in sections[section]:
                normalized = " ".join(line.lower().split())
                if normalized not in seen:
                    seen.add(normalized)
                    deduplicated.append(line)
            sections[section] = deduplicated

    # Clean up miscellaneous section
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
        print(f"✅ Detected Section: {detected_section}")

        if not detected_section:
            return {"error": "Could not detect section from suggestion."}

        # If the section doesn't exist, create it
        if detected_section not in sections or not sections[detected_section].strip():
            prompt = f"""
You are an AI resume assistant. Based on the following suggestion and full resume context, write a new section for the resume.
Only output the improved section content, no explanation.

Resume:
{full_resume_text}

Suggestion:
{suggestion}
"""
            print("🧠 Prompt for new section:\n", prompt)
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

Please return only the improved content for this section. Do not include any explanation or headers.
"""

        print("🧠 Prompt for fix:\n", prompt)

        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an expert resume fixer."},
                {"role": "user", "content": prompt}
            ]
        )

        improved_content = response.choices[0].message.content.strip()
        print("✅ AI Response:", improved_content)

        return {
            "section": detected_section,
            "fixedContent": improved_content
        }

    except Exception as e:
        print(f"❌ [ERROR in generate_section_content]: {str(e)}")
        return {"error": "Failed to generate fixed content from suggestion."}
