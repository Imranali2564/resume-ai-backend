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
        "summary": "",
        "education": "",
        "experience": "",
        "skills": "",
        "certifications": "",
        "languages": "",
        "hobbies": ""
    }

    current_section = None
    for line in text.splitlines():
        line_lower = line.lower().strip()

        # Detect section headings
        if "education" in line_lower:
            current_section = "education"
        elif "experience" in line_lower or "employment" in line_lower:
            current_section = "experience"
        elif "skill" in line_lower:
            current_section = "skills"
        elif "certification" in line_lower or "course" in line_lower:
            current_section = "certifications"
        elif "language" in line_lower:
            current_section = "languages"
        elif "hobby" in line_lower or "interest" in line_lower:
            current_section = "hobbies"
        elif "summary" in line_lower or "objective" in line_lower or "profile" in line_lower:
            current_section = "summary"
        elif len(line.strip()) == 0:
            continue
        elif current_section:
            sections[current_section] += line.strip() + "\n"

    for key in sections:
        sections[key] = sections[key].strip()

    return sections


def detect_section_from_suggestion(suggestion):
    suggestion = suggestion.lower()
    section_keywords = {
    "skills": ["skill", "proficiency", "tools", "technologies", "software", "languages known"],
    "experience": ["experience", "worked", "responsibility", "role", "company", "job title", "employment"],
    "education": ["education", "degree", "university", "college", "academic", "school"],
    "certifications": ["certification", "course", "certified", "training", "diploma"],
    "languages": ["language", "fluent", "spoken", "bilingual", "multilingual"],
    "hobbies": ["hobby", "interest", "extracurricular", "leisure", "passion"],
    "summary": ["summary", "objective", "profile", "career goal", "introduction"]
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

        if detected_section not in sections:
            # Create a new section if it doesn't exist
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
            return {
                "section": detected_section,
                "fixedContent": response.choices[0].message.content.strip()
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
