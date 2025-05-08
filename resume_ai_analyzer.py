import os
import docx
import pytesseract
import pdfplumber
from PIL import Image
import io
from pdf2image import convert_from_path
import openai

openai.api_key = os.environ.get("OPENAI_API_KEY")

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
        images = convert_from_path(file_path, dpi=300)
        text = ""
        for img in images:
            text += pytesseract.image_to_string(img)
        return text.strip()
    except Exception:
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
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an ATS resume expert."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content.strip()
    except:
        return "❌ Failed to analyze ATS compatibility."

def analyze_resume_with_openai(file_path, atsfix=False):
    text = ""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        text = extract_text_from_pdf(file_path) or extract_text_with_ocr(file_path)
    elif ext == ".docx":
        text = extract_text_from_docx(file_path)

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
try:
    print("✅ [OpenAI] Sending resume for suggestion generation...")
    response = openai.ChatCompletion.create(
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
    print("❌ [OpenAI ERROR]", str(e))
    return {"error": "Failed to generate suggestions."}

def generate_ai_resume_content(data):
    name = data.get("name", "")
    email = data.get("email", "")
    phone = data.get("phone", "")
    location = data.get("location", "")
    education = data.get("education", "")
    experience = data.get("experience", "")
    skills = data.get("skills", "")
    certifications = data.get("certifications", "")
    languages = data.get("languages", "")
    hobbies = data.get("hobbies", "")
    summary = data.get("summary", "")

    def format_section(title, content):
        if content.strip():
            safe_content = content.replace("\n", "<br>")
            return f"""
            <div class="section">
              <h3 style='font-size:0.95rem; line-height:1.3; color:#222; margin-bottom:4px; border-bottom:1px solid #ccc;'>{title}</h3>
              <div>{safe_content}</div>
            </div>
            """
        return ""

    sections = []

    if name or email or phone or location:
        sections.append(f"""
        <div style="text-align:center; margin-bottom: 1.5rem;">
            <div style="font-size: 1.5rem; font-weight: bold; color: #1D75E5;">{name}</div>
            <div style="font-size: 0.95rem; color: #444;">{email}<br>{phone}<br>{location}</div>
        </div>
        """)

    sections.append(format_section("Summary", summary))
    sections.append(format_section("Education", education))
    sections.append(format_section("Experience", experience))
    sections.append(format_section("Skills", skills))
    sections.append(format_section("Certifications", certifications))
    sections.append(format_section("Languages", languages))
    sections.append(format_section("Hobbies", hobbies))

    resume_html = "\n".join(sections)

    return {
        "success": True,
        "html": resume_html
    }
