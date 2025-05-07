import os
import fitz  # PyMuPDF
import docx
import pytesseract
from PIL import Image
import io
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

POPPLER_PATH = r"C:\\Users\\Imran\\Downloads\\poppler-24.08.0\\Library\\bin"

def extract_text_from_pdf(file_path):
    try:
        doc = fitz.open(file_path)
        text = ""
        for page in doc:
            text += page.get_text()
        return text.strip()
    except Exception:
        return ""

def extract_text_from_docx(file_path):
    try:
        doc = docx.Document(file_path)
        return "\n".join([p.text for p in doc.paragraphs]).strip()
    except Exception:
        return ""

def extract_text_with_ocr(file_path):
    try:
        images = fitz.open(file_path)
        text = ""
        for page_num in range(len(images)):
            pix = images[page_num].get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes()))
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
        response = client.chat.completions.create(
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

    prompt = f"""
You are a professional resume coach. Give improvement suggestions in short clear bullet points.
Make suggestions specific, actionable, and impactful.
Don't explain anything else. List one suggestion per line.

Resume:
{text[:4000]}
    """

    if atsfix:
        prompt = f"""
You are an ATS resume expert. Provide 5 to 7 most important and high-impact improvement suggestions that directly affect ATS compatibility and selection.
List only important actionable suggestions in short bullet points. One suggestion per line. No intro or outro.

Resume:
{text[:4000]}
        """

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a professional resume suggestion assistant."},
                {"role": "user", "content": prompt}
            ]
        )
        suggestions = response.choices[0].message.content.strip()
        return {"suggestions": suggestions}
    except:
        return {"error": "Failed to generate suggestions."}
    def generate_ai_resume_content(name, email, phone, location, summary, education, experience, certifications, skills, languages, hobbies):
    prompt = f"""
You are a professional resume builder AI. Using the following input data, generate a clean, professional and impactful resume content in simple HTML format, with heading sections and clear layout.

Name: {name}
Email: {email}
Phone: {phone}
Location: {location}
Summary: {summary}
Education: {education}
Experience: {experience}
Certifications: {certifications}
Skills: {skills}
Languages: {languages}
Hobbies: {hobbies}

Rules:
- Start with name and contact at the top.
- Each section must have a heading like <h3>Education</h3> and content below.
- Use <ul> and <li> where suitable.
- Make the tone professional and positive.

Respond only with HTML.
"""
    import openai
    import os
    openai.api_key = os.getenv("OPENAI_API_KEY")
    
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()

